"""HTTP transport and wire policies for the Frappe v2 REST API.

This is the only layer that talks HTTP. Doc verbs, reports and files are all
sugar over the same handful of methods, so an MCP wrapper (or anything else)
could sit on this class without touching the CLI.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any, cast

import httpx

from .credentials import ApiKeyProvider, CredentialProvider
from .errors import FrappeError, extract_message
from .site import SiteURL

Document = dict[str, Any]
Filters = list[Any] | dict[str, Any]

DEFAULT_TIMEOUT = 60.0

# Discovery is cache-backed: a cold cache answers 503 while the server queues
# generation. Retry a bounded number of times, honouring Retry-After, so a
# command recovers from a cold cache without ever hanging.
DISCOVERY_MAX_RETRIES = 4
DISCOVERY_FALLBACK_BACKOFF = 2.0
# Hosts for which plain HTTP is tolerated: the API secret never leaves the box.
_DEBUG_BODY_LIMIT = 2000

# HTTP methods that never mutate server state. A read-only profile is allowed
# exactly these; anything else is refused before it reaches the wire.
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _auth_header(token_type: str, token: str) -> str:
    """The Authorization header value for a credential.

    ``"token"`` -> ``token key:secret`` (API key/secret).
    ``"bearer"`` -> ``Bearer <access_token>`` (OAuth).
    """
    if token_type == "bearer":
        return f"Bearer {token}"
    return f"token {token}"


class _LegacyBearerProvider:
    """Adapter for the pre-provider ``on_unauthorized`` constructor API."""

    def __init__(
        self, token: str, on_unauthorized: Callable[[], str | None] | None
    ) -> None:
        self._token = token
        self._on_unauthorized = on_unauthorized

    def authorization_header(self) -> str:
        return _auth_header("bearer", self._token)

    def refresh(self) -> bool:
        if self._on_unauthorized is None:
            return False
        token = self._on_unauthorized()
        if token is None:
            return False
        self._token = token
        return True


class FrappeTransport:
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
        credential_provider: CredentialProvider | None = None,
    ):
        site_url = SiteURL.parse(site)
        self.site = str(site_url)
        self.debug = debug
        self.read_only = read_only
        if credential_provider is None:
            if token_type == "bearer":
                credential_provider = _LegacyBearerProvider(token, on_unauthorized)
            else:
                key, _, secret = token.partition(":")
                credential_provider = ApiKeyProvider(key, secret)
        self._credential_provider = credential_provider

        # Refuse to put the credential on the wire in cleartext. Plain HTTP is
        # only allowed for local development (localhost / *.localhost / loopback).
        try:
            site_url.require_secure_credentials()
        except ValueError:
            raise FrappeError(
                f"Refusing to talk to {self.site} over plain HTTP: the "
                "credential would be sent in cleartext. Use an https:// URL "
                "(or a localhost address for local development)."
            )

        self._http = httpx.Client(
            base_url=self.site,
            headers={
                "Authorization": credential_provider.authorization_header(),
                "Accept": "application/json",
                "User-Agent": "frappectl",
            },
            timeout=timeout,
            # httpx strips the Authorization header on cross-origin redirects,
            # so the credential is never handed to a host we did not configure.
            follow_redirects=True,
            event_hooks=(
                {"request": [self._log_request], "response": [self._log_response]}
                if debug
                else {}
            ),
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "FrappeTransport":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

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
        for trace in _server_tracebacks(body):
            self._dbg("  [server traceback]")
            for line in _strip_control(trace).splitlines():
                self._dbg(f"    {line}")

    def _server_error(
        self, message: str, status_code: int | None, body: Any
    ) -> FrappeError:
        """Build a :class:`FrappeError`, emitting the server traceback first.

        Under ``--debug`` the full traceback is printed to stderr here; when it
        is off but the body carried one, the error is flagged so the print site
        can nudge the caller to re-run with ``--debug``.
        """
        self._emit_server_error(body)
        return FrappeError(
            message,
            status_code,
            has_server_exception=not self.debug and bool(_server_tracebacks(body)),
        )

    def _try_refresh(self) -> bool:
        """Obtain a fresh token via the refresh callback and re-arm the header.

        Returns True when a new token was installed, so the caller can replay
        the request. A no-op (returns False) when there is no callback or the
        refresh failed — the original 401 then surfaces unchanged.
        """
        if not self._credential_provider.refresh():
            return False
        self._http.headers["Authorization"] = (
            self._credential_provider.authorization_header()
        )
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
        resp = self.send(
            method,
            path,
            params=_clean_params(params),
            json=json_body,
            data=data,
            files=files,
        )
        return self.handle_response(resp)

    def send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send one policy-checked request and return its undecoded response."""
        if self.read_only and method.upper() not in _SAFE_METHODS:
            raise FrappeError(
                f"Refusing to send a {method.upper()} request: this profile is "
                "read-only. Only GET, HEAD, and OPTIONS requests are permitted. Use a "
                "writable profile, or pass -X GET for a whitelisted read method."
            )
        try:
            return self._send(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise FrappeError(f"Could not reach {self.site}: {e}") from e

    def handle_response(self, response: httpx.Response) -> Any:
        """Decode one response through the shared Frappe error policy."""
        return self._handle(response)

    def _handle(self, resp: httpx.Response) -> Any:
        body: Any = None
        if resp.content:
            try:
                body = resp.json()
            except (json.JSONDecodeError, ValueError):
                body = resp.text

        if resp.status_code >= 400:
            if resp.status_code == 401:
                raise self._server_error(
                    "Authentication failed (401). Check the API key/secret for "
                    "this site.",
                    401,
                    body,
                )
            if resp.status_code == 403:
                msg = extract_message(body, 403)
                raise self._server_error(
                    msg if msg != "HTTP 403" else "Permission denied (403).", 403, body
                )
            raise self._server_error(
                extract_message(body, resp.status_code), resp.status_code, body
            )

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
                    raise self._server_error(
                        extract_message(body, resp.status_code),
                        resp.status_code,
                        body,
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

    def request_page(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> tuple[list[Document], bool]:
        """Return a paginated response without discarding its page marker."""
        try:
            resp = self._send("GET", path, params=_clean_params(params))
        except httpx.HTTPError as e:
            raise FrappeError(f"Could not reach {self.site}: {e}") from e
        if resp.status_code >= 400:
            body = _safe_json(resp)
            raise self._server_error(
                extract_message(body, resp.status_code), resp.status_code, body
            )
        body = resp.json()
        self._emit_server_debug(body)
        rows = cast("list[Document]", body.get("data", []))
        return rows, bool(body.get("has_next_page"))

    def request_envelope_data(self, method: str, path: str) -> Any:
        """Return data only when the server sent a trusted JSON envelope."""
        try:
            resp = self._send(method, path)
        except httpx.HTTPError as e:
            raise FrappeError(f"Could not reach {self.site}: {e}") from e
        self._handle(resp)
        body = _safe_json(resp)
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        raise FrappeError("The server returned an unexpected response envelope.")


def _server_tracebacks(body: Any) -> list[str]:
    """The full server tracebacks carried by a v2 error body, if any.

    A v2 error body is ``{"errors": [{"type", "message", "exception", ...}]}``
    where ``exception`` (or ``exc``) is the full server traceback.
    """
    if not isinstance(body, dict):
        return []
    errors = body.get("errors")
    if not isinstance(errors, list):
        return []
    traces: list[str] = []
    for err in errors:
        if not isinstance(err, dict):
            continue
        trace = err.get("exception") or err.get("exc")
        if isinstance(trace, str) and trace.strip():
            traces.append(trace)
    return traces


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
