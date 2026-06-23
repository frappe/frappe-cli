"""frappe-cli — top-level Typer app and global options."""

from __future__ import annotations

import sys
from typing import Optional

import typer

from . import __version__
from .commands import api as api_cmd
from .commands import auth, doc, doctype, file, report
from .commands import guide as guide_cmd
from .config import ConfigError
from .errors import FrappeError, UsageError
from .output import Ctx, fail

app = typer.Typer(
    name="frappe-cli",
    help="frappe-cli — a command-line client for Frappe sites.",
    no_args_is_help=True,
    add_completion=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)

app.add_typer(auth.app, name="auth")
app.add_typer(doc.app, name="doc")
app.add_typer(doctype.app, name="doctype")
app.add_typer(file.app, name="file")
app.add_typer(report.app, name="report")
# `frappe-cli api <path>` — single command, mounted directly.
app.command(name="api", help=api_cmd.api.__doc__)(api_cmd.api)
# `frappe-cli guide` — static usage primer; the first thing an agent should run.
app.command(name="guide", help=guide_cmd.guide.__doc__)(guide_cmd.guide)


def _version_callback(value: bool):
    if value:
        typer.echo(f"frappe-cli {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    ctx: typer.Context,
    site: Optional[str] = typer.Option(
        None, "-s", "--site", help="Profile to use (overrides the default profile)."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Force JSON output (default when piped)."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Assume yes for confirmation prompts."
    ),
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version."
    ),
):
    """Global options apply to every subcommand."""
    ctx.obj = Ctx(json_mode=json_out, assume_yes=yes, profile=site)


# Global flags that must reach the top-level callback. We hoist them to the
# front of argv so they work in gh-style trailing position too, e.g.
#   frappe-cli doc list "Sales Invoice" --json
# is rewritten to `frappe-cli --json doc list "Sales Invoice"`.
_VALUELESS_GLOBALS = {"--json", "--yes", "-y"}
_VALUED_GLOBALS = {"-s", "--site"}


def _hoist_globals(argv: list[str]) -> list[str]:
    hoisted: list[str] = []
    rest: list[str] = []
    i = 0
    seen_ddash = False
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            seen_ddash = True
            rest.append(tok)
            i += 1
            continue
        if not seen_ddash and tok in _VALUELESS_GLOBALS:
            # Normalize -y to --yes for the callback.
            hoisted.append("--yes" if tok == "-y" else tok)
            i += 1
            continue
        if not seen_ddash and tok in _VALUED_GLOBALS:
            hoisted.append("--site")
            if i + 1 < len(argv):
                hoisted.append(argv[i + 1])
                i += 2
            else:
                i += 1
            continue
        if not seen_ddash and (tok.startswith("--site=") or tok.startswith("-s=")):
            hoisted.extend(["--site", tok.split("=", 1)[1]])
            i += 1
            continue
        rest.append(tok)
        i += 1
    return hoisted + rest


def main() -> None:
    sys.argv = [sys.argv[0]] + _hoist_globals(sys.argv[1:])
    try:
        app()
    except UsageError as e:
        fail(e.message, 2)
        sys.exit(2)
    except FrappeError as e:
        fail(e.message, 1)
        sys.exit(1)
    except ConfigError as e:
        fail(str(e), 2)
        sys.exit(2)


if __name__ == "__main__":
    main()
