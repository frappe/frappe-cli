"""Output + interaction contract.

TTY  -> rich tables and colours.
Piped / ``--json`` -> clean JSON on stdout.

Exit codes: 0 success, 1 failure, 2 usage error. Detail lives in the message,
not the code.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

# stderr console: progress, errors, prompts. Never pollutes stdout JSON.
err_console = Console(stderr=True)
_out_console = Console()


class Output:
    """Own JSON/TTY rendering and the stdout/stderr contract."""

    def __init__(self, json_mode: bool, *, is_tty: bool | None = None):
        self.is_tty = sys.stdout.isatty() if is_tty is None else is_tty
        self.json = json_mode or not self.is_tty

    def emit_json(self, value: object) -> None:
        print_json(value)

    def emit_list(
        self,
        rows: list[dict[str, Any]],
        columns: list[str] | None,
        title: str | None = None,
    ) -> None:
        if self.json:
            self.emit_json(rows)
            return
        render_rows(rows, columns or (list(rows[0].keys()) if rows else []), title)

    def emit_record(self, record: Any, title: str | None = None) -> None:
        if self.json:
            self.emit_json(record)
        elif isinstance(record, dict):
            render_record(record, title=title)
        else:
            _out_console.print(_scalar(record))

    def progress(self, message: str) -> None:
        err_console.print(f"[dim]{message}[/dim]")

    def success(self, message: str) -> None:
        err_console.print(f"[green]{message}[/green]")

    def error(self, message: str, code: int = 1) -> "typer.Exit":
        from .errors import FrappeError, error_hint

        err_console.print(f"[red]error:[/red] {message}")
        hint = error_hint(message)
        if hint:
            err_console.print(f"[dim]tip:[/dim] {hint}")
        exc = sys.exc_info()[1]
        if isinstance(exc, FrappeError) and exc.has_server_exception:
            err_console.print(
                "[dim]tip:[/dim] re-run with --debug to see the full server traceback."
            )
        return typer.Exit(code)


@dataclass(init=False)
class ApplicationContext:
    """Run-wide dependencies and user-selected execution state."""

    def __init__(
        self,
        json_mode: bool,
        profile: str | None = None,
        debug: bool = False,
    ):
        from .session import ClientFactory

        self.profile = profile
        self.debug = debug
        self.output = Output(json_mode)
        self.client_factory = ClientFactory()

    @property
    def json(self) -> bool:
        return self.output.json

    @json.setter
    def json(self, value: bool) -> None:
        self.output.json = value

    @property
    def is_tty(self) -> bool:
        return self.output.is_tty

    @is_tty.setter
    def is_tty(self, value: bool) -> None:
        self.output.is_tty = value


Ctx = ApplicationContext


def get_ctx(ctx: typer.Context) -> ApplicationContext:
    obj = ctx.obj
    assert isinstance(obj, ApplicationContext)
    return obj


_JSON_WIDTH = 100


def _dumps(value: Any, level: int) -> str:
    """Render one value, inline when it fits on a line and expanded when not."""
    inline = json.dumps(value, separators=(",", ":"), default=str, ensure_ascii=False)
    if level + len(inline) <= _JSON_WIDTH or not isinstance(value, (dict, list)):
        return inline
    pad = " " * level
    if isinstance(value, dict):
        body = ",\n".join(
            f"{pad} {json.dumps(str(k), ensure_ascii=False)}:{_dumps(v, level + 1)}"
            for k, v in value.items()
        )
        return f"{{\n{body}\n{pad}}}"
    body = ",\n".join(f"{pad} {_dumps(v, level + 1)}" for v in value)
    return f"[\n{body}\n{pad}]"


def print_json(data: Any) -> None:
    """Write JSON to stdout, one line per value that does not fit inline.

    Padding and deep indentation cost a token on every line and tell the
    reader nothing. A child row or a short list therefore stays on one line,
    and only a value wider than the line limit opens up.
    """
    sys.stdout.write(_dumps(data, 0))
    sys.stdout.write("\n")


def fail(message: str, code: int = 1) -> "typer.Exit":
    """Print an error to stderr and return an Exit to raise.

    When the message matches a known failure shape, append a one-line tip
    pointing at the command that can resolve it. And when the error being
    handled carried a full server traceback that was suppressed (``--debug``
    off), nudge the caller toward it. Hints go to stderr, so they never pollute
    JSON on stdout.
    """
    return Output(json_mode=False).error(message, code)


def _scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "✓" if value else ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return str(value)


def render_rows(
    rows: list[dict[str, Any]], columns: list[str], title: str | None = None
) -> None:
    """Render a list of dicts as a rich table on stdout."""
    table = Table(title=title, header_style="bold cyan", show_lines=False)
    for col in columns:
        table.add_column(col, overflow="fold")
    for row in rows:
        table.add_row(*[_scalar(row.get(col)) for col in columns])
    _out_console.print(table)
    if not rows:
        err_console.print("[dim]No records.[/dim]")


def render_record(record: dict[str, Any], title: str | None = None) -> None:
    """Render a single document as a two-column key/value table."""
    table = Table(title=title, header_style="bold cyan", show_header=False, box=None)
    table.add_column("field", style="bold")
    table.add_column("value", overflow="fold")
    for key, value in record.items():
        table.add_row(key, _scalar(value))
    _out_console.print(table)


def emit_list(
    ctx: Ctx,
    rows: list[dict[str, Any]],
    columns: list[str] | None,
    title: str | None = None,
) -> None:
    ctx.output.emit_list(rows, columns, title)


def emit_source(source: str) -> None:
    """Render Python source with syntax highlighting on stdout.

    Uses a transparent background so the highlighted code blends with the
    terminal theme instead of painting an opaque block.
    """
    from rich.syntax import Syntax

    _out_console.print(Syntax(source, "python", background_color="default"))


def emit_record(ctx: Ctx, record: Any, title: str | None = None) -> None:
    ctx.output.emit_record(record, title)
