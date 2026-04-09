"""
Automatic download of the jlcparts parts database.

The jlcparts project (https://github.com/yaqwsx/jlcparts, MIT license, Jan Mrázek)
publishes a SQLite snapshot of the JLCPCB SMT assembly component catalog as a
split zip archive on GitHub Pages. This module fetches all parts, concatenates
them, and extracts the sqlite3 file to the configured path.

The database is ~1 GB compressed / ~2 GB on disk. Download runs once at startup
if the file is missing; the server operates normally (skipping jlcparts lookups)
until it completes.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

import httpx

import log

_logger = log.get_logger("parts_bin.jlcparts_download")
_BASE_URL = "https://yaqwsx.github.io/jlcparts/data"


async def download_if_missing(db_path: str | Path) -> None:
    """
    Download the jlcparts database to db_path if it does not already exist.
    Logs progress and errors; never raises.
    """
    db_path = Path(db_path)
    if db_path.exists():
        return

    _logger.info("jlcparts database missing, starting background download",
                 extra={"db_path": str(db_path)})

    try:
        await _download(db_path)
    except Exception as exc:
        _logger.error("jlcparts download failed",
                      extra={"error": str(exc), "error_type": type(exc).__name__})


async def _download(db_path: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
        part_paths: list[Path] = []

        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            # Main part.
            part_paths.append(
                await _stream_part(client, f"{_BASE_URL}/cache.zip", tmp_dir / "cache.zip")
            )
            _logger.info("jlcparts downloaded part", extra={"part": "cache.zip", "n": 1})

            # Numbered parts — stop on first 404.
            for i in range(1, 200):
                part_name = f"cache.z{i:02d}"
                url = f"{_BASE_URL}/{part_name}"
                probe = await client.head(url)
                if probe.status_code == 404:
                    break
                part_paths.append(await _stream_part(client, url, tmp_dir / part_name))
                _logger.info("jlcparts downloaded part",
                             extra={"part": part_name, "n": len(part_paths)})

        _logger.info("jlcparts concatenating and extracting",
                     extra={"total_parts": len(part_paths)})

        # Concatenate all parts into one file that zipfile can read.
        concat_path = tmp_dir / "cache_full.zip"
        with open(concat_path, "wb") as out:
            for part in part_paths:
                with open(part, "rb") as src:
                    shutil.copyfileobj(src, out)

        sqlite_path = _extract_sqlite(concat_path, tmp_dir)
        if sqlite_path is None:
            raise RuntimeError("no .sqlite3 file found in jlcparts archive")

        db_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(sqlite_path), db_path)
        _logger.info("jlcparts database ready", extra={"db_path": str(db_path)})


_MAX_PART_BYTES = 512 * 1024 * 1024  # 512 MB per part — well above current ~50 MB


async def _stream_part(client: httpx.AsyncClient, url: str, dest: Path) -> Path:
    async with client.stream("GET", url) as resp:
        resp.raise_for_status()
        written = 0
        with open(dest, "wb") as f:
            async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                written += len(chunk)
                if written > _MAX_PART_BYTES:
                    raise RuntimeError(
                        f"jlcparts part exceeded {_MAX_PART_BYTES} bytes — aborting download"
                    )
                f.write(chunk)
    return dest


_SQLITE_MAGIC = b"SQLite format 3\x00"
_MAX_EXTRACT_BYTES = 8 * 1024 * 1024 * 1024  # 8 GB hard cap against zip bombs


def _extract_sqlite(zip_path: Path, dest_dir: Path) -> Path | None:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                name = info.filename

                # Skip anything that is not a plain sqlite3/sqlite file.
                if not (name.endswith(".sqlite3") or name.endswith(".sqlite")):
                    continue

                # Reject symlinks (external_attr encodes Unix mode in the high 16 bits).
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                if unix_mode and (unix_mode & 0xA000) == 0xA000:
                    _logger.warning("jlcparts skipping symlink entry", extra={"name": name})
                    continue

                # Reject path traversal — only keep the bare filename.
                safe_name = Path(name).name
                if not safe_name or safe_name.startswith("."):
                    _logger.warning("jlcparts skipping suspicious entry", extra={"name": name})
                    continue

                # Zip bomb guard: uncompressed size must be within cap.
                if info.file_size > _MAX_EXTRACT_BYTES:
                    _logger.error("jlcparts entry exceeds size cap, aborting",
                                  extra={"name": name, "size": info.file_size})
                    return None

                out_path = dest_dir / safe_name
                written = 0
                with zf.open(info) as src, open(out_path, "wb") as dst:
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        written += len(chunk)
                        if written > _MAX_EXTRACT_BYTES:
                            _logger.error("jlcparts extraction exceeded size cap, aborting")
                            dst.close()
                            out_path.unlink(missing_ok=True)
                            return None
                        dst.write(chunk)

                # Verify SQLite magic bytes.
                with open(out_path, "rb") as f:
                    magic = f.read(len(_SQLITE_MAGIC))
                if magic != _SQLITE_MAGIC:
                    _logger.error("jlcparts extracted file is not a SQLite database",
                                  extra={"name": name})
                    out_path.unlink(missing_ok=True)
                    return None

                return out_path

    except zipfile.BadZipFile as exc:
        _logger.error("jlcparts zip extraction failed — split-zip format may need 7z",
                      extra={"error": str(exc)})
    return None
