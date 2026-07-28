"""``frappectl doc`` — generic DocType verbs."""

from __future__ import annotations

import json
import sys
from typing import Any, Optional, cast

import typer

from .. import helpers
from ..client import Document, Filters, FrappeClient
from ..errors import FrappeError
from ..output import (
    Ctx,
    emit_list,
    emit_record,
    err_console,
    fail,
    get_ctx,
)
from ..session import get_client

app = typer.Typer(
    no_args_is_help=True, help="Create, read, update and delete documents."
)


def _read_stdin_json() -> Optional[Document]:
    if sys.stdin.isatty():
        return None
    raw = sys.stdin.read().strip()
    if not raw:
        return None
    try:
        return cast(Document, json.loads(raw))
    except json.JSONDecodeError as e:
        raise fail(f"stdin is not valid JSON: {e}", 2)


def _load_input(input_file: Optional[str], set_values: list[str]) -> Document:
    """Merge a JSON document (stdin or --input) with --set scalars."""
    data: dict[str, Any] = {}
    if input_file:
        try:
            with open(input_file) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise fail(f"Could not read --input {input_file}: {e}", 2)
    else:
        stdin_data = _read_stdin_json()
        if stdin_data is not None:
            data = stdin_data

    if not isinstance(data, dict):
        raise fail("Document input must be a JSON object.", 2)

    if set_values:
        data.update(helpers.parse_set(set_values))
    return data


@app.command("list")
def list_docs(
    ctx: typer.Context,
    doctype: str = typer.Argument(..., help="DocType, e.g. 'Sales Invoice'."),
    filters: list[str] = typer.Option(
        [], "-f", help="Filter, e.g. -f status=Paid -f 'grand_total>1000'. Repeatable."
    ),
    filters_json: Optional[str] = typer.Option(
        None, "--filters-json", help="Full Frappe filter syntax as JSON."
    ),
    fields: Optional[str] = typer.Option(
        None,
        "--fields",
        help="Comma-separated fields, or '*' for all. Default: meta-driven.",
    ),
    order_by: str = typer.Option(
        "creation desc", "--order-by", help="Sort order, e.g. 'creation desc'."
    ),
    limit: int = typer.Option(20, "--limit", help="Max rows."),
    all_: bool = typer.Option(False, "--all", help="Auto-paginate all matching rows."),
) -> None:
    """List documents of a DocType."""
    c = get_ctx(ctx)
    client = get_client(c)

    if filters_json:
        flt = helpers.parse_filters_json(filters_json)
    elif filters:
        flt = helpers.parse_filters(filters)
    else:
        flt = None

    field_list = helpers.parse_fields(fields)
    if field_list is None:
        try:
            meta = client.get_meta(doctype)
            field_list = helpers.default_fields(meta)
        except FrappeError:
            field_list = ["name"]
    elif field_list == ["*"]:
        field_list = None

    try:
        rows = _fetch(client, doctype, field_list, flt, order_by, limit, all_, c)
    except FrappeError as e:
        raise fail(e.message)

    columns = field_list if (field_list and field_list != ["*"]) else None
    emit_list(c, rows, columns)


def _fetch(
    client: FrappeClient,
    doctype: str,
    field_list: list[str] | None,
    flt: Filters | None,
    order_by: str,
    limit: int,
    all_: bool,
    c: Ctx,
) -> list[Document]:
    if not all_:
        rows, _ = client.list_documents(
            doctype,
            fields=field_list,
            filters=flt,
            order_by=order_by,
            limit=limit,
        )
        return rows

    all_rows: list[Document] = []
    page = 200
    start = 0
    while True:
        batch, has_next = client.list_documents(
            doctype,
            fields=field_list,
            filters=flt,
            order_by=order_by,
            start=start,
            limit=page,
        )
        all_rows.extend(batch)
        if not c.json or c.is_tty:
            err_console.print(f"[dim]fetched {len(all_rows)}…[/dim]", end="\r")
        if not has_next or not batch:
            break
        start += page
    if all_rows and (not c.json or c.is_tty):
        err_console.print(f"[dim]fetched {len(all_rows)} total.[/dim]")
    return all_rows


_CHILD_BOILERPLATE = {
    "doctype",
    "owner",
    "creation",
    "modified",
    "modified_by",
    "docstatus",
    "parent",
    "parentfield",
    "parenttype",
}


def _slim_document(doc: Document) -> Document:
    """Strip values a reader can derive from the parent or does not need.

    Child rows repeat the parent's ownership, timestamps and docstatus on every
    row, which dominates the output of a document with large tables.
    """
    slim: Document = {}
    for key, value in doc.items():
        if key == "idx":
            continue
        if key == "amended_from" and value is None:
            continue
        if isinstance(value, list) and value and isinstance(value[0], dict):
            slim[key] = [
                {k: v for k, v in row.items() if k not in _CHILD_BOILERPLATE}
                if isinstance(row, dict)
                else row
                for row in value
            ]
        else:
            slim[key] = value
    return slim


@app.command("get")
def get_doc(
    ctx: typer.Context,
    doctype: str = typer.Argument(...),
    name: str = typer.Argument(...),
) -> None:
    """Fetch a single document by name."""
    c = get_ctx(ctx)
    client = get_client(c)
    try:
        doc = client.get_document(doctype, name)
    except FrappeError as e:
        raise fail(e.message)
    emit_record(c, _slim_document(doc), title=f"{doctype} {name}")


@app.command("create")
def create_doc(
    ctx: typer.Context,
    doctype: str = typer.Argument(...),
    set_values: list[str] = typer.Option(
        [], "--set", help="field=value scalar. Repeatable."
    ),
    input_file: Optional[str] = typer.Option(
        None, "--input", help="JSON document file (for child tables / nesting)."
    ),
) -> None:
    """Create a document. Reads JSON from stdin when piped."""
    c = get_ctx(ctx)
    data = _load_input(input_file, set_values)
    data.pop("doctype", None)
    client = get_client(c)
    try:
        doc = client.create_document(doctype, data)
    except FrappeError as e:
        raise fail(e.message)
    if not c.json:
        err_console.print(f"[green]created[/green] {doctype} {doc.get('name')}")
    emit_record(c, doc)


@app.command("update")
def update_doc(
    ctx: typer.Context,
    doctype: str = typer.Argument(...),
    name: str = typer.Argument(...),
    set_values: list[str] = typer.Option(
        [], "--set", help="field=value scalar. Repeatable."
    ),
    input_file: Optional[str] = typer.Option(
        None, "--input", help="JSON document file."
    ),
    force: bool = typer.Option(
        False, "--force", help="Skip optimistic-concurrency check (overwrite)."
    ),
) -> None:
    """Update a document. Optimistic by default — fails on concurrent edits."""
    c = get_ctx(ctx)
    data = _load_input(input_file, set_values)
    data.pop("doctype", None)
    data.pop("name", None)
    if not data:
        raise fail("Nothing to update. Use --set, --input, or pipe JSON.", 2)

    client = get_client(c)
    try:
        if not force and "modified" not in data:
            current = client.get_document(doctype, name)
            if current.get("modified"):
                data["modified"] = current["modified"]
        elif force:
            data.pop("modified", None)
        doc = client.update_document(doctype, name, data)
    except FrappeError as e:
        if _is_conflict(e):
            raise fail(
                f"{doctype} {name} was modified since you read it. "
                "Re-read and retry, or pass --force to overwrite."
            )
        raise fail(e.message)
    if not c.json:
        err_console.print(f"[green]updated[/green] {doctype} {name}")
    emit_record(c, doc)


def _is_conflict(e: FrappeError) -> bool:
    msg = (e.message or "").lower()
    return (
        e.status_code == 409
        or "timestampmismatch" in msg
        or "modified after you have opened" in msg
        or "please refresh" in msg
    )


@app.command("delete")
def delete_doc(
    ctx: typer.Context,
    doctype: str = typer.Argument(...),
    name: str = typer.Argument(...),
) -> None:
    """Delete a document."""
    c = get_ctx(ctx)
    client = get_client(c)
    try:
        client.delete_document(doctype, name)
    except FrappeError as e:
        raise fail(e.message)
    if c.json:
        emit_record(c, {"deleted": name, "doctype": doctype})
    else:
        err_console.print(f"[green]deleted[/green] {doctype} {name}")


def _lifecycle(c: Ctx, doctype: str, name: str, method: str, verb: str) -> None:
    client = get_client(c)
    try:
        client.run_doc_method(doctype, name, method)
        doc = client.get_document(doctype, name)
    except FrappeError as e:
        raise fail(e.message)
    if not c.json:
        err_console.print(f"[green]{verb}[/green] {doctype} {name}")
    emit_record(c, doc)


@app.command("submit")
def submit_doc(
    ctx: typer.Context,
    doctype: str = typer.Argument(...),
    name: str = typer.Argument(...),
) -> None:
    """Submit a document (docstatus 1)."""
    _lifecycle(get_ctx(ctx), doctype, name, "submit", "submitted")


@app.command("cancel")
def cancel_doc(
    ctx: typer.Context,
    doctype: str = typer.Argument(...),
    name: str = typer.Argument(...),
) -> None:
    """Cancel a submitted document (docstatus 2)."""
    _lifecycle(get_ctx(ctx), doctype, name, "cancel", "cancelled")


@app.command("amend")
def amend_doc(
    ctx: typer.Context,
    doctype: str = typer.Argument(...),
    name: str = typer.Argument(...),
) -> None:
    """Create a new draft amending a cancelled document."""
    c = get_ctx(ctx)
    client = get_client(c)
    try:
        original = client.get_document(doctype, name)
        if original.get("docstatus") != 2:
            raise fail(
                f"{doctype} {name} is not cancelled; only cancelled documents "
                "can be amended."
            )
        new_doc = {
            k: v
            for k, v in original.items()
            if k not in {"name", "owner", "creation", "modified", "modified_by", "idx"}
        }
        new_doc["docstatus"] = 0
        new_doc["amended_from"] = name
        doc = client.create_document(doctype, new_doc)
    except FrappeError as e:
        raise fail(e.message)
    if not c.json:
        err_console.print(
            f"[green]amended[/green] {doctype} {name} → {doc.get('name')}"
        )
    emit_record(c, doc)
