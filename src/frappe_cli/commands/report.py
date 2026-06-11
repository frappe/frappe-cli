"""``fr report`` — run query/script reports with the same filter UX as list."""

from __future__ import annotations

import json
from typing import Optional

import typer

from .. import helpers
from ..errors import FrappeError
from ..output import emit_list, fail, get_ctx, print_json
from ..session import get_client

app = typer.Typer(no_args_is_help=True, help="Run reports.")


def _filters_to_dict(filters: list[str], filters_json: Optional[str]) -> dict:
    """Reports take filters as a flat {field: value} dict."""
    if filters_json:
        parsed = helpers.parse_filters_json(filters_json)
        if not isinstance(parsed, dict):
            raise fail("--filters-json for reports must be a JSON object.", 2)
        return parsed
    out: dict = {}
    for token in filters:
        field, op, value = helpers.parse_filter(token)
        if op != "=":
            raise fail(
                f"Report filters only support field=value (got {token!r}); "
                "use --filters-json for operators.",
                2,
            )
        out[field] = value
    return out


def _column_keys(columns: list) -> list[str]:
    """Return field keys from a report's column metadata."""
    keys: list[str] = []
    for col in columns:
        if isinstance(col, dict):
            key = col.get("fieldname") or col.get("label")
            if key:
                keys.append(key)
        elif isinstance(col, str):
            # "Label:Type:Width" legacy format.
            keys.append(col.split(":", 1)[0])
    return keys


@app.command("run")
def run_report(
    ctx: typer.Context,
    report: str = typer.Argument(..., help="Report name, e.g. 'Accounts Receivable'."),
    filters: list[str] = typer.Option([], "-f", help="field=value filter. Repeatable."),
    filters_json: Optional[str] = typer.Option(None, "--filters-json", help="Filters as JSON."),
):
    """Run a report and print its rows."""
    c = get_ctx(ctx)
    client = get_client(c)
    flt = _filters_to_dict(filters, filters_json)

    try:
        result = client.call_method(
            "frappe.desk.query_report.run",
            params={"report_name": report, "filters": json.dumps(flt)},
            http_method="GET",
        )
    except FrappeError as e:
        raise fail(e.message)

    columns = result.get("columns", []) if isinstance(result, dict) else []
    rows = result.get("result", []) if isinstance(result, dict) else []
    keys = _column_keys(columns)

    # Normalize list-rows into dicts keyed by column fieldname.
    norm: list[dict] = []
    for row in rows:
        if isinstance(row, dict):
            norm.append(row)
        elif isinstance(row, (list, tuple)):
            norm.append({keys[i]: v for i, v in enumerate(row) if i < len(keys)})
    norm = [r for r in norm if r]

    if c.json:
        print_json({"columns": columns, "result": rows})
        return

    display_cols = keys or (list(norm[0].keys()) if norm else [])
    emit_list(c, norm, display_cols, title=report)
