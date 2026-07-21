"""``frappectl query`` — execute read-only SQL through System Console."""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

import typer

from ..errors import FrappeError
from ..output import emit_list, fail, get_ctx
from ..session import get_client


def _read_query(query: Optional[str], input_file: Optional[str]) -> str:
    if query is not None and input_file is not None:
        raise fail("Pass either QUERY or --input, not both.", 2)

    if input_file is not None or query == "-":
        source = input_file or "-"
        try:
            if source == "-":
                value = sys.stdin.read()
            else:
                with open(source, encoding="utf-8") as f:
                    value = f.read()
        except OSError as e:
            raise fail(f"Could not read query input: {e}", 2)
    elif query is not None:
        value = query
    else:
        raise fail("Missing SQL query. Pass QUERY, use QUERY='-', or use --input.", 2)

    if not value.strip():
        raise fail("SQL query must not be empty.", 2)
    return value


def _rows_from_result(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        raise fail("System Console returned an unexpected response.")

    output = result.get("output")
    if not isinstance(output, str):
        raise fail("System Console returned no SQL output.")

    try:
        rows = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        # System Console catches SQL and permission errors and puts their traceback
        # in `output`, while still returning a successful HTTP response.
        detail = output.strip()
        if detail.startswith("Traceback"):
            detail = detail.rsplit("\n", 1)[-1]
        raise fail(detail or "The SQL query failed.")

    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise fail("System Console returned unexpected SQL output.")
    return rows


def query(
    ctx: typer.Context,
    sql: Optional[str] = typer.Argument(
        None,
        metavar="QUERY",
        help="SQL query. Use '-' to read it from stdin.",
    ),
    input_file: Optional[str] = typer.Option(
        None,
        "--input",
        "-i",
        help="Read SQL from a file ('-' for stdin).",
    ),
) -> None:
    """Run a read-only SQL query and print the result as a table."""
    c = get_ctx(ctx)
    statement = _read_query(sql, input_file)
    client = get_client(c)

    try:
        result = client.execute_read_only_sql(statement)
    except FrappeError as e:
        raise fail(e.message)

    rows = _rows_from_result(result)
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    emit_list(c, rows, columns, title="Query result")
