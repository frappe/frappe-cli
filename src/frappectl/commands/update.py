"""``frappectl update`` — upgrade frappectl in place.

`uv tool install frappectl` is the supported install method, so updating is
simply `uv tool upgrade frappectl`. Editable/dev checkouts are refused (update
those with git); anything else that `uv tool upgrade` can't handle fails with
uv's own error message.
"""

from __future__ import annotations

import importlib.metadata as importlib_metadata
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, cast

import httpx
import typer

from .. import __version__
from ..config import config_dir
from ..output import Ctx, err_console, fail, get_ctx, print_json

_DIST = "frappectl"
# PyPI's public JSON API for the package — the source of truth for what
# `frappectl update` can actually install.
_PYPI_JSON_URL = f"https://pypi.org/pypi/{_DIST}/json"

# Passive update check: we ask PyPI for the latest release at most once per
# this window, cache the answer, and nudge interactive users when they lag
# behind. Kept deliberately long so the check never becomes a hot path.
_CHECK_TTL = 24 * 60 * 60  # one day
_CHECK_CACHE = "update-check.json"

_UPGRADE_ARGV = ["uv", "tool", "upgrade", _DIST]


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


def update(ctx: typer.Context) -> None:
    """Update frappectl in place with `uv tool upgrade`.

    Refuses (without changing anything) for editable/dev checkouts — update
    those with git — and when uv isn't available.
    """
    c = get_ctx(ctx)

    dist = _dist()
    if dist is not None and _is_editable(dist):
        raise fail(
            "This is an editable/development install; update it with git "
            "(e.g. `git pull`) instead of `frappectl update`.",
            1,
        )

    if shutil.which("uv") is None:
        raise fail(
            "`frappectl update` needs uv (https://docs.astral.sh/uv/). "
            "Install uv, or upgrade frappectl with your own package manager.",
            1,
        )

    cmd = " ".join(_UPGRADE_ARGV)
    err_console.print(f"[dim]frappectl {__version__} — updating: {cmd}[/dim]")

    proc = subprocess.run(_UPGRADE_ARGV)
    if proc.returncode != 0:
        raise fail(f"Update failed: `{cmd}` exited {proc.returncode}.", proc.returncode)

    if c.json:
        print_json(
            {
                "command": _UPGRADE_ARGV,
                "previous_version": __version__,
                "ok": True,
            }
        )
    else:
        err_console.print(
            "[green]done.[/green] Re-run `frappectl --version` to confirm the "
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


def _fetch_latest_release(timeout: float = 2.0) -> str | None:
    """Return the newest released version on PyPI, or None.

    One GET against PyPI's JSON API — ``info.version`` is the latest non-yanked
    release, i.e. exactly what a bare-name upgrade would install. Every failure
    (network down, timeout, unexpected payload) collapses to None so the caller
    can treat "couldn't check" and "no newer version" the same way.
    """
    try:
        resp = httpx.get(_PYPI_JSON_URL, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        version = resp.json()["info"]["version"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
    return version if isinstance(version, str) else None


def latest_version(now: float | None = None) -> str | None:
    """Newest available version string, backed by a day-long cache.

    The network is touched at most once per ``_CHECK_TTL``. A failed check still
    records the attempt time, so a machine that's offline won't re-probe PyPI on
    every single command for the rest of the day — it keeps serving the last
    known-good answer (if any) until the window elapses.
    """
    now = time.time() if now is None else now
    cache = _load_cache()
    if now - cache.get("checked_at", 0) < _CHECK_TTL:
        return cache.get("latest")

    latest = _fetch_latest_release()
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
        f"[yellow]A new version of frappectl is available "
        f"({__version__} → {latest}).[/yellow] Run [bold]frappectl update[/bold] "
        "to upgrade."
    )
