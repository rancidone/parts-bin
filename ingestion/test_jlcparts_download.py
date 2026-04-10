"""Tests for jlcparts downloading and extraction via system tools."""

from pathlib import Path
from unittest.mock import patch

from ingestion.jlcparts_download import _SQLITE_MAGIC, _extract_sqlite
from ingestion import jlcparts_download


class _CompletedProcess:
    def __init__(self, stdout: str = ""):
        self.stdout = stdout


def test_extract_sqlite_uses_system_tools_to_extract_sqlite_file(tmp_path):
    archive = tmp_path / "cache_full.zip"
    archive.write_bytes(b"not-a-real-zip")

    def fake_run(args, capture_output=False, text=False, check=False, stdout=None, stderr=None):
        if args[:2] == ["/usr/bin/bsdtar", "-tf"]:
            return _CompletedProcess(stdout="nested/cache.sqlite3\nREADME.txt\n")
        if args[:2] == ["/usr/bin/bsdtar", "-xOf"]:
            assert stdout is not None
            stdout.write(_SQLITE_MAGIC + b"payload")
            return _CompletedProcess()
        raise AssertionError(f"unexpected command: {args}")

    with patch("ingestion.jlcparts_download.shutil.which", side_effect=lambda cmd: f"/usr/bin/{cmd}" if cmd == "bsdtar" else None):
        with patch("ingestion.jlcparts_download.subprocess.run", side_effect=fake_run):
            extracted = _extract_sqlite(archive, tmp_path)

    assert extracted == Path(tmp_path / "cache.sqlite3")
    assert extracted.read_bytes().startswith(_SQLITE_MAGIC)


def test_extract_sqlite_falls_back_to_unzip_when_bsdtar_missing(tmp_path):
    archive = tmp_path / "cache_full.zip"
    archive.write_bytes(b"not-a-real-zip")

    def fake_run(args, capture_output=False, text=False, check=False, stdout=None, stderr=None):
        if args[:2] == ["/usr/bin/zipinfo", "-1"]:
            return _CompletedProcess(stdout="nested/cache.sqlite3\n")
        if args[:2] == ["/usr/bin/unzip", "-p"]:
            assert stdout is not None
            stdout.write(_SQLITE_MAGIC + b"payload")
            return _CompletedProcess()
        raise AssertionError(f"unexpected command: {args}")

    def fake_which(cmd: str):
        if cmd == "bsdtar":
            return None
        if cmd in {"zipinfo", "unzip"}:
            return f"/usr/bin/{cmd}"
        return None

    with patch("ingestion.jlcparts_download.shutil.which", side_effect=fake_which):
        with patch("ingestion.jlcparts_download.subprocess.run", side_effect=fake_run):
            extracted = _extract_sqlite(archive, tmp_path)

    assert extracted == Path(tmp_path / "cache.sqlite3")
    assert extracted.read_bytes().startswith(_SQLITE_MAGIC)


async def test_download_concatenates_split_zip_parts_in_correct_order(tmp_path):
    db_path = tmp_path / "jlcparts.sqlite3"
    downloaded: dict[str, bytes] = {
        "cache.zip": b"ZIP",
        "cache.z01": b"Z01",
        "cache.z02": b"Z02",
    }

    async def fake_stream_part(client, url: str, dest: Path) -> Path:
        name = Path(url).name
        dest.write_bytes(downloaded[name])
        return dest

    class _Resp:
        def __init__(self, status_code: int):
            self.status_code = status_code

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def head(self, url: str):
            name = Path(url).name
            if name in {"cache.z01", "cache.z02"}:
                return _Resp(200)
            return _Resp(404)

    def fake_async_client(*args, **kwargs):
        return _Client()

    def fake_extract(zip_path: Path, dest_dir: Path) -> Path:
        assert zip_path.read_bytes() == b"Z01Z02ZIP"
        out = dest_dir / "cache.sqlite3"
        out.write_bytes(_SQLITE_MAGIC + b"db")
        return out

    with patch("ingestion.jlcparts_download.httpx.AsyncClient", side_effect=fake_async_client):
        with patch("ingestion.jlcparts_download._stream_part", side_effect=fake_stream_part):
            with patch("ingestion.jlcparts_download._extract_sqlite", side_effect=fake_extract):
                await jlcparts_download._download(db_path)

    assert db_path.read_bytes().startswith(_SQLITE_MAGIC)
