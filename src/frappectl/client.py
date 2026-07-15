"""Thin httpx wrapper around the Frappe v2 REST API.

This is the only layer that talks HTTP. Doc verbs, reports and files are all
sugar over the same handful of methods, so an MCP wrapper (or anything else)
could sit on this class without touching the CLI.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from typing import Any, BinaryIO, cast
from urllib.parse import quote

import httpx

from .errors import FrappeError, extract_message

# A Frappe document (or any JSON object) as returned by the REST API: a mapping
# keyed by fieldname. The values are unchecked server JSON, hence ``Any``.
Document = dict[str, Any]

# Query filters accepted by the list endpoints: either the list-of-lists form
# (``[["field", "op", value], ...]``) or the dict shorthand (``{"field": value}``).
Filters = list[Any] | dict[str, Any]

DEFAULT_TIMEOUT = 60.0

# Discovery is cache-backed: a cold cache answers 503 while the server queues
# generation. Retry a bounded number of times, honouring Retry-After, so a
# command recovers from a cold cache without ever hanging.
DISCOVERY_MAX_RETRIES = 4
DISCOVERY_FALLBACK_BACKOFF = 2.0  # seconds, when Retry-After is absent
DISCOVERY_MAX_RETRY_WAIT = 30.0  # never wait longer than this per attempt

# Hosts for which plain HTTP is tolerated: the API secret never leaves the box.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

# Never print more than this much of a request body in --debug (file uploads etc.)
_DEBUG_BODY_LIMIT = 2000

# HTTP methods that never mutate server state. A read-only profile is allowed
# exactly these; anything else is refused before it reaches the wire.
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _is_local_host(host: str) -> bool:
    host = (host or "").lower()
    return host in _LOCAL_HOSTS or host.endswith(".localhost")


def _auth_header(token_type: str, token: str) -> str:
    """The Authorization header value for a credential.

    ``"token"`` -> ``token key:secret`` (API key/secret).
    ``"bearer"`` -> ``Bearer <access_token>`` (OAuth).
    """
    if token_type == "bearer":
        return f"Bearer {token}"
    return f"token {token}"


class FrappeClient:
    def __init__(
        self,
        site: str,
        token: str,
        timeout: float = DEFAULT_TIMEOUT,
        *,
        debug: bool = False,
        read_only: bool = False,
        token_type: str = "token",
        on_unauthorized: Callable[[], str | None] | None = None,
    ):
        self.site = site.rstrip("/")
        self.debug = debug
        self.read_only = read_only
        self._token = token
        self._token_type = token_type
        # Called on a 401 to obtain a fresh token (OAuth refresh). Returns the
        # new access token, or None if it could not be refreshed.
        self._on_unauthorized = on_unauthorized

        # Refuse to put the credential on the wire in cleartext. Plain HTTP is
        # only allowed for local development (localhost / *.localhost / loopback).
        parsed = httpx.URL(self.site)
        if parsed.scheme == "http" and not _is_local_host(parsed.host):
            raise FrappeError(
                f"Refusing to talk to {self.site} over plain HTTP: the "
                "credential would be sent in cleartext. Use an https:// URL "
                "(or a localhost address for local development)."
            )

        self._http = httpx.Client(
            base_url=self.site,
            headers={
                "Authorization": _auth_header(token_type, token),
                "Accept": "application/json",
                "User-Agent": "frappectl",
            },
            timeout=timeout,
            # Follow redirects (e.g. Frappe's trailing-slash normalization).
            # httpx strips the Authorization header on cross-origin redirects,
            # so the credential is never handed to a host we did not configure.
            follow_redirects=True,
            # --debug wires request/response logging straight into the transport,
            # so every request (including retries and redirects) is traced.
            event_hooks=(
                {"request": [self._log_request], "response": [self._log_response]}
                if debug
                else {}
            ),
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "FrappeClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- debug tracing -----------------------------------------------------

    @staticmethod
    def _dbg(line: str) -> None:
        print(line, file=sys.stderr)

    def _log_request(self, request: httpx.Request) -> None:
        self._dbg(f"→ {request.method} {request.url}")
        for name, value in request.headers.items():
            # Never leak the credential, even to the local terminal. Keep the
            # scheme (token / Bearer) so the auth mode is still visible.
            if name.lower() == "authorization":
                scheme = value.split(" ", 1)[0] if " " in value else "token"
                value = f"{scheme} ***"
            self._dbg(f"  {name}: {value}")
        body = request.content
        if body:
            try:
                text = body.decode("utf-8")
            except UnicodeDecodeError:
                self._dbg(f"  <{len(body)} bytes of binary body>")
            else:
                if len(text) > _DEBUG_BODY_LIMIT:
                    text = text[:_DEBUG_BODY_LIMIT] + "… (truncated)"
                self._dbg(f"  body: {text}")

    def _log_response(self, response: httpx.Response) -> None:
        self._dbg(f"← {response.status_code} {response.reason_phrase}")

    def _emit_server_debug(self, body: Any) -> None:
        """Surface server-side debug output (e.g. SQL) returned in the payload.

        The v2 API adds a top-level ``debug`` list (SQL and friends) when a
        request passes ``debug=1`` and the caller may see tracebacks (dev server
        or a system user). ``request()`` unwraps ``data`` and drops the rest, so
        pull the messages out here before they are lost.
        """
        if not self.debug or not isinstance(body, dict):
            return
        messages: list[str] = []
        for entry in body.get("debug") or []:
            if isinstance(entry, dict):
                messages.append(str(entry.get("message", entry)))
            else:
                messages.append(str(entry))
        for message in messages:
            self._dbg(f"  [server] {_strip_control(message)}")

    def _emit_server_error(self, body: Any) -> None:
        """Surface the full server-side traceback of a failed request.

        A v2 error body is ``{"errors": [{"type", "message", "exception", ...}]}``
        where ``exception`` is the full server traceback. :func:`extract_message`
        reduces that to a single human line for the raised error, so under
        ``--debug`` we print the untruncated traceback here (to stderr) before it
        is lost — otherwise a failing whitelisted method gives the caller no way
        to see what actually blew up on the server.
        """
        if not self.debug or not isinstance(body, dict):
            return
        errors = body.get("errors")
        if not isinstance(errors, list):
            return
        for err in errors:
            if not isinstance(err, dict):
                continue
            trace = err.get("exception") or err.get("exc")
            if not isinstance(trace, str) or not trace.strip():
                continue
            self._dbg("  [server traceback]")
            for line in _strip_control(trace).splitlines():
                self._dbg(f"    {line}")

    # --- core request ------------------------------------------------------

    def _try_refresh(self) -> bool:
        """Obtain a fresh token via the refresh callback and re-arm the header.

        Returns True when a new token was installed, so the caller can replay
        the request. A no-op (returns False) when there is no callback or the
        refresh failed — the original 401 then surfaces unchanged.
        """
        if not self._on_unauthorized:
            return False
        new_token = self._on_unauthorized()
        if not new_token:
            return False
        self._token = new_token
        self._http.headers["Authorization"] = _auth_header(self._token_type, new_token)
        return True

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send a request, refreshing once and replaying on a 401.

        Every HTTP call funnels through here so the refresh-retry applies
        uniformly (documents, methods, discovery, auth checks).
        """
        resp = self._http.request(method, path, **kwargs)
        if resp.status_code == 401 and self._try_refresh():
            resp = self._http.request(method, path, **kwargs)
        return resp

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> Any:
        """Make a request and return the unwrapped ``data`` payload.

        Raises :class:`FrappeError` on any non-2xx response, or
        :class:`FrappeError` wrapping a transport error.
        """
        if self.read_only and method.upper() not in _SAFE_METHODS:
            raise FrappeError(
                f"Refusing to send a {method.upper()} request: this profile is "
                "read-only. Only GET, HEAD, and OPTIONS requests are permitted. Use a "
                "writable profile, or pass -X GET for a whitelisted read method."
            )
        try:
            resp = self._send(
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
            self._emit_server_error(body)
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

        self._emit_server_debug(body)

        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body

    def stream_download(
        self,
        path: str,
        writer: Callable[[bytes], object],
        *,
        params: dict[str, Any] | None = None,
    ) -> int:
        """Stream a GET body to ``writer(bytes)`` in chunks; return total bytes.

        Avoids buffering the whole file in memory.
        """
        try:
            resp = self._send_stream(path, _clean_params(params))
            try:
                if resp.status_code >= 400:
                    resp.read()
                    body = _safe_json(resp)
                    self._emit_server_error(body)
                    raise FrappeError(
                        extract_message(body, resp.status_code), resp.status_code
                    )
                total = 0
                for chunk in resp.iter_bytes():
                    writer(chunk)
                    total += len(chunk)
                return total
            finally:
                resp.close()
        except httpx.HTTPError as e:
            raise FrappeError(f"Could not reach {self.site}: {e}") from e

    def _send_stream(self, path: str, params: dict[str, Any] | None) -> httpx.Response:
        """Open a streaming GET, refreshing once and replaying on a 401.

        Mirrors :meth:`_send` for the streaming case, where the body is consumed
        lazily so the response can't be replayed after iteration begins.
        """

        def _open() -> httpx.Response:
            return self._http.send(
                self._http.build_request("GET", path, params=params), stream=True
            )

        resp = _open()
        if resp.status_code == 401 and self._try_refresh():
            resp.close()
            resp = _open()
        return resp

    # --- documents ---------------------------------------------------------

    def list_documents(
        self,
        doctype: str,
        *,
        fields: list[str] | None = None,
        filters: Filters | None = None,
        order_by: str | None = None,
        start: int = 0,
        limit: int = 20,
    ) -> tuple[list[Document], bool]:
        """Return ``(rows, has_next_page)``."""
        params: dict[str, Any] = {"start": start, "limit": limit}
        if fields:
            params["fields"] = json.dumps(fields)
        if filters:
            params["filters"] = json.dumps(filters)
        if order_by:
            params["order_by"] = order_by
        if self.debug:
            # Ask the server to echo the generated SQL (dev server / system user).
            params["debug"] = "true"

        try:
            resp = self._send(
                "GET", f"/api/v2/document/{doctype}", params=_clean_params(params)
            )
        except httpx.HTTPError as e:
            raise FrappeError(f"Could not reach {self.site}: {e}") from e

        if resp.status_code >= 400:
            body = _safe_json(resp)
            self._emit_server_error(body)
            raise FrappeError(extract_message(body, resp.status_code), resp.status_code)

        body = resp.json()
        self._emit_server_debug(body)
        rows = cast("list[Document]", body.get("data", []))
        return rows, bool(body.get("has_next_page"))

    def get_document(self, doctype: str, name: str) -> Document:
        return cast(
            Document, self.request("GET", f"/api/v2/document/{doctype}/{name}/")
        )

    def create_document(self, doctype: str, data: Document) -> Document:
        return cast(
            Document,
            self.request("POST", f"/api/v2/document/{doctype}", json_body=data),
        )

    def update_document(self, doctype: str, name: str, data: Document) -> Document:
        return cast(
            Document,
            self.request(
                "PATCH", f"/api/v2/document/{doctype}/{name}/", json_body=data
            ),
        )

    def delete_document(self, doctype: str, name: str) -> Any:
        return self.request("DELETE", f"/api/v2/document/{doctype}/{name}/")

    def run_doc_method(
        self, doctype: str, name: str, method: str, params: dict[str, Any] | None = None
    ) -> Any:
        return self.request(
            "POST",
            f"/api/v2/document/{doctype}/{name}/method/{method}/",
            json_body=params or {},
        )

    # --- collection --------------------------------------------------------

    def get_meta(self, doctype: str) -> Document:
        return cast(Document, self.request("GET", f"/api/v2/doctype/{doctype}/meta"))

    def get_count(self, doctype: str, filters: Filters | None = None) -> int:
        params: dict[str, Any] = {}
        if filters:
            params["filters"] = json.dumps(filters)
        return cast(
            int, self.request("GET", f"/api/v2/doctype/{doctype}/count", params=params)
        )

    # --- methods -----------------------------------------------------------

    def call_method(
        self,
        method: str,
        *,
        params: dict[str, Any] | None = None,
        http_method: str = "POST",
    ) -> Any:
        path = f"/api/v2/method/{method}"
        verb = http_method.upper()
        if verb == "GET":
            return self.request("GET", path, params=params)
        # Honor the caller's verb (PUT/DELETE/…) rather than silently forcing POST.
        return self.request(verb, path, json_body=params or {})

    def get_logged_user(self) -> str:
        """Return the logged-in user, only when Frappe returned a trusted envelope."""
        try:
            resp = self._send("GET", "/api/v2/method/frappe.auth.get_logged_user")
        except httpx.HTTPError as e:
            raise FrappeError(f"Could not reach {self.site}: {e}") from e

        body: Any = None
        if resp.content:
            try:
                body = resp.json()
            except (json.JSONDecodeError, ValueError):
                body = resp.text

        if resp.status_code >= 400:
            return cast(str, self._handle(resp))

        self._emit_server_debug(body)

        if isinstance(body, dict) and "data" in body:
            user = body["data"]
            if isinstance(user, str) and user and user != "Guest":
                return user

        raise FrappeError(
            "Authentication could not be verified: expected get_logged_user to "
            "return JSON with a non-Guest data value."
        )

    # --- discovery ---------------------------------------------------------

    def _retry_after_seconds(self, resp: httpx.Response) -> float:
        """Seconds to wait before retrying, from Retry-After or a fallback."""
        raw = resp.headers.get("Retry-After")
        wait = DISCOVERY_FALLBACK_BACKOFF
        if raw:
            try:
                # Retry-After is most commonly an integer number of seconds.
                # An HTTP-date form is possible but rare here; fall back if so.
                wait = float(raw)
            except ValueError:
                wait = DISCOVERY_FALLBACK_BACKOFF
        return max(0.0, min(wait, DISCOVERY_MAX_RETRY_WAIT))

    def _discovery_get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        """GET a discovery endpoint, retrying transient 503s on a cold cache.

        Retries are capped so a command never hangs. A persistent 503 surfaces
        as a FrappeError like any other failure; a 404 (unsupported site or
        unknown method) surfaces with ``status_code == 404`` for the caller to
        interpret.
        """
        attempts = 0
        while True:
            try:
                resp = self._send("GET", path, params=_clean_params(params))
            except httpx.HTTPError as e:
                raise FrappeError(f"Could not reach {self.site}: {e}") from e
            if resp.status_code == 503 and attempts < DISCOVERY_MAX_RETRIES:
                attempts += 1
                time.sleep(self._retry_after_seconds(resp))
                continue
            return self._handle(resp)

    def discovery_root(self) -> Any:
        """Root discovery document; also a capability check for the feature."""
        return self._discovery_get("/api/v2/discovery")

    def discovery_search(self, query: str) -> Any:
        return self._discovery_get("/api/v2/discovery/search", params={"q": query})

    def discovery_list(self) -> Any:
        return self._discovery_get("/api/v2/discovery/method")

    def discovery_show(self, method: str) -> Any:
        return self._discovery_get(f"/api/v2/discovery/method/{method}")

    def discovery_doctype_list(self, doctype: str) -> Any:
        """List all discoverable methods for one doctype (live introspection).

        Includes both controller-specific and inherited standard methods, so it
        is not backed by the global discovery cache.
        """
        return self._discovery_get(
            f"/api/v2/discovery/doctype/{quote(doctype, safe='')}"
        )

    def discovery_doctype_show(self, doctype: str, method: str) -> Any:
        """Detail document for a single doctype method."""
        return self._discovery_get(
            f"/api/v2/discovery/doctype/{quote(doctype, safe='')}"
            f"/method/{quote(method, safe='')}"
        )

    def call_document_method(
        self,
        doctype: str,
        name: str,
        method: str,
        *,
        params: dict[str, Any] | None = None,
        http_method: str = "POST",
    ) -> Any:
        """Invoke a whitelisted doctype method against an existing document.

        Targets ``/api/v2/document/{doctype}/{name}/method/{method}`` directly,
        as advertised by discovery — not the ``run_doc_method`` RPC. Path
        components are URL-encoded so names containing ``/``, spaces or ``@``
        are handled correctly.
        """
        path = (
            f"/api/v2/document/{quote(doctype, safe='')}"
            f"/{quote(name, safe='')}"
            f"/method/{quote(method, safe='')}"
        )
        verb = http_method.upper()
        if verb == "GET":
            return self.request("GET", path, params=params)
        return self.request(verb, path, json_body=params or {})

    def discovery_supported(self) -> bool:
        """True if this site exposes method discovery (root is not a 404)."""
        try:
            self.discovery_root()
            return True
        except FrappeError as e:
            if e.status_code == 404:
                return False
            raise

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
    ) -> Document:
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
        return cast(
            Document,
            self.request(
                "POST",
                "/api/v2/method/upload_file",
                data=data,
                files={"file": (filename, fileobj)},
            ),
        )


def _strip_control(text: str) -> str:
    """Drop ANSI/control characters so a hostile server cannot inject terminal
    escape sequences into the user's terminal. Tabs and newlines are kept."""
    return "".join(c for c in text if c >= " " or c in "\t\n")


def _clean_params(
    params: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not params:
        return params
    return {k: v for k, v in params.items() if v is not None}


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError):
        return resp.text
