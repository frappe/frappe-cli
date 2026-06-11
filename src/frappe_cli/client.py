"""Thin httpx wrapper around the Frappe v2 REST API.

This is the only layer that talks HTTP. Doc verbs, reports and files are all
sugar over the same handful of methods, so an MCP wrapper (or anything else)
could sit on this class without touching the CLI.
"""

from __future__ import annotations

import json
from typing import Any, BinaryIO

import httpx

from .errors import FrappeError, extract_message

DEFAULT_TIMEOUT = 60.0


class FrappeClient:
    def __init__(self, site: str, token: str, timeout: float = DEFAULT_TIMEOUT):
        self.site = site.rstrip("/")
        self._http = httpx.Client(
            base_url=self.site,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/json",
                "User-Agent": "frappe-cli",
            },
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "FrappeClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- core request ------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
        data: dict | None = None,
        files: dict | None = None,
    ) -> Any:
        """Make a request and return the unwrapped ``data`` payload.

        Raises :class:`FrappeError` on any non-2xx response, or
        :class:`FrappeError` wrapping a transport error.
        """
        try:
            resp = self._http.request(
                method,
                path,
                params=_clean_params(params),
                json=json_body,
                data=data,
                files=files,
            )
        except httpx.HTTPError as e:
            raise FrappeError(f"Could not reach {self.site}: {e}") from e

        return self._handle(resp)

    def _handle(self, resp: httpx.Response) -> Any:
        body: Any = None
        if resp.content:
            try:
                body = resp.json()
            except (json.JSONDecodeError, ValueError):
                body = resp.text

        if resp.status_code >= 400:
            if resp.status_code == 401:
                raise FrappeError(
                    "Authentication failed (401). Check the API key/secret for "
                    "this site.",
                    401,
                )
            if resp.status_code == 403:
                msg = extract_message(body, 403)
                raise FrappeError(
                    msg if msg != "HTTP 403" else "Permission denied (403).", 403
                )
            raise FrappeError(extract_message(body, resp.status_code), resp.status_code)

        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body

    def raw(
        self, method: str, path: str, *, params: dict | None = None, json_body: Any = None
    ) -> httpx.Response:
        """Like :meth:`request` but returns the raw response (for downloads)."""
        try:
            resp = self._http.request(
                method, path, params=_clean_params(params), json=json_body
            )
        except httpx.HTTPError as e:
            raise FrappeError(f"Could not reach {self.site}: {e}") from e
        if resp.status_code >= 400:
            body: Any = None
            if resp.content:
                try:
                    body = resp.json()
                except (json.JSONDecodeError, ValueError):
                    body = resp.text
            raise FrappeError(extract_message(body, resp.status_code), resp.status_code)
        return resp

    # --- documents ---------------------------------------------------------

    def list_documents(
        self,
        doctype: str,
        *,
        fields: list[str] | None = None,
        filters: list | dict | None = None,
        order_by: str | None = None,
        start: int = 0,
        limit: int = 20,
    ) -> tuple[list[dict], bool]:
        """Return ``(rows, has_next_page)``."""
        params: dict[str, Any] = {"start": start, "limit": limit}
        if fields:
            params["fields"] = json.dumps(fields)
        if filters:
            params["filters"] = json.dumps(filters)
        if order_by:
            params["order_by"] = order_by

        try:
            resp = self._http.get(
                f"/api/v2/document/{doctype}", params=_clean_params(params)
            )
        except httpx.HTTPError as e:
            raise FrappeError(f"Could not reach {self.site}: {e}") from e

        if resp.status_code >= 400:
            body = _safe_json(resp)
            raise FrappeError(extract_message(body, resp.status_code), resp.status_code)

        body = resp.json()
        return body.get("data", []), bool(body.get("has_next_page"))

    def get_document(self, doctype: str, name: str) -> dict:
        return self.request("GET", f"/api/v2/document/{doctype}/{name}/")

    def create_document(self, doctype: str, data: dict) -> dict:
        return self.request("POST", f"/api/v2/document/{doctype}", json_body=data)

    def update_document(self, doctype: str, name: str, data: dict) -> dict:
        return self.request(
            "PATCH", f"/api/v2/document/{doctype}/{name}/", json_body=data
        )

    def delete_document(self, doctype: str, name: str) -> Any:
        return self.request("DELETE", f"/api/v2/document/{doctype}/{name}/")

    def run_doc_method(
        self, doctype: str, name: str, method: str, params: dict | None = None
    ) -> Any:
        return self.request(
            "POST",
            f"/api/v2/document/{doctype}/{name}/method/{method}/",
            json_body=params or {},
        )

    # --- collection --------------------------------------------------------

    def get_meta(self, doctype: str) -> dict:
        return self.request("GET", f"/api/v2/doctype/{doctype}/meta")

    def get_count(self, doctype: str, filters: list | dict | None = None) -> int:
        params = {}
        if filters:
            params["filters"] = json.dumps(filters)
        return self.request("GET", f"/api/v2/doctype/{doctype}/count", params=params)

    # --- methods -----------------------------------------------------------

    def call_method(
        self,
        method: str,
        *,
        params: dict | None = None,
        http_method: str = "POST",
    ) -> Any:
        path = f"/api/v2/method/{method}"
        if http_method.upper() == "GET":
            return self.request("GET", path, params=params)
        return self.request("POST", path, json_body=params or {})

    def upload_file(
        self,
        fileobj: BinaryIO,
        filename: str,
        *,
        is_private: bool = False,
        doctype: str | None = None,
        docname: str | None = None,
        fieldname: str | None = None,
        folder: str = "Home",
    ) -> dict:
        data: dict[str, Any] = {
            "is_private": 1 if is_private else 0,
            "folder": folder,
        }
        if doctype:
            data["doctype"] = doctype
        if docname:
            data["docname"] = docname
        if fieldname:
            data["fieldname"] = fieldname
        return self.request(
            "POST",
            "/api/v2/method/upload_file",
            data=data,
            files={"file": (filename, fileobj)},
        )


def _clean_params(params: dict | None) -> dict | None:
    if not params:
        return params
    return {k: v for k, v in params.items() if v is not None}


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError):
        return resp.text
