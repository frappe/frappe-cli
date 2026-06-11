"""``fr doctype`` — introspection / meta suite for agent self-orientation."""

from __future__ import annotations

from typing import Optional

import typer

from ..errors import FrappeError
from ..output import emit_list, emit_record, fail, get_ctx, print_json
from ..session import get_client

app = typer.Typer(no_args_is_help=True, help="Inspect DocTypes: fields, links, permissions.")


@app.command("list")
def list_doctypes(
    ctx: typer.Context,
    filter_module: Optional[str] = typer.Option(None, "--module", help="Filter by module."),
    custom: bool = typer.Option(False, "--custom", help="Only custom DocTypes."),
    limit: int = typer.Option(500, "--limit", help="Max rows."),
):
    """List DocTypes on the site."""
    c = get_ctx(ctx)
    client = get_client(c)
    filters = {}
    if filter_module:
        filters["module"] = filter_module
    if custom:
        filters["custom"] = 1
    try:
        rows, _ = client.list_documents(
            "DocType",
            fields=["name", "module", "issingle", "istable", "custom"],
            filters=filters or None,
            order_by="name asc",
            limit=limit,
        )
    except FrappeError as e:
        raise fail(e.message)
    emit_list(c, rows, ["name", "module", "issingle", "istable", "custom"])


def _summarize_field(df: dict) -> dict:
    return {
        "fieldname": df.get("fieldname"),
        "label": df.get("label"),
        "fieldtype": df.get("fieldtype"),
        "options": df.get("options"),
        "reqd": bool(df.get("reqd")),
        "in_list_view": bool(df.get("in_list_view")),
    }


@app.command("show")
def show_doctype(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="DocType name."),
    raw: bool = typer.Option(False, "--raw", help="Print the full unprocessed meta."),
):
    """Show fields, types, link targets, child tables and permissions."""
    c = get_ctx(ctx)
    client = get_client(c)
    try:
        meta = client.get_meta(name)
    except FrappeError as e:
        raise fail(e.message)

    if raw:
        print_json(meta)
        return

    all_fields = meta.get("fields", [])
    layout = {"Section Break", "Column Break", "Tab Break", "HTML", "Heading"}
    fields = [_summarize_field(f) for f in all_fields if f.get("fieldtype") not in layout]
    links = [
        {"fieldname": f.get("fieldname"), "target": f.get("options")}
        for f in all_fields
        if f.get("fieldtype") == "Link"
    ]
    child_tables = [
        {"fieldname": f.get("fieldname"), "child_doctype": f.get("options")}
        for f in all_fields
        if f.get("fieldtype") in {"Table", "Table MultiSelect"}
    ]

    if c.json:
        print_json(
            {
                "name": meta.get("name"),
                "module": meta.get("module"),
                "issingle": meta.get("issingle"),
                "istable": meta.get("istable"),
                "is_submittable": meta.get("is_submittable"),
                "title_field": meta.get("title_field"),
                "autoname": meta.get("autoname"),
                "fields": fields,
                "links": links,
                "child_tables": child_tables,
                "permissions": meta.get("permissions", []),
            }
        )
        return

    emit_record(
        c,
        {
            "name": meta.get("name"),
            "module": meta.get("module"),
            "is_submittable": bool(meta.get("is_submittable")),
            "issingle": bool(meta.get("issingle")),
            "title_field": meta.get("title_field"),
            "autoname": meta.get("autoname"),
        },
        title=f"DocType {name}",
    )
    emit_list(c, fields, ["fieldname", "label", "fieldtype", "options", "reqd", "in_list_view"])
    if child_tables:
        from ..output import err_console

        err_console.print("[bold]Child tables[/bold]")
        emit_list(c, child_tables, ["fieldname", "child_doctype"])
