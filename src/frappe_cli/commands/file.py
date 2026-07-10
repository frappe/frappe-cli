"""``frappe-cli file`` — upload and download files."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
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
) -> None:
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
) -> None:
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

    # Stream to stdout without buffering the whole file in memory.
    if output == "-":
        try:
            client.stream_download(file_url, sys.stdout.buffer.write)
        except FrappeError as e:
            raise fail(e.message)
        return

    out_path = (
        output or file_name or os.path.basename(file_url.split("?", 1)[0]) or "download"
    )
    # Stream into an exclusively created temp file beside the destination and
    # rename on success. A predictable `<output>.part` path could be a symlink
    # to another file, causing the download to overwrite that file instead.
    destination = Path(out_path)
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".part",
            delete=False,
        ) as f:
            tmp_path = f.name
            total = client.stream_download(file_url, f.write)
        os.replace(tmp_path, destination)
    except (FrappeError, OSError) as e:
        if tmp_path is not None:
            _unlink_quietly(tmp_path)
        if isinstance(e, FrappeError):
            raise fail(e.message)
        raise fail(f"Could not write {out_path}: {e}")

    if c.json:
        from ..output import print_json

        print_json({"saved": out_path, "bytes": total, "file_url": file_url})
    else:
        err_console.print(f"[green]downloaded[/green] {out_path} ({total} bytes)")


def _unlink_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
