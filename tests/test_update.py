import json
import subprocess

from typer.testing import CliRunner

from frappe_cli.cli import app
from frappe_cli.commands import update

runner = CliRunner()


class FakeDist:
    """Minimal stand-in for importlib.metadata.Distribution."""

    def __init__(
        self,
        installer=None,
        location="/opt/venv/site-packages",
        editable=False,
        vcs_url=None,
        vcs_ref=None,
    ):
        self._files = {}
        if installer is not None:
            self._files["INSTALLER"] = installer + "\n"
        if editable:
            self._files["direct_url.json"] = json.dumps(
                {"url": "file:///src", "dir_info": {"editable": True}}
            )
        if vcs_url:
            info = {"vcs": "git"}
            if vcs_ref:
                info["requested_revision"] = vcs_ref
            self._files["direct_url.json"] = json.dumps(
                {"url": vcs_url, "vcs_info": info}
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
        location="/home/u/.local/share/uv/tools/frappe-cli/lib/site-packages",
    )
    backend = update.detect_backend(dist)
    assert backend.name == "uv tool"
    assert backend.argv == ["uv", "tool", "upgrade", "frappe-cli"]


def test_detects_uv_pip_install(monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda n: "/usr/bin/uv")
    dist = FakeDist(installer="uv", location="/opt/venv/lib/site-packages")
    backend = update.detect_backend(dist)
    assert backend.name == "uv pip"
    assert backend.argv == ["uv", "pip", "install", "--upgrade", "frappe-cli"]


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
    assert backend.argv[-3:] == ["install", "--upgrade", "frappe-cli"]
    assert backend.argv[1:3] == ["-m", "pip"]


def test_detects_pipx_install(monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda n: "/usr/bin/pipx")
    dist = FakeDist(
        installer="pip", location="/home/u/.local/pipx/venvs/frappe-cli/lib"
    )
    backend = update.detect_backend(dist)
    assert backend.name == "pipx"
    assert backend.argv == ["pipx", "upgrade", "frappe-cli"]


def test_git_pip_install_uses_git_source(monkeypatch):
    # `pip install git+https://…`: must re-pull from git, NOT resolve the bare
    # name against PyPI (an unrelated `frappe-cli` lives there).
    monkeypatch.setattr(update.shutil, "which", lambda n: None)
    dist = FakeDist(
        installer="pip",
        location="/opt/venv/lib/site-packages",
        vcs_url="https://github.com/frappe/frappe-cli",
        vcs_ref="main",
    )
    backend = update.detect_backend(dist)
    assert backend.name == "pip"
    src = "git+https://github.com/frappe/frappe-cli@main"
    assert src in backend.argv
    assert "frappe-cli" not in backend.argv  # never the bare PyPI name
    assert "--force-reinstall" in backend.argv


def test_git_uv_pip_install_uses_git_source(monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda n: "/usr/bin/uv")
    dist = FakeDist(
        installer="uv",
        location="/opt/venv/lib/site-packages",
        vcs_url="https://github.com/frappe/frappe-cli",
    )
    backend = update.detect_backend(dist)
    assert backend.name == "uv pip"
    # No ref recorded -> spec without @ref.
    assert "git+https://github.com/frappe/frappe-cli" in backend.argv
    assert backend.argv[-2:] == ["--reinstall-package", "frappe-cli"]


def test_git_uv_tool_install_upgrades_by_name(monkeypatch):
    # uv tool re-pulls its recorded git source on upgrade-by-name.
    monkeypatch.setattr(update.shutil, "which", lambda n: "/usr/bin/uv")
    dist = FakeDist(
        installer="uv",
        location="/home/u/.local/share/uv/tools/frappe-cli/lib/site-packages",
        vcs_url="https://github.com/frappe/frappe-cli",
        vcs_ref="main",
    )
    backend = update.detect_backend(dist)
    assert backend.argv == ["uv", "tool", "upgrade", "frappe-cli"]


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
    assert calls == [["uv", "pip", "install", "--upgrade", "frappe-cli"]]


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
