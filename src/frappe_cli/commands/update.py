"""``frappe-cli update`` — upgrade frappe-cli in place via its own installer.

The CLI only knows how to drive one of two package backends: uv and pip. We
work out how *this* install was created (from the ``INSTALLER`` marker and the
install location), pick the matching upgrade command, and run it. If we can't
recognise a uv/pip backend — or the install is an editable/dev checkout — we
refuse rather than guess, because running the wrong upgrade command could break
the user's environment or silently no-op.
"""

from __future__ import annotations

import importlib.metadata as importlib_metadata
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import typer

from .. import __version__
from ..config import config_dir
from ..output import Ctx, confirm, err_console, fail, get_ctx, print_json

_DIST = "frappe-cli"
# Canonical source for git installs. Hardcoded rather than read back from
# direct_url.json: the repo never moves, and the bare name resolves to an
# unrelated package on PyPI.
_GIT_SOURCE = "git+https://github.com/frappe/frappe-cli"
# The same repo as a plain git URL (git ls-remote doesn't understand pip's
# `git+` scheme prefix).
_REPO_URL = _GIT_SOURCE.removeprefix("git+")

# Passive update check: we read the repo's git tags at most once per this
# window, cache the newest version, and nudge interactive users when they lag
# behind. Kept deliberately long so the check never becomes a hot path.
_CHECK_TTL = 24 * 60 * 60  # one day
_CHECK_CACHE = "update-check.json"


@dataclass
class Backend:
    """A recognised way to upgrade this install: a label and the command."""

    name: str  # human-facing backend name, e.g. "uv tool", "pip"
    argv: list[str]  # the upgrade command to run


def _dist() -> importlib_metadata.Distribution | None:
    try:
        return importlib_metadata.distribution(_DIST)
    except importlib_metadata.PackageNotFoundError:
        return None


def _is_editable(dist: importlib_metadata.Distribution) -> bool:
    """True for `pip install -e .` / `uv pip install -e .` style dev checkouts."""
    text = dist.read_text("direct_url.json")
    if not text:
        return False
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    return bool(data.get("dir_info", {}).get("editable"))


def _installer(dist: importlib_metadata.Distribution) -> str:
    """The tool that recorded the install (`pip`, `uv`, …), lower-cased."""
    text = dist.read_text("INSTALLER")
    return text.strip().lower() if text else ""


def _is_vcs_install(dist: importlib_metadata.Distribution) -> bool:
    """True if this was installed from a VCS (git) URL rather than a registry.

    A git install is *not* on PyPI under this name (there is an unrelated
    `frappe-cli` there), so upgrading it by bare name would pull the wrong
    project — we upgrade from `_GIT_SOURCE` instead. PEP 610's direct_url.json
    carries a `vcs_info` block for exactly these installs.
    """
    text = dist.read_text("direct_url.json")
    if not text:
        return False
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    return "vcs_info" in data


def detect_backend(dist: importlib_metadata.Distribution) -> Backend | None:
    """Map this install to a uv/pip upgrade command, or None if unrecognised.

    Detection order matters: a `uv tool` install lives in a dedicated tools dir
    and must be upgraded with `uv tool upgrade`, not `uv pip`. Everything else
    keys off the recorded installer, falling back through pipx (a pip family
    member with its own upgrade verb) to plain pip.

    For git installs we hand pip/uv-pip the recorded ``git+…`` source instead of
    the bare name (which would resolve to an unrelated PyPI package). `uv tool`
    and `pipx` re-pull their recorded source on an upgrade-by-name, so they take
    the name regardless.
    """
    installer = _installer(dist)
    vcs = _is_vcs_install(dist)
    # For pip-family installs, the thing to (re)install: the git source if this
    # was a VCS install, else the package name from the registry.
    target = _GIT_SOURCE if vcs else _DIST
    # Wrap the location in separators so `_has(location, "uv")` matches the
    # path *component* `uv`, not a substring of e.g. `myuvproject`.
    location = f"{os.sep}{dist.locate_file('')}{os.sep}"
    have_uv = shutil.which("uv") is not None

    def _has(component: str) -> bool:
        return f"{os.sep}{component}{os.sep}" in location

    # `uv tool install` — isolated app in uv's tools dir.
    if have_uv and _has("uv") and _has("tools"):
        return Backend("uv tool", ["uv", "tool", "upgrade", _DIST])

    # `uv pip install` into a regular environment.
    if installer == "uv" and have_uv:
        argv = ["uv", "pip", "install", "--upgrade", target]
        # A git ref (e.g. a branch) can move without the version changing; force
        # a fresh pull so `update` isn't a silent no-op on the same tag/commit.
        if vcs:
            argv.append("--reinstall-package")
            argv.append(_DIST)
        return Backend("uv pip", argv)

    if installer == "pip":
        # pipx installs record `pip` as the installer but live under pipx's
        # venvs dir and want `pipx upgrade`.
        if _has("pipx") and shutil.which("pipx"):
            return Backend("pipx", ["pipx", "upgrade", _DIST])
        argv = [sys.executable, "-m", "pip", "install", "--upgrade", target]
        # Same as uv above: pip treats a same-version git ref as satisfied, so
        # force a reinstall to actually re-pull the branch/tag.
        if vcs:
            argv.append("--force-reinstall")
        return Backend("pip", argv)

    return None


def update(ctx: typer.Context) -> None:
    """Update frappe-cli in place using the installer it was set up with.

    Detects whether this install came from uv or pip and runs the matching
    upgrade command. Refuses (without changing anything) for editable/dev
    checkouts or when no uv/pip backend can be detected — update those by hand.
    """
    c = get_ctx(ctx)

    dist = _dist()
    if dist is None:
        raise fail(f"'{_DIST}' is not installed as a package; nothing to update.", 1)

    if _is_editable(dist):
        raise fail(
            "This is an editable/development install; update it with git "
            "(e.g. `git pull`) instead of `frappe-cli update`.",
            1,
        )

    backend = detect_backend(dist)
    if backend is None:
        raise fail(
            "Couldn't detect a uv or pip install backend; leaving this install "
            "untouched. Update frappe-cli manually with your package manager.",
            1,
        )

    err_console.print(
        f"[dim]frappe-cli {__version__} — updating via {backend.name}: "
        f"{' '.join(backend.argv)}[/dim]"
    )
    if not confirm(c, f"Run `{' '.join(backend.argv)}` to update frappe-cli?"):
        raise fail("Update cancelled.", 1)

    try:
        proc = subprocess.run(backend.argv)
    except FileNotFoundError:
        raise fail(f"'{backend.argv[0]}' not found on PATH.", 127)

    if proc.returncode != 0:
        raise fail(
            f"Update failed: `{' '.join(backend.argv)}` exited {proc.returncode}.",
            proc.returncode,
        )

    if c.json:
        print_json(
            {
                "backend": backend.name,
                "command": backend.argv,
                "previous_version": __version__,
                "ok": True,
            }
        )
    else:
        err_console.print(
            "[green]done.[/green] Re-run `frappe-cli --version` to confirm the "
            "new version."
        )


# --- passive update notification ------------------------------------------

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _parse_version(text: str) -> tuple[int, int, int] | None:
    """Pull an ``X.Y.Z`` release tuple out of a version/tag string.

    Tolerant by design: accepts a leading ``v`` and ignores any pre-release or
    build suffix (``1.2.3rc1``, ``1.2.3+unknown``). Pre-release *ordering* is
    intentionally not modelled — this drives a soft nudge, not a gate.
    """
    m = _VERSION_RE.search(text)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _cache_file() -> Path:
    return config_dir() / _CHECK_CACHE


def _load_cache() -> dict[str, Any]:
    try:
        return cast("dict[str, Any]", json.loads(_cache_file().read_text()))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(data: dict[str, Any]) -> None:
    try:
        path = _cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    except OSError:
        pass


def _fetch_latest_tag(timeout: float = 2.0) -> str | None:
    """Return the newest ``X.Y.Z`` version tag on the remote, or None.

    Uses ``git ls-remote`` — a single round trip with no clone and no auth — and
    swallows every failure (no git binary, network down, timeout) into None so
    the caller can treat "couldn't check" and "no newer version" the same way.
    """
    if shutil.which("git") is None:
        return None
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--tags", "--refs", _REPO_URL],
            capture_output=True,
            text=True,
            timeout=timeout,
            # Never let git block on a credential/terminal prompt.
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None

    best: tuple[int, int, int] | None = None
    best_str: str | None = None
    for line in proc.stdout.splitlines():
        # Each line is "<sha>\trefs/tags/<tag>"; we only need the tag.
        ref = line.rsplit("/", 1)[-1]
        parsed = _parse_version(ref)
        if parsed is not None and (best is None or parsed > best):
            best = parsed
            best_str = f"{parsed[0]}.{parsed[1]}.{parsed[2]}"
    return best_str


def latest_version(now: float | None = None) -> str | None:
    """Newest available version string, backed by a day-long cache.

    The network is touched at most once per ``_CHECK_TTL``. A failed check still
    records the attempt time, so a machine that's offline won't re-probe git on
    every single command for the rest of the day — it keeps serving the last
    known-good answer (if any) until the window elapses.
    """
    now = time.time() if now is None else now
    cache = _load_cache()
    if now - cache.get("checked_at", 0) < _CHECK_TTL:
        return cache.get("latest")

    latest = _fetch_latest_tag()
    cache["checked_at"] = now
    if latest:
        cache["latest"] = latest
    _save_cache(cache)
    return cache.get("latest")


def notify_if_outdated(ctx: Ctx) -> None:
    """Print a one-line "update available" hint to stderr, best-effort.

    Deliberately unobtrusive and safe to call before every command:
      * only for interactive TTY output — never in ``--json`` or piped mode, so
        it can't corrupt machine-readable stdout;
      * skipped for editable/dev checkouts and non-package runs, which carry no
        meaningful version to compare;
      * throttled to one network check per day and silent on any error.
    """
    if ctx.json or not ctx.is_tty:
        return

    dist = _dist()
    if dist is None or _is_editable(dist):
        return

    current = _parse_version(__version__)
    if current is None:
        return

    try:
        latest = latest_version()
    except Exception:
        # A notification must never break the command the user actually ran.
        return
    if not latest:
        return
    newest = _parse_version(latest)
    if newest is None or newest <= current:
        return

    err_console.print(
        f"[yellow]A new version of frappe-cli is available "
        f"({__version__} → {latest}).[/yellow] Run [bold]frappe-cli update[/bold] "
        "to upgrade."
    )
