"""``frappe-cli method`` — discover whitelisted API methods and server scripts.

Backed by Frappe's ``/api/v2/discovery`` endpoints. Results are filtered for the
current session: guests see guest-accessible methods, authenticated sessions see
their whitelisted methods, and API server scripts appear only with read
permission on ``Server Script``.
"""

from __future__ import annotations

import typer

from ..errors import FrappeError
from ..output import (
    emit_list,
    emit_record,
    emit_source,
    err_console,
    fail,
    get_ctx,
    print_json,
)
from ..session import get_client

app = typer.Typer(no_args_is_help=True, help="Discover and inspect API methods.")

# Human-facing messages for the two distinct 404 shapes discovery can produce.
_NOT_AVAILABLE = "Method discovery is not available on this site."
_METHOD_NOT_FOUND = "Method not found or not visible to this session."


@app.command("search")
def search_methods(
    ctx: typer.Context,
    query: str = typer.Option(..., "--query", "-q", help="Search terms."),
) -> None:
    """Search whitelisted methods by type, path, description and docstring."""
    c = get_ctx(ctx)
    client = get_client(c)
    try:
        result = client.discovery_search(query)
    except FrappeError as e:
        if e.status_code == 404:
            raise fail(_NOT_AVAILABLE)
        raise fail(e.message)

    if c.json:
        print_json(result)
        return

    results = result.get("results", []) if isinstance(result, dict) else []
    emit_list(
        c,
        results,
        ["path", "type", "allow_guest", "description"],
        title=f"Methods matching {query!r}",
    )


@app.command("list")
def list_methods(ctx: typer.Context) -> None:
    """List whitelisted methods visible to the current session."""
    c = get_ctx(ctx)
    client = get_client(c)
    try:
        result = client.discovery_list()
    except FrappeError as e:
        if e.status_code == 404:
            raise fail(_NOT_AVAILABLE)
        raise fail(e.message)

    if c.json:
        print_json(result)
        return

    methods = result.get("methods", []) if isinstance(result, dict) else []
    emit_list(c, methods, ["path", "allow_guest", "description"], title="Methods")


@app.command("show")
def show_method(
    ctx: typer.Context,
    method: str = typer.Argument(..., help="Method path, e.g. 'frappe.ping'."),
) -> None:
    """Show the detailed contract for a single method."""
    c = get_ctx(ctx)
    client = get_client(c)
    try:
        result = client.discovery_show(method)
    except FrappeError as e:
        if e.status_code == 404:
            # A 404 here is ambiguous: either the whole feature is missing, or
            # the method is unknown/invisible. Probe the root to disambiguate.
            if client.discovery_supported():
                raise fail(_METHOD_NOT_FOUND)
            raise fail(_NOT_AVAILABLE)
        raise fail(e.message)

    if c.json:
        print_json(result)
        return

    params = result.get("params", []) if isinstance(result, dict) else []
    emit_record(
        c,
        {
            "path": result.get("path"),
            "name": result.get("name"),
            "allow_guest": bool(result.get("allow_guest")),
            "http_methods": ", ".join(result.get("http_methods") or []),
            "endpoint": result.get("endpoint"),
            "docstring": result.get("docstring"),
        },
        title=f"Method {method}",
    )
    if params:
        err_console.print("[bold]Parameters[/bold]")
        emit_list(c, params, ["name", "type", "required", "default"])
    else:
        err_console.print("[dim]No parameters.[/dim]")

    # Source is present only when the method's app opts in via the
    # `expose_discovery_source` hook; older sites omit the key entirely.
    source = result.get("source") if isinstance(result, dict) else None
    if source:
        err_console.print("[bold]Source[/bold]")
        emit_source(source)
