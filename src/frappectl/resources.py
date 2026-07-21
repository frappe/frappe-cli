"""Focused Frappe API resources built over a shared transport."""

from __future__ import annotations

import time
from typing import Any, BinaryIO, cast

from .errors import FrappeError
from .output import err_console
from .site import endpoint_path
from .transport import (
    DISCOVERY_FALLBACK_BACKOFF,
    DISCOVERY_MAX_RETRIES,
    Document,
    Filters,
    FrappeTransport,
)

DISCOVERY_MAX_RETRY_WAIT = 30.0


class DocumentsAPI:
    def __init__(self, transport: FrappeTransport):
        self._transport = transport

    def list(
        self,
        doctype: str,
        *,
        fields: list[str] | None = None,
        filters: Filters | None = None,
        order_by: str | None = None,
        start: int = 0,
        limit: int = 20,
    ) -> tuple[list[Document], bool]:
        params: dict[str, Any] = {"start": start, "limit": limit}
        if fields:
            params["fields"] = fields
        if filters:
            params["filters"] = filters
        if order_by:
            params["order_by"] = order_by
        if self._transport.debug:
            params["debug"] = "true"
        return self._transport.request_page(
            endpoint_path("api", "v2", "document", doctype), params=params
        )

    def get(self, doctype: str, name: str) -> Document:
        return cast(
            Document,
            self._transport.request(
                "GET",
                endpoint_path(
                    "api", "v2", "document", doctype, name, trailing_slash=True
                ),
            ),
        )

    def create(self, doctype: str, data: Document) -> Document:
        return cast(
            Document,
            self._transport.request(
                "POST", endpoint_path("api", "v2", "document", doctype), json_body=data
            ),
        )

    def update(self, doctype: str, name: str, data: Document) -> Document:
        return cast(
            Document,
            self._transport.request(
                "PATCH",
                endpoint_path(
                    "api", "v2", "document", doctype, name, trailing_slash=True
                ),
                json_body=data,
            ),
        )

    def delete(self, doctype: str, name: str) -> Any:
        return self._transport.request(
            "DELETE",
            endpoint_path("api", "v2", "document", doctype, name, trailing_slash=True),
        )

    def run_method(
        self, doctype: str, name: str, method: str, params: dict[str, Any] | None = None
    ) -> Any:
        return self._transport.request(
            "POST",
            endpoint_path(
                "api",
                "v2",
                "document",
                doctype,
                name,
                "method",
                method,
                trailing_slash=True,
            ),
            json_body=params or {},
        )

    def meta(self, doctype: str) -> Document:
        return cast(
            Document,
            self._transport.request(
                "GET", endpoint_path("api", "v2", "doctype", doctype, "meta")
            ),
        )

    def count(self, doctype: str, filters: Filters | None = None) -> int:
        params = {"filters": filters} if filters else None
        return cast(
            int,
            self._transport.request_read(
                endpoint_path("api", "v2", "doctype", doctype, "count"),
                params=params,
            ),
        )


class MethodsAPI:
    def __init__(self, transport: FrappeTransport):
        self._transport = transport

    def call(
        self,
        method: str,
        *,
        params: dict[str, Any] | None = None,
        http_method: str = "POST",
    ) -> Any:
        path = endpoint_path("api", "v2", "method", method)
        verb = http_method.upper()
        if verb == "GET":
            return self._transport.request_read(path, params=params)
        return self._transport.request(verb, path, json_body=params or {})

    def call_document(
        self,
        doctype: str,
        name: str,
        method: str,
        *,
        params: dict[str, Any] | None = None,
        http_method: str = "POST",
    ) -> Any:
        path = endpoint_path("api", "v2", "document", doctype, name, "method", method)
        verb = http_method.upper()
        if verb == "GET":
            return self._transport.request_read(path, params=params)
        return self._transport.request(verb, path, json_body=params or {})

    def logged_user(self) -> str:
        user = self._transport.request_envelope_data(
            "GET", "/api/v2/method/frappe.auth.get_logged_user"
        )
        if isinstance(user, str) and user and user != "Guest":
            return user
        raise FrappeError(
            "Authentication could not be verified: expected get_logged_user to "
            "return JSON with a non-Guest data value."
        )


class DiscoveryAPI:
    def __init__(self, transport: FrappeTransport):
        self._transport = transport

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        attempts = 0
        while True:
            response = self._transport.send("GET", path, params=params)
            if response.status_code != 503 or attempts >= DISCOVERY_MAX_RETRIES:
                return self._transport.handle_response(response)
            attempts += 1
            raw_wait = response.headers.get("Retry-After")
            try:
                wait = float(raw_wait) if raw_wait else DISCOVERY_FALLBACK_BACKOFF
            except ValueError:
                wait = DISCOVERY_FALLBACK_BACKOFF
            wait = max(0.0, min(wait, DISCOVERY_MAX_RETRY_WAIT))
            err_console.print(
                f"[dim]Discovery cache is being generated; "
                f"retrying in {wait:g} seconds.[/dim]"
            )
            time.sleep(wait)

    def root(self) -> Any:
        return self._get("/api/v2/discovery")

    def search(self, query: str) -> Any:
        return self._get("/api/v2/discovery/search", params={"q": query})

    def list(self) -> Any:
        return self._get("/api/v2/discovery/method")

    def show(self, method: str) -> Any:
        return self._get(endpoint_path("api", "v2", "discovery", "method", method))

    def doctype_list(self, doctype: str) -> Any:
        return self._get(endpoint_path("api", "v2", "discovery", "doctype", doctype))

    def doctype_show(self, doctype: str, method: str) -> Any:
        return self._get(
            endpoint_path(
                "api", "v2", "discovery", "doctype", doctype, "method", method
            )
        )

    def supported(self) -> bool:
        try:
            self.root()
            return True
        except FrappeError as e:
            if e.status_code == 404:
                return False
            raise


class FilesAPI:
    def __init__(self, transport: FrappeTransport):
        self._transport = transport

    def upload(
        self,
        fileobj: BinaryIO,
        filename: str,
        *,
        is_private: bool = False,
        doctype: str | None = None,
        docname: str | None = None,
        fieldname: str | None = None,
        folder: str = "Home",
    ) -> Document:
        data: dict[str, Any] = {
            "is_private": 1 if is_private else 0,
            "folder": folder,
        }
        for key, value in (
            ("doctype", doctype),
            ("docname", docname),
            ("fieldname", fieldname),
        ):
            if value:
                data[key] = value
        return cast(
            Document,
            self._transport.request(
                "POST",
                "/api/v2/method/upload_file",
                data=data,
                files={"file": (filename, fileobj)},
            ),
        )

    def download(
        self,
        path: str,
        writer: Any,
        *,
        params: dict[str, Any] | None = None,
    ) -> int:
        return self._transport.stream_download(path, writer, params=params)
