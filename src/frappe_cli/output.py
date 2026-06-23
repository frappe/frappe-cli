"""Output + interaction contract.

TTY  -> rich tables, colours, confirmation prompts.
Piped / ``--json`` -> clean JSON on stdout, no prompts.

Exit codes: 0 success, 1 failure, 2 usage error. Detail lives in the message,
not the code.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

# stderr console: progress, errors, prompts. Never pollutes stdout JSON.
err_console = Console(stderr=True)
_out_console = Console()


class Ctx:
    """Holds run-wide output state, stashed on the Typer context object."""

    def __init__(self, json_mode: bool, assume_yes: bool, profile: str | None = None):
        self.assume_yes = assume_yes
        self.profile = profile
        # --json forces JSON; otherwise JSON whenever stdout is not a TTY.
        self.json = json_mode or not sys.stdout.isatty()
        self.is_tty = sys.stdout.isatty()


def get_ctx(ctx: typer.Context) -> Ctx:
    obj = ctx.obj
    assert isinstance(obj, Ctx)
    return obj


def print_json(data: Any) -> None:
    sys.stdout.write(json.dumps(data, indent=2, default=str, ensure_ascii=False))
    sys.stdout.write("\n")


def fail(message: str, code: int = 1) -> "typer.Exit":
    """Print an error to stderr and return an Exit to raise.

    When the message matches a known failure shape, append a one-line tip
    pointing at the command that can resolve it. Hints go to stderr, so they
    never pollute JSON on stdout.
    """
    from .errors import error_hint

    err_console.print(f"[red]error:[/red] {message}")
    hint = error_hint(message)
    if hint:
        err_console.print(f"[dim]tip:[/dim] {hint}")
    return typer.Exit(code)


def confirm(ctx: Ctx, prompt: str) -> bool:
    """Confirm a destructive action.

    On a TTY: interactive y/N. Non-interactive: require ``--yes`` up front.
    """
    if ctx.assume_yes:
        return True
    if not ctx.is_tty:
        raise fail(
            f"{prompt} Refusing without confirmation; pass --yes to proceed "
            "non-interactively.",
            2,
        )
    return typer.confirm(prompt)


# --- table rendering -------------------------------------------------------


def _scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "✓" if value else ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return str(value)


def render_rows(rows: list[dict], columns: list[str], title: str | None = None) -> None:
    """Render a list of dicts as a rich table on stdout."""
    table = Table(title=title, header_style="bold cyan", show_lines=False)
    for col in columns:
        table.add_column(col, overflow="fold")
    for row in rows:
        table.add_row(*[_scalar(row.get(col)) for col in columns])
    _out_console.print(table)
    if not rows:
        err_console.print("[dim]No records.[/dim]")


def render_record(record: dict, title: str | None = None) -> None:
    """Render a single document as a two-column key/value table."""
    table = Table(title=title, header_style="bold cyan", show_header=False, box=None)
    table.add_column("field", style="bold")
    table.add_column("value", overflow="fold")
    for key, value in record.items():
        table.add_row(key, _scalar(value))
    _out_console.print(table)


def emit_list(
    ctx: Ctx, rows: list[dict], columns: list[str] | None, title: str | None = None
) -> None:
    if ctx.json:
        print_json(rows)
        return
    if columns is None:
        columns = list(rows[0].keys()) if rows else []
    render_rows(rows, columns, title=title)


def emit_record(ctx: Ctx, record: Any, title: str | None = None) -> None:
    if ctx.json:
        print_json(record)
        return
    if isinstance(record, dict):
        render_record(record, title=title)
    else:
        _out_console.print(_scalar(record))
