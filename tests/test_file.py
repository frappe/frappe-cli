import os

import httpx
import respx
from typer.testing import CliRunner

from frappe_cli.cli import app

BASE = "http://site.test"
runner = CliRunner()


def _env(monkeypatch):
    monkeypatch.setenv("FRAPPE_SITE", BASE)
    monkeypatch.setenv("FRAPPE_API_KEY", "k")
    monkeypatch.setenv("FRAPPE_API_SECRET", "s")


@respx.mock
def test_download_basenames_server_filename(monkeypatch, tmp_path):
    """A crafted File.file_name must not let the download escape the cwd."""
    _env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    respx.get(f"{BASE}/api/v2/document/File/evil/").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "file_url": "/private/files/x.bin",
                    "file_name": "../../../pwned.bin",
                }
            },
        )
    )
    respx.get(f"{BASE}/private/files/x.bin").mock(
        return_value=httpx.Response(200, content=b"payload")
    )

    result = runner.invoke(app, ["file", "download", "evil"])
    assert result.exit_code == 0, result.stderr

    # Written inside cwd under the sanitized basename, nowhere above it.
    assert (tmp_path / "pwned.bin").read_bytes() == b"payload"
    assert not (tmp_path.parent / "pwned.bin").exists()
    assert not os.path.exists(tmp_path / ".." / ".." / ".." / "pwned.bin")
