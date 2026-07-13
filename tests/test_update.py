import json
import subprocess

import pytest
from typer.testing import CliRunner

from frappectl.cli import app
from frappectl.commands import update

runner = CliRunner()


class FakeDist:
    """Minimal stand-in for importlib.metadata.Distribution."""

    def __init__(
        self,
        installer=None,
        location="/opt/venv/site-packages",
        editable=False,
        vcs=False,
    ):
        self._files = {}
        if installer is not None:
            self._files["INSTALLER"] = installer + "\n"
        if editable:
            self._files["direct_url.json"] = json.dumps(
                {"url": "file:///src", "dir_info": {"editable": True}}
            )
        if vcs:
            self._files["direct_url.json"] = json.dumps(
                {
                    "url": "https://github.com/frappe/frappectl",
                    "vcs_info": {"vcs": "git", "requested_revision": "main"},
                }
            )
        self._location = location

    def read_text(self, name):
        return self._files.get(name)

    def locate_file(self, path):
        return self._location


# --- detect_backend -------------------------------------------------------


def test_detects_uv_tool_install(monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda n: "/usr/bin/uv")
    dist = FakeDist(
        installer="uv",
        location="/home/u/.local/share/uv/tools/frappectl/lib/site-packages",
    )
    backend = update.detect_backend(dist)
    assert backend.name == "uv tool"
    assert backend.argv == ["uv", "tool", "upgrade", "frappectl"]


def test_detects_uv_pip_install(monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda n: "/usr/bin/uv")
    dist = FakeDist(installer="uv", location="/opt/venv/lib/site-packages")
    backend = update.detect_backend(dist)
    assert backend.name == "uv pip"
    assert backend.argv == ["uv", "pip", "install", "--upgrade", "frappectl"]


def test_uv_installer_without_uv_binary_falls_through(monkeypatch):
    # INSTALLER says uv but uv isn't on PATH -> no recognised backend.
    monkeypatch.setattr(update.shutil, "which", lambda n: None)
    dist = FakeDist(installer="uv", location="/opt/venv/lib/site-packages")
    assert update.detect_backend(dist) is None


def test_detects_pip_install(monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda n: None)
    dist = FakeDist(installer="pip", location="/opt/venv/lib/site-packages")
    backend = update.detect_backend(dist)
    assert backend.name == "pip"
    assert backend.argv[-3:] == ["install", "--upgrade", "frappectl"]
    assert backend.argv[1:3] == ["-m", "pip"]


def test_detects_pipx_install(monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda n: "/usr/bin/pipx")
    dist = FakeDist(installer="pip", location="/home/u/.local/pipx/venvs/frappectl/lib")
    backend = update.detect_backend(dist)
    assert backend.name == "pipx"
    assert backend.argv == ["pipx", "upgrade", "frappectl"]


def test_git_pip_install_uses_git_source(monkeypatch):
    # `pip install git+https://…`: must re-pull from git, NOT resolve the bare
    # name against PyPI (which would switch the install to the PyPI release).
    monkeypatch.setattr(update.shutil, "which", lambda n: None)
    dist = FakeDist(installer="pip", location="/opt/venv/lib/site-packages", vcs=True)
    backend = update.detect_backend(dist)
    assert backend.name == "pip"
    assert update._GIT_SOURCE in backend.argv
    assert "frappectl" not in backend.argv  # never the bare PyPI name
    assert "--force-reinstall" in backend.argv


def test_git_uv_pip_install_uses_git_source(monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda n: "/usr/bin/uv")
    dist = FakeDist(installer="uv", location="/opt/venv/lib/site-packages", vcs=True)
    backend = update.detect_backend(dist)
    assert backend.name == "uv pip"
    assert update._GIT_SOURCE in backend.argv
    assert backend.argv[-2:] == ["--reinstall-package", "frappectl"]


def test_git_uv_tool_install_upgrades_by_name(monkeypatch):
    # uv tool re-pulls its recorded git source on upgrade-by-name.
    monkeypatch.setattr(update.shutil, "which", lambda n: "/usr/bin/uv")
    dist = FakeDist(
        installer="uv",
        location="/home/u/.local/share/uv/tools/frappectl/lib/site-packages",
        vcs=True,
    )
    backend = update.detect_backend(dist)
    assert backend.argv == ["uv", "tool", "upgrade", "frappectl"]


def test_unknown_installer_returns_none(monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda n: None)
    dist = FakeDist(installer="conda", location="/opt/conda/lib/site-packages")
    assert update.detect_backend(dist) is None


# --- update command -------------------------------------------------------


def test_refuses_editable(monkeypatch):
    monkeypatch.setattr(
        update, "_dist", lambda: FakeDist(installer="uv", editable=True)
    )
    result = runner.invoke(app, ["--yes", "update"])
    assert result.exit_code == 1
    assert "editable" in result.stderr


def test_refuses_when_no_backend(monkeypatch):
    monkeypatch.setattr(update, "_dist", lambda: FakeDist(installer="conda"))
    monkeypatch.setattr(update.shutil, "which", lambda n: None)
    result = runner.invoke(app, ["--yes", "update"])
    assert result.exit_code == 1
    assert "Couldn't detect" in result.stderr


def test_refuses_when_not_installed(monkeypatch):
    monkeypatch.setattr(update, "_dist", lambda: None)
    result = runner.invoke(app, ["--yes", "update"])
    assert result.exit_code == 1
    assert "not installed" in result.stderr


def test_runs_backend_command(monkeypatch):
    monkeypatch.setattr(update, "_dist", lambda: FakeDist(installer="uv"))
    monkeypatch.setattr(update.shutil, "which", lambda n: "/usr/bin/uv")
    calls = []

    def fake_run(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(update.subprocess, "run", fake_run)
    result = runner.invoke(app, ["--yes", "update"])
    assert result.exit_code == 0
    assert calls == [["uv", "pip", "install", "--upgrade", "frappectl"]]


def test_propagates_failure(monkeypatch):
    monkeypatch.setattr(update, "_dist", lambda: FakeDist(installer="pip"))
    monkeypatch.setattr(update.shutil, "which", lambda n: None)
    monkeypatch.setattr(
        update.subprocess, "run", lambda argv: subprocess.CompletedProcess(argv, 3)
    )
    result = runner.invoke(app, ["--yes", "update"])
    assert result.exit_code == 3
    assert "Update failed" in result.stderr


def test_json_output(monkeypatch):
    monkeypatch.setattr(update, "_dist", lambda: FakeDist(installer="uv"))
    monkeypatch.setattr(update.shutil, "which", lambda n: "/usr/bin/uv")
    monkeypatch.setattr(
        update.subprocess, "run", lambda argv: subprocess.CompletedProcess(argv, 0)
    )
    result = runner.invoke(app, ["--yes", "--json", "update"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["backend"] == "uv pip"


def test_cancelled_when_not_confirmed(monkeypatch):
    monkeypatch.setattr(update, "_dist", lambda: FakeDist(installer="uv"))
    monkeypatch.setattr(update.shutil, "which", lambda n: "/usr/bin/uv")
    ran = []
    monkeypatch.setattr(update.subprocess, "run", lambda argv: ran.append(argv))
    # No --yes and non-TTY (CliRunner) -> confirm() refuses to proceed.
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 2
    assert ran == []


# --- passive update notification ------------------------------------------


def _ls_remote_output(*versions):
    return "".join(f"deadbeef\trefs/tags/{v}\n" for v in versions)


def test_parse_version_tolerates_prefix_and_suffix():
    assert update._parse_version("v1.2.3") == (1, 2, 3)
    assert update._parse_version("1.2.3") == (1, 2, 3)
    assert update._parse_version("v1.2.3rc1") == (1, 2, 3)
    assert update._parse_version("0.0.0+unknown") == (0, 0, 0)
    assert update._parse_version("not-a-version") is None


def test_fetch_latest_tag_picks_highest(monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda n: "/usr/bin/git")
    out = _ls_remote_output("v0.8.0", "v1.0.0", "v0.9.1", "not-a-tag")

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    monkeypatch.setattr(update.subprocess, "run", fake_run)
    assert update._fetch_latest_tag() == "1.0.0"


def test_fetch_latest_tag_without_git_is_none(monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda n: None)
    assert update._fetch_latest_tag() is None


def test_fetch_latest_tag_swallows_failure(monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda n: "/usr/bin/git")

    def boom(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 2.0)

    monkeypatch.setattr(update.subprocess, "run", boom)
    assert update._fetch_latest_tag() is None


def test_latest_version_uses_fresh_cache(monkeypatch, tmp_path):
    cache = tmp_path / "update-check.json"
    cache.write_text(json.dumps({"checked_at": 1000.0, "latest": "1.0.0"}))
    monkeypatch.setattr(update, "_cache_file", lambda: cache)
    # Any network call would be a bug: cache is fresh relative to `now`.
    monkeypatch.setattr(
        update, "_fetch_latest_tag", lambda *a, **k: pytest.fail("hit network")
    )
    assert update.latest_version(now=1000.0 + 10) == "1.0.0"


def test_latest_version_refreshes_stale_cache(monkeypatch, tmp_path):
    cache = tmp_path / "update-check.json"
    cache.write_text(json.dumps({"checked_at": 1000.0, "latest": "0.8.0"}))
    monkeypatch.setattr(update, "_cache_file", lambda: cache)
    monkeypatch.setattr(update, "_fetch_latest_tag", lambda *a, **k: "1.0.0")
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
    monkeypatch.setattr(update, "_fetch_latest_tag", lambda *a, **k: None)
    now = 1000.0 + update._CHECK_TTL + 1
    # Offline: keep the last known version but bump checked_at to throttle retries.
    assert update.latest_version(now=now) == "0.8.0"
    assert json.loads(cache.read_text())["checked_at"] == now


def _tty_ctx():
    from frappectl.output import Ctx

    ctx = Ctx(json_mode=False, assume_yes=False)
    ctx.is_tty = True
    ctx.json = False
    return ctx


def test_notify_prints_when_outdated(monkeypatch):
    monkeypatch.setattr(update, "_dist", lambda: FakeDist(installer="uv"))
    monkeypatch.setattr(update, "__version__", "0.8.0")
    monkeypatch.setattr(update, "latest_version", lambda: "1.0.0")
    printed = []
    monkeypatch.setattr(
        update.err_console, "print", lambda msg, **k: printed.append(msg)
    )
    update.notify_if_outdated(_tty_ctx())
    assert printed and "1.0.0" in printed[0]


def test_notify_silent_when_current(monkeypatch):
    monkeypatch.setattr(update, "_dist", lambda: FakeDist(installer="uv"))
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
    monkeypatch.setattr(
        update, "_dist", lambda: FakeDist(installer="uv", editable=True)
    )

    def fail_check():
        pytest.fail("must not check for updates on a dev checkout")

    monkeypatch.setattr(update, "latest_version", fail_check)
    update.notify_if_outdated(_tty_ctx())
