"""``frappe-cli api`` — raw access to the v2 API.

    frappe-cli api method/frappe.client.get_count -F doctype=User
    frappe-cli api method/gameplan.api.get_unread_count
    frappe-cli api document/ToDo --method GET

Doc verbs are sugar over this same client; this is the sugar-free escape hatch
for whitelisted methods and arbitrary REST paths.
"""

from __future__ import annotations

import json
import sys
from typing import Optional

import typer

from .. import helpers
from ..errors import FrappeError
from ..output import fail, get_ctx, print_json
from ..session import get_client

app = typer.Typer(
    help="Raw v2 API access (whitelisted methods, arbitrary paths).",
    no_args_is_help=True,
)


@app.command()
def api(
    ctx: typer.Context,
    path: str = typer.Argument(
        ...,
        help="API path, e.g. 'method/frappe.client.get_count' or 'document/User'.",
    ),
    fields: list[str] = typer.Option(
        [], "-F", "--field", help="key=value param (typed). Repeatable."
    ),
    raw_fields: list[str] = typer.Option(
        [], "-f", "--raw-field", help="key=value param (always string). Repeatable."
    ),
    method: Optional[str] = typer.Option(
        None, "--method", "-X", help="HTTP method. Default: GET, or POST if params/input given."
    ),
    input_file: Optional[str] = typer.Option(
        None, "--input", help="JSON request body file ('-' for stdin)."
    ),
):
    """Make a raw request to /api/v2/<path>."""
    c = get_ctx(ctx)

    params: dict = {}
    if fields:
        params.update(helpers.parse_method_params(fields))
    for item in raw_fields:
        if "=" not in item:
            raise fail(f"-f expects key=value, got {item!r}", 2)
        k, v = item.split("=", 1)
        params[k.strip()] = v

    body = None
    if input_file:
        try:
            text = sys.stdin.read() if input_file == "-" else open(input_file).read()
            body = json.loads(text)
        except (OSError, json.JSONDecodeError) as e:
            raise fail(f"Could not read --input: {e}", 2)

    # Default GET (params ride along as query string); a request body (--input)
    # implies POST. Use --method to force a verb explicitly.
    http_method = (method or ("POST" if body is not None else "GET")).upper()

    clean_path = path.lstrip("/")
    if clean_path.startswith("api/v2/"):
        clean_path = clean_path[len("api/v2/"):]
    full_path = f"/api/v2/{clean_path}"

    client = get_client(c)
    try:
        if http_method == "GET":
            result = client.request("GET", full_path, params=params or None)
        else:
            payload = body if body is not None else params
            result = client.request(
                http_method, full_path, json_body=payload, params=params if body is not None else None
            )
    except FrappeError as e:
        raise fail(e.message)

    print_json(result)
