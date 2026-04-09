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


async def _stream_part(client: httpx.AsyncClient, url: str, dest: Path) -> Path:
    async with client.stream("GET", url) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                f.write(chunk)
    return dest


def _extract_sqlite(zip_path: Path, dest_dir: Path) -> Path | None:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                if name.endswith(".sqlite3") or name.endswith(".sqlite"):
                    out_path = dest_dir / Path(name).name
                    with zf.open(name) as src, open(out_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    return out_path
    except zipfile.BadZipFile as exc:
        _logger.error("jlcparts zip extraction failed — split-zip format may need 7z",
                      extra={"error": str(exc)})
    return None
