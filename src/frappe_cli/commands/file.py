"""``frappe-cli file`` — upload and download files."""

from __future__ import annotations

import os
import sys
from typing import Optional

import typer

from ..errors import FrappeError
from ..output import emit_record, err_console, fail, get_ctx
from ..session import get_client

app = typer.Typer(no_args_is_help=True, help="Upload and download files.")


@app.command("upload")
def upload(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="Local file to upload."),
    doctype: Optional[str] = typer.Option(
        None, "--doctype", help="Attach to this DocType."
    ),
    name: Optional[str] = typer.Option(None, "--name", help="Attach to this document."),
    fieldname: Optional[str] = typer.Option(
        None, "--fieldname", help="Attach to this field."
    ),
    private: bool = typer.Option(False, "--private", help="Mark the file private."),
):
    """Upload a file, optionally attaching it to a document."""
    c = get_ctx(ctx)
    if not os.path.isfile(path):
        raise fail(f"No such file: {path}", 2)
    if name and not doctype:
        raise fail("--name requires --doctype.", 2)

    client = get_client(c)
    try:
        with open(path, "rb") as f:
            result = client.upload_file(
                f,
                os.path.basename(path),
                is_private=private,
                doctype=doctype,
                docname=name,
                fieldname=fieldname,
            )
    except FrappeError as e:
        raise fail(e.message)

    if not c.json:
        err_console.print(
            f"[green]uploaded[/green] {result.get('file_name')} → {result.get('file_url')}"
        )
    emit_record(c, result)


@app.command("download")
def download(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="File document name, or a /files/... URL."),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output path. Default: derived filename, or '-' for stdout.",
    ),
):
    """Download a file by File name or by file URL."""
    c = get_ctx(ctx)
    client = get_client(c)

    file_url = ref
    file_name = None
    if not ref.startswith("/") and "://" not in ref:
        # Treat as a File document name.
        try:
            doc = client.get_document("File", ref)
        except FrappeError as e:
            raise fail(e.message)
        file_url = doc.get("file_url") or ""
        # file_name is server-controlled and derived from an uploaded filename;
        # basename it so a crafted name like "../../x" can't escape the cwd.
        file_name = os.path.basename(doc.get("file_name") or "") or None
        if not file_url:
            raise fail(f"File {ref} has no file_url.")

    try:
        resp = client.raw("GET", file_url)
    except FrappeError as e:
        raise fail(e.message)

    content = resp.content
    if output == "-":
        sys.stdout.buffer.write(content)
        return

    out_path = (
        output or file_name or os.path.basename(file_url.split("?", 1)[0]) or "download"
    )
    try:
        with open(out_path, "wb") as f:
            f.write(content)
    except OSError as e:
        raise fail(f"Could not write {out_path}: {e}")

    if c.json:
        from ..output import print_json

        print_json({"saved": out_path, "bytes": len(content), "file_url": file_url})
    else:
        err_console.print(
            f"[green]downloaded[/green] {out_path} ({len(content)} bytes)"
        )
