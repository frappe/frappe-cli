import json
import subprocess

import httpx
import pytest
from typer.testing import CliRunner

from frappectl.cli import app
from frappectl.commands import update

runner = CliRunner()

_UPGRADE = ["uv", "tool", "upgrade", "frappectl"]


class FakeDist:
    """Minimal stand-in for importlib.metadata.Distribution."""

    def __init__(self, editable=False):
        self._files = {}
        if editable:
            self._files["direct_url.json"] = json.dumps(
                {"url": "file:///src", "dir_info": {"editable": True}}
            )

    def read_text(self, name):
        return self._files.get(name)


# --- update command -------------------------------------------------------


def test_refuses_editable(monkeypatch):
    monkeypatch.setattr(update, "_dist", lambda: FakeDist(editable=True))
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 1
    assert "editable" in result.stderr


def test_refuses_without_uv(monkeypatch):
    monkeypatch.setattr(update, "_dist", lambda: FakeDist())
    monkeypatch.setattr(update.shutil, "which", lambda n: None)
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 1
    assert "uv" in result.stderr


def test_runs_uv_tool_upgrade(monkeypatch):
    monkeypatch.setattr(update, "_dist", lambda: FakeDist())
    monkeypatch.setattr(update.shutil, "which", lambda n: "/usr/bin/uv")
    calls = []

    def fake_run(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(update.subprocess, "run", fake_run)
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert calls == [_UPGRADE]


def test_propagates_failure(monkeypatch):
    monkeypatch.setattr(update, "_dist", lambda: FakeDist())
    monkeypatch.setattr(update.shutil, "which", lambda n: "/usr/bin/uv")
    monkeypatch.setattr(
        update.subprocess, "run", lambda argv: subprocess.CompletedProcess(argv, 3)
    )
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 3
    assert "Update failed" in result.stderr


def test_json_output(monkeypatch):
    monkeypatch.setattr(update, "_dist", lambda: FakeDist())
    monkeypatch.setattr(update.shutil, "which", lambda n: "/usr/bin/uv")
    monkeypatch.setattr(
        update.subprocess, "run", lambda argv: subprocess.CompletedProcess(argv, 0)
    )
    result = runner.invoke(app, ["--json", "update"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["command"] == _UPGRADE


# --- passive update notification ------------------------------------------


def test_parse_version_tolerates_prefix_and_suffix():
    assert update._parse_version("v1.2.3") == (1, 2, 3)
    assert update._parse_version("1.2.3") == (1, 2, 3)
    assert update._parse_version("v1.2.3rc1") == (1, 2, 3)
    assert update._parse_version("0.0.0+unknown") == (0, 0, 0)
    assert update._parse_version("not-a-version") is None


def _pypi_response(payload, status=200):
    request = httpx.Request("GET", update._PYPI_JSON_URL)
    return httpx.Response(status, json=payload, request=request)


def test_fetch_latest_release_reads_pypi(monkeypatch):
    def fake_get(url, **kwargs):
        assert url == update._PYPI_JSON_URL
        return _pypi_response({"info": {"version": "1.0.0"}})

    monkeypatch.setattr(update.httpx, "get", fake_get)
    assert update._fetch_latest_release() == "1.0.0"


def test_fetch_latest_release_swallows_network_failure(monkeypatch):
    def boom(url, **kwargs):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(update.httpx, "get", boom)
    assert update._fetch_latest_release() is None


def test_fetch_latest_release_swallows_http_error(monkeypatch):
    monkeypatch.setattr(
        update.httpx, "get", lambda url, **k: _pypi_response({}, status=503)
    )
    assert update._fetch_latest_release() is None


def test_fetch_latest_release_swallows_bad_payload(monkeypatch):
    monkeypatch.setattr(
        update.httpx, "get", lambda url, **k: _pypi_response({"info": {}})
    )
    assert update._fetch_latest_release() is None


def test_latest_version_uses_fresh_cache(monkeypatch, tmp_path):
    cache = tmp_path / "update-check.json"
    cache.write_text(json.dumps({"checked_at": 1000.0, "latest": "1.0.0"}))
    monkeypatch.setattr(update, "_cache_file", lambda: cache)
    # Any network call would be a bug: cache is fresh relative to `now`.
    monkeypatch.setattr(
        update, "_fetch_latest_release", lambda *a, **k: pytest.fail("hit network")
    )
    assert update.latest_version(now=1000.0 + 10) == "1.0.0"


def test_latest_version_refreshes_stale_cache(monkeypatch, tmp_path):
    cache = tmp_path / "update-check.json"
    cache.write_text(json.dumps({"checked_at": 1000.0, "latest": "0.8.0"}))
    monkeypatch.setattr(update, "_cache_file", lambda: cache)
    monkeypatch.setattr(update, "_fetch_latest_release", lambda *a, **k: "1.0.0")
    now = 1000.0 + update._CHECK_TTL + 1
    assert update.latest_version(now=now) == "1.0.0"
    # New answer and attempt time are persisted.
    saved = json.loads(cache.read_text())
    assert saved["latest"] == "1.0.0"
    assert saved["checked_at"] == now


def test_latest_version_failed_refresh_keeps_last_known(monkeypatch, tmp_path):
    cache = tmp_path / "update-check.json"
    cache.write_text(json.dumps({"checked_at": 1000.0, "latest": "0.8.0"}))
    monkeypatch.setattr(update, "_cache_file", lambda: cache)
    monkeypatch.setattr(update, "_fetch_latest_release", lambda *a, **k: None)
    now = 1000.0 + update._CHECK_TTL + 1
    # Offline: keep the last known version but bump checked_at to throttle retries.
    assert update.latest_version(now=now) == "0.8.0"
    assert json.loads(cache.read_text())["checked_at"] == now


def _tty_ctx():
    from frappectl.output import Ctx

    ctx = Ctx(json_mode=False)
    ctx.is_tty = True
    ctx.json = False
    return ctx


def test_notify_prints_when_outdated(monkeypatch):
    monkeypatch.setattr(update, "_dist", lambda: FakeDist())
    monkeypatch.setattr(update, "__version__", "0.8.0")
    monkeypatch.setattr(update, "latest_version", lambda: "1.0.0")
    printed = []
    monkeypatch.setattr(
        update.err_console, "print", lambda msg, **k: printed.append(msg)
    )
    update.notify_if_outdated(_tty_ctx())
    assert printed and "1.0.0" in printed[0]


def test_notify_silent_when_current(monkeypatch):
    monkeypatch.setattr(update, "_dist", lambda: FakeDist())
    monkeypatch.setattr(update, "__version__", "1.0.0")
    monkeypatch.setattr(update, "latest_version", lambda: "1.0.0")
    printed = []
    monkeypatch.setattr(
        update.err_console, "print", lambda msg, **k: printed.append(msg)
    )
    update.notify_if_outdated(_tty_ctx())
    assert printed == []


def test_notify_silent_in_json_mode(monkeypatch):
    def fail_check():
        pytest.fail("must not check for updates in JSON mode")

    monkeypatch.setattr(update, "latest_version", fail_check)
    ctx = _tty_ctx()
    ctx.json = True
    update.notify_if_outdated(ctx)  # returns before touching the network


def test_notify_silent_for_editable(monkeypatch):
    monkeypatch.setattr(update, "_dist", lambda: FakeDist(editable=True))

    def fail_check():
        pytest.fail("must not check for updates on a dev checkout")

    monkeypatch.setattr(update, "latest_version", fail_check)
    update.notify_if_outdated(_tty_ctx())
