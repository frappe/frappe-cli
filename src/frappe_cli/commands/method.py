"""``frappe-cli method`` — discover and invoke whitelisted API methods.

Backed by Frappe's ``/api/v2/discovery`` endpoints. Discovery covers two kinds
of method, distinguished by a ``kind`` field:

- ``rpc`` — a dotted whitelisted function path, invoked via ``/api/v2/method``.
- ``doctype`` — a whitelisted controller method, invoked against an existing
  document via ``/api/v2/document/{doctype}/{name}/method/{method}``.

Results are filtered for the current session: guests see guest-accessible
methods, authenticated sessions see their whitelisted methods, and API server
scripts appear only with read permission on ``Server Script``. All discovery
endpoints require the ``System Manager`` role.
"""

from __future__ import annotations

from typing import Any, Optional

import typer

from .. import helpers
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

# Human-facing messages for the distinct 404 shapes discovery can produce.
_NOT_AVAILABLE = "Method discovery is not available on this site."
_METHOD_NOT_FOUND = "Method not found or not visible to this session."


def _summary_row(entry: Any) -> dict[str, Any]:
    """Flatten an ``rpc`` | ``doctype`` method summary to a uniform table row.

    Dispatches on ``kind`` before reading kind-specific fields, and folds the
    two shapes into a single ``ref`` identifier column:

    - ``rpc`` -> the dotted path (``frappe.tests.test_api.test``)
    - ``doctype`` -> ``Doctype.method`` (``User.populate_role_profile_roles``)

    An unknown/forward-compatible ``kind`` falls back to whatever identifier the
    entry carries rather than dropping the row.
    """
    if not isinstance(entry, dict):
        return {"kind": None, "ref": str(entry), "description": None}
    kind = entry.get("kind")
    if kind == "doctype":
        ref = f"{entry.get('doctype')}.{entry.get('method')}"
    else:
        ref = entry.get("path") or entry.get("method") or ""
    return {"kind": kind, "ref": ref, "description": entry.get("description")}


@app.command("search")
def search_methods(
    ctx: typer.Context,
    query: str = typer.Option(..., "--query", "-q", help="Search terms."),
) -> None:
    """Search whitelisted methods by kind, path, doctype, name and docstring."""
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
        [_summary_row(r) for r in results],
        ["kind", "ref", "description"],
        title=f"Methods matching {query!r}",
    )


@app.command("list")
def list_methods(
    ctx: typer.Context,
    doctype: Optional[str] = typer.Option(
        None,
        "--doctype",
        help="List methods for one DocType (live: includes inherited standard "
        "methods) instead of the global RPC + doctype index.",
    ),
) -> None:
    """List whitelisted methods visible to the current session."""
    c = get_ctx(ctx)
    client = get_client(c)
    try:
        if doctype:
            result = client.discovery_doctype_list(doctype)
            title = f"Methods on {doctype}"
        else:
            result = client.discovery_list()
            title = "Methods"
    except FrappeError as e:
        if e.status_code == 404:
            if doctype:
                # Doctype listing is live and cache-independent, so a 404 means
                # the doctype is unknown/undiscoverable (child tables, unknown
                # names) — unless the whole feature is absent.
                if client.discovery_supported():
                    raise fail(
                        f"DocType {doctype!r} not found or has no discoverable methods."
                    )
            raise fail(_NOT_AVAILABLE)
        raise fail(e.message)

    if c.json:
        print_json(result)
        return

    methods = result.get("methods", []) if isinstance(result, dict) else []
    emit_list(
        c,
        [_summary_row(m) for m in methods],
        ["kind", "ref", "description"],
        title=title,
    )


def _show_rpc(c: Any, result: Any, method: str) -> None:
    params = result.get("params", []) if isinstance(result, dict) else []
    emit_record(
        c,
        {
            "kind": "rpc",
            "path": result.get("path"),
            "name": result.get("name"),
            "allow_guest": bool(result.get("allow_guest")),
            "http_methods": ", ".join(result.get("http_methods") or []),
            "endpoint": result.get("endpoint"),
            "docstring": result.get("docstring"),
        },
        title=f"Method {method}",
    )
    _emit_params_and_source(c, result, params)


def _show_doctype(c: Any, result: Any, doctype: str, method: str) -> None:
    params = result.get("params", []) if isinstance(result, dict) else []
    permission = result.get("permission") or {}
    perm_display = ", ".join(f"{k}={v}" for k, v in permission.items())
    emit_record(
        c,
        {
            "kind": "doctype",
            "doctype": result.get("doctype"),
            "method": result.get("method"),
            "defined_in": result.get("defined_in"),
            "http_methods": ", ".join(result.get("http_methods") or []),
            "permission": perm_display,
            "endpoint": result.get("endpoint"),
            "docstring": result.get("docstring"),
        },
        title=f"Method {doctype}.{method}",
    )
    _emit_params_and_source(c, result, params)


def _emit_params_and_source(c: Any, result: Any, params: list[Any]) -> None:
    if params:
        err_console.print("[bold]Parameters[/bold]")
        emit_list(c, params, ["name", "type", "required", "default"])
    else:
        err_console.print("[dim]No parameters.[/dim]")

    # Source is present only when the method's app opts in via the
    # `expose_discovery_source` hook; other sites omit the key entirely.
    source = result.get("source") if isinstance(result, dict) else None
    if source:
        err_console.print("[bold]Source[/bold]")
        emit_source(source)


@app.command("show")
def show_method(
    ctx: typer.Context,
    method: str = typer.Argument(
        ..., help="RPC path (e.g. 'frappe.ping'), or the method name with --doctype."
    ),
    doctype: Optional[str] = typer.Option(
        None, "--doctype", help="Resolve METHOD as a method on this DocType."
    ),
) -> None:
    """Show the detailed contract for a single method."""
    c = get_ctx(ctx)
    client = get_client(c)
    try:
        if doctype:
            result = client.discovery_doctype_show(doctype, method)
        else:
            result = client.discovery_show(method)
    except FrappeError as e:
        if e.status_code == 404:
            # A 404 here is ambiguous: either the whole feature is missing, or
            # the doctype/method is unknown/invisible. Probe the root once to
            # disambiguate.
            if client.discovery_supported():
                raise fail(_METHOD_NOT_FOUND)
            raise fail(_NOT_AVAILABLE)
        raise fail(e.message)

    if c.json:
        print_json(result)
        return

    if doctype:
        _show_doctype(c, result, doctype, method)
    else:
        _show_rpc(c, result, method)


def _pick_verb(http_methods: list[Any]) -> str:
    """Choose a verb for invoking a doctype method from its advertised set.

    Prefer POST when the method offers it (a ``call`` usually intends to act);
    otherwise use the single advertised verb, defaulting to POST.
    """
    verbs = [str(v).upper() for v in http_methods]
    if "POST" in verbs:
        return "POST"
    return verbs[0] if verbs else "POST"


@app.command("call")
def call_method(
    ctx: typer.Context,
    doctype: str = typer.Argument(..., help="DocType, e.g. 'User'."),
    name: str = typer.Argument(..., help="Existing document name."),
    method: str = typer.Argument(..., help="Method name, e.g. 'add_comment'."),
    fields: list[str] = typer.Option(
        [], "-F", "--field", help="key=value param (typed). Repeatable."
    ),
    raw_fields: list[str] = typer.Option(
        [], "-f", "--raw-field", help="key=value param (always string). Repeatable."
    ),
    http_method: Optional[str] = typer.Option(
        None,
        "--method",
        "-X",
        help="HTTP verb. Default: chosen from the method's detail (POST if offered).",
    ),
) -> None:
    """Invoke a whitelisted doctype method against an existing document."""
    c = get_ctx(ctx)
    client = get_client(c)

    params: dict[str, Any] = {}
    if fields:
        params.update(helpers.parse_method_params(fields))
    for item in raw_fields:
        if "=" not in item:
            raise fail(f"-f expects key=value, got {item!r}", 2)
        k, v = item.split("=", 1)
        params[k.strip()] = v

    verb = http_method.upper() if http_method else None
    if verb is None:
        # No explicit verb: consult the detail document to pick GET vs POST.
        try:
            detail = client.discovery_doctype_show(doctype, method)
        except FrappeError as e:
            if e.status_code == 404:
                if client.discovery_supported():
                    raise fail(_METHOD_NOT_FOUND)
                raise fail(_NOT_AVAILABLE)
            raise fail(e.message)
        verb = _pick_verb(
            detail.get("http_methods") or [] if isinstance(detail, dict) else []
        )

    try:
        result = client.call_document_method(
            doctype, name, method, params=params, http_method=verb
        )
    except FrappeError as e:
        raise fail(e.message)
    print_json(result)
