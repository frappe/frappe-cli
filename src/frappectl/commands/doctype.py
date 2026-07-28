"""``frappectl doctype`` — introspection / meta suite for agent self-orientation."""

from __future__ import annotations

from typing import Any, Optional

import typer

from ..client import Document
from ..errors import FrappeError
from ..output import emit_list, emit_record, fail, get_ctx, print_json
from ..session import get_client

app = typer.Typer(
    no_args_is_help=True, help="Inspect DocTypes: fields, links, permissions."
)


@app.command("list")
def list_doctypes(
    ctx: typer.Context,
    filter_module: Optional[str] = typer.Option(
        None, "--module", help="Filter by module."
    ),
    custom: bool = typer.Option(False, "--custom", help="Only custom DocTypes."),
    limit: int = typer.Option(10000, "--limit", help="Max rows."),
) -> None:
    """List DocTypes on the site."""
    c = get_ctx(ctx)
    client = get_client(c)
    filters: dict[str, Any] = {}
    if filter_module:
        filters["module"] = filter_module
    if custom:
        filters["custom"] = 1
    try:
        rows, _ = client.list_documents(
            "DocType",
            fields=["name", "module"],
            filters=filters or None,
            order_by="name asc",
            limit=limit,
        )
    except FrappeError as e:
        raise fail(e.message)
    emit_list(c, rows, ["name", "module"])


def _summarize_field(df: Document, in_list_view: bool = False) -> Document:
    summary: Document = {
        "fieldname": df.get("fieldname"),
        "label": df.get("label"),
        "fieldtype": df.get("fieldtype"),
    }
    if df.get("options"):
        summary["options"] = df.get("options")
    if df.get("reqd"):
        summary["reqd"] = True
    if in_list_view:
        summary["in_list_view"] = bool(df.get("in_list_view"))
    return summary


_PERM_BOILERPLATE = {
    "doctype",
    "name",
    "creation",
    "modified",
    "modified_by",
    "owner",
    "parent",
    "parentfield",
    "parenttype",
    "idx",
}


def _summarize_permission(perm: Document) -> Document:
    return {
        k: v
        for k, v in perm.items()
        if k not in _PERM_BOILERPLATE and (v != 0 or k == "permlevel")
    }


@app.command("show")
def show_doctype(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="DocType name."),
    raw: bool = typer.Option(False, "--raw", help="Print the full unprocessed meta."),
) -> None:
    """Show fields, types, link targets and permissions."""
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
    fields = [
        _summarize_field(f, in_list_view=not c.json)
        for f in all_fields
        if f.get("fieldtype") not in layout
    ]
    links = [
        {"fieldname": f.get("fieldname"), "target": f.get("options")}
        for f in all_fields
        if f.get("fieldtype") == "Link"
    ]
    is_virtual = bool(meta.get("is_virtual"))

    if c.json:
        payload = {
            "name": meta.get("name"),
            "module": meta.get("module"),
            "issingle": meta.get("issingle"),
            "istable": meta.get("istable"),
            "is_submittable": meta.get("is_submittable"),
            "title_field": meta.get("title_field"),
            "autoname": meta.get("autoname"),
            "fields": fields,
            "links": links,
            "permissions": [
                _summarize_permission(p) for p in meta.get("permissions", [])
            ],
        }
        if is_virtual:
            payload["is_virtual"] = True
        print_json(payload)
        return

    record: Document = {
        "name": meta.get("name"),
        "module": meta.get("module"),
        "is_submittable": bool(meta.get("is_submittable")),
        "issingle": bool(meta.get("issingle")),
        "title_field": meta.get("title_field"),
        "autoname": meta.get("autoname"),
    }
    if is_virtual:
        record["is_virtual"] = True
    emit_record(c, record, title=f"DocType {name}")
    emit_list(
        c,
        fields,
        ["fieldname", "label", "fieldtype", "options", "reqd", "in_list_view"],
    )
