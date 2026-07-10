"""OAuth 2.0 login engine — Authorization Code + PKCE, public client.

This is the interactive, browser-driven auth path. It is deliberately separate
from the API-key path in :mod:`config`: OAuth is an added method, never a
replacement.

The OAuth *mechanics* — PKCE (S256), the authorization-code exchange and refresh
— run through Authlib's :class:`~authlib.integrations.httpx_client.OAuth2Client`,
so we don't hand-roll security-sensitive protocol code. What Authlib does not
cover is Frappe-specific and lives here:

1. **Discover** the authorization-server metadata
   (``/.well-known/oauth-authorization-server``), falling back to Frappe's known
   endpoint paths when discovery 404s.
2. **Resolve a client_id.** If the metadata advertises a registration endpoint,
   dynamically register a *public* client (RFC 7591) whose one redirect URI is
   our fixed loopback URL, and hand the ``client_id`` back to the caller to
   persist. Otherwise the caller must supply a pre-registered public client id.
3. Catch the browser **redirect** on a one-shot loopback server.

Frappe matches redirect URIs byte-for-byte (it does *not* honour RFC 8252's
"any loopback port" rule), so we use a **fixed** loopback port and register that
exact URI.
"""

from __future__ import annotations

import secrets
import time
import urllib.parse
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, cast

import httpx
from authlib.common.errors import AuthlibBaseError
from authlib.integrations.httpx_client import OAuth2Client

from .client import _is_local_host

# Fixed loopback redirect. The port is fixed (not ephemeral) because Frappe
# validates the redirect URI exactly; an ephemeral port would never match the
# registered client. It is overridable (``--oauth-port``) for the rare clash.
DEFAULT_LOOPBACK_PORT = 9876
_LOOPBACK_HOST = "127.0.0.1"
_CALLBACK_PATH = "/callback"

# How long to wait for the user to finish the browser leg before giving up.
LOGIN_TIMEOUT = 300.0  # seconds
_POLL_INTERVAL = 1.0  # seconds; how often the loopback server wakes to re-check

# Scopes: ``openid`` lets us confirm identity via the userinfo endpoint;
# ``all`` grants the same API surface an API key would.
_SCOPE = "openid all"

_HTTP_TIMEOUT = 30.0

# Fallback endpoint paths, used only if discovery 404s. Mirrors
# ``frappe/integrations/oauth2.py`` ENDPOINTS.
_FALLBACK_ENDPOINTS = {
    "authorization": "/api/method/frappe.integrations.oauth2.authorize",
    "token": "/api/method/frappe.integrations.oauth2.get_token",
    "revocation": "/api/method/frappe.integrations.oauth2.revoke_token",
    "userinfo": "/api/method/frappe.integrations.oauth2.openid_profile",
    "registration": "/api/method/frappe.integrations.oauth2.register_client",
}


class OAuthError(Exception):
    """An OAuth login / refresh failure, reduced to a clean message."""


def _require_secure_transport(site: str) -> None:
    """Refuse OAuth over plain HTTP to a remote host.

    Access and refresh tokens are bearer credentials — anyone who sees them can
    act as the user. Plain HTTP is tolerated only for local development
    (localhost / ``*.localhost`` / loopback), mirroring the API-key client.
    """
    parsed = httpx.URL(site)
    if parsed.scheme == "http" and not _is_local_host(parsed.host):
        raise OAuthError(
            f"Refusing to run OAuth against {site} over plain HTTP: the access "
            "and refresh tokens would be sent in cleartext. Use an https:// URL "
            "(http is allowed only for local development)."
        )


@dataclass
class Tokens:
    """The tokens returned by the authorization server."""

    access_token: str
    refresh_token: str
    expires_at: float  # epoch seconds
    token_type: str = "bearer"

    def with_refresh_token(self, refresh_token: str) -> "Tokens":
        return replace(self, refresh_token=refresh_token)


@dataclass
class Metadata:
    """The subset of authorization-server metadata we use."""

    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str
    userinfo_endpoint: str
    # Empty when the site does not offer dynamic client registration.
    registration_endpoint: str = ""


# --- discovery -------------------------------------------------------------


def discover(site: str) -> Metadata:
    """Fetch authorization-server metadata for ``site``.

    Falls back to Frappe's known endpoint table if the well-known document is
    absent (404). Any other transport/HTTP failure is surfaced as an
    :class:`OAuthError`.
    """
    site = site.rstrip("/")
    _require_secure_transport(site)
    url = f"{site}/.well-known/oauth-authorization-server"
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as e:
        raise OAuthError(f"Could not reach {site} for OAuth discovery: {e}") from e

    if resp.status_code == 404:
        return _fallback_metadata(site)
    if resp.status_code >= 400:
        raise OAuthError(f"OAuth discovery failed at {url}: HTTP {resp.status_code}.")
    try:
        data = resp.json()
    except ValueError as e:
        raise OAuthError(f"OAuth discovery at {url} returned invalid JSON.") from e
    if not isinstance(data, dict):
        raise OAuthError(f"OAuth discovery at {url} returned an unexpected document.")

    try:
        return Metadata(
            authorization_endpoint=str(data["authorization_endpoint"]),
            token_endpoint=str(data["token_endpoint"]),
            revocation_endpoint=str(
                data.get("revocation_endpoint")
                or f"{site}{_FALLBACK_ENDPOINTS['revocation']}"
            ),
            userinfo_endpoint=str(
                data.get("userinfo_endpoint")
                or f"{site}{_FALLBACK_ENDPOINTS['userinfo']}"
            ),
            registration_endpoint=str(data.get("registration_endpoint") or ""),
        )
    except KeyError as e:
        raise OAuthError(
            f"OAuth discovery at {url} is missing a required endpoint: {e}."
        ) from e


def _fallback_metadata(site: str) -> Metadata:
    return Metadata(
        authorization_endpoint=f"{site}{_FALLBACK_ENDPOINTS['authorization']}",
        token_endpoint=f"{site}{_FALLBACK_ENDPOINTS['token']}",
        revocation_endpoint=f"{site}{_FALLBACK_ENDPOINTS['revocation']}",
        userinfo_endpoint=f"{site}{_FALLBACK_ENDPOINTS['userinfo']}",
        # Discovery is what advertises DCR; without it we cannot assume the
        # registration endpoint is enabled, so leave it empty and fall back to a
        # configured client_id.
        registration_endpoint="",
    )


# --- loopback server -------------------------------------------------------


class _LoopbackServer(HTTPServer):
    """One-shot server that captures the OAuth redirect query parameters."""

    result: dict[str, str] | None = None
    callback_path: str = _CALLBACK_PATH


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        server = cast("_LoopbackServer", self.server)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != server.callback_path:
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        flat = {k: v[0] for k, v in params.items() if v}
        server.result = flat
        ok = "code" in flat and "error" not in flat
        body = _result_page(ok, flat.get("error"))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        # Silence the default stderr access log; it is noise for a CLI.
        pass


def _result_page(ok: bool, error: str | None) -> bytes:
    if ok:
        msg = "Login complete. You can close this tab and return to the terminal."
    else:
        detail = f" ({error})" if error else ""
        msg = f"Login failed{detail}. Return to the terminal for details."
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>frappe-cli</title></head>"
        f"<body style='font-family:sans-serif;padding:2rem'><p>{msg}</p></body></html>"
    )
    return html.encode("utf-8")


# --- login / refresh -------------------------------------------------------


def redirect_uri(port: int = DEFAULT_LOOPBACK_PORT) -> str:
    return f"http://{_LOOPBACK_HOST}:{port}{_CALLBACK_PATH}"


def _oauth_client(client_id: str, redirect: str) -> OAuth2Client:
    """A public (secret-less) PKCE-S256 client bound to our loopback redirect."""
    return OAuth2Client(
        client_id=client_id,
        token_endpoint_auth_method="none",
        scope=_SCOPE,
        redirect_uri=redirect,
        code_challenge_method="S256",
        timeout=_HTTP_TIMEOUT,
    )


def login(
    site: str,
    client_id: str | None = None,
    port: int = DEFAULT_LOOPBACK_PORT,
    *,
    open_browser: bool = True,
    timeout: float = LOGIN_TIMEOUT,
    announce: Callable[[str], None] | None = None,
) -> tuple[Tokens, str]:
    """Run the full authorization-code + PKCE flow. Return ``(tokens, client_id)``.

    ``client_id`` is resolved automatically: dynamic registration when the site
    advertises it, otherwise the supplied value. The resolved id is returned so
    the caller can persist it and reuse it on later logins.

    ``open_browser=False`` and the ``announce`` hook exist so the browser leg can
    be driven in tests (post the code to the loopback directly).
    """
    site = site.rstrip("/")
    meta = discover(site)
    redirect = redirect_uri(port)

    if client_id is None:
        if meta.registration_endpoint:
            client_id = register_client(meta, redirect)
        else:
            raise OAuthError(
                f"{site} does not advertise dynamic client registration and no "
                "client id was configured. Pass --client-id <id> for a public "
                "OAuth client already registered on the site with the redirect "
                f"URI {redirect}."
            )

    client = _oauth_client(client_id, redirect)
    verifier = secrets.token_urlsafe(48)
    authorize_url, state = client.create_authorization_url(
        meta.authorization_endpoint, code_verifier=verifier
    )

    result = _await_redirect(
        port,
        authorize_url,
        open_browser=open_browser,
        timeout=timeout,
        announce=announce,
    )
    if result.get("error"):
        raise OAuthError(_redirect_error(result))

    # Rebuild the redirect response Authlib expects; it validates ``state`` and
    # extracts the code, so we don't re-implement either.
    authorization_response = f"{redirect}?{urllib.parse.urlencode(result)}"
    try:
        token = client.fetch_token(
            meta.token_endpoint,
            authorization_response=authorization_response,
            code_verifier=verifier,
            state=state,
        )
    except AuthlibBaseError as e:
        raise OAuthError(f"Token exchange failed: {e}") from e
    except httpx.HTTPError as e:
        raise OAuthError(f"Could not reach the token endpoint: {e}") from e

    return _tokens_from(token), client_id


def _await_redirect(
    port: int,
    authorize_url: str,
    *,
    open_browser: bool,
    timeout: float,
    announce: Callable[[str], None] | None,
) -> dict[str, str]:
    try:
        server = _LoopbackServer((_LOOPBACK_HOST, port), _CallbackHandler)
    except OSError as e:
        raise OAuthError(
            f"Could not bind the loopback port {port} for the OAuth redirect "
            f"({e}). Close whatever is using it, or pass --oauth-port <port> "
            "(the port must match a redirect URI registered on the site)."
        ) from e

    server.result = None
    server.timeout = _POLL_INTERVAL
    try:
        if announce:
            announce(authorize_url)
        if open_browser:
            webbrowser.open(authorize_url)
        deadline = time.monotonic() + timeout
        while server.result is None and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()

    if server.result is None:
        raise OAuthError(
            "Timed out waiting for the authorization redirect. Complete the login "
            "in the browser, or retry."
        )
    return server.result


def refresh(site: str, client_id: str, refresh_token: str) -> Tokens:
    """Exchange a refresh token for a fresh access token."""
    if not refresh_token:
        raise OAuthError("No refresh token available; log in again.")
    site = site.rstrip("/")
    meta = discover(site)
    client = _oauth_client(client_id, redirect_uri())
    try:
        token = client.refresh_token(meta.token_endpoint, refresh_token=refresh_token)
    except AuthlibBaseError as e:
        raise OAuthError(f"Token refresh failed: {e}") from e
    except httpx.HTTPError as e:
        raise OAuthError(f"Could not reach the token endpoint: {e}") from e
    return _tokens_from(token)


def register_client(meta: Metadata, redirect: str) -> str:
    """Dynamically register a public client (RFC 7591). Return its ``client_id``.

    The client is public (``token_endpoint_auth_method="none"``) so no secret is
    ever issued or stored, and it is scoped to exactly our loopback redirect URI.
    """
    if not meta.registration_endpoint:
        raise OAuthError("This site does not offer dynamic client registration.")
    body = {
        "client_name": "frappe-cli",
        "redirect_uris": [redirect],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "scope": _SCOPE,
    }
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.post(
                meta.registration_endpoint,
                json=body,
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as e:
        raise OAuthError(f"Could not reach the registration endpoint: {e}") from e

    payload = _json_or_none(resp)
    if resp.status_code >= 400:
        raise OAuthError(_http_error(payload, resp.status_code))
    if not isinstance(payload, dict) or not payload.get("client_id"):
        raise OAuthError("Client registration returned no client_id.")
    return str(payload["client_id"])


def revoke(site: str, token: str) -> None:
    """Best-effort revocation of a token on logout. Never raises."""
    if not token:
        return
    try:
        meta = discover(site.rstrip("/"))
        with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
            client.post(meta.revocation_endpoint, data={"token": token})
    except (httpx.HTTPError, OAuthError):
        # Logout should always succeed locally even if the server is unreachable.
        pass


# --- helpers ---------------------------------------------------------------


def _tokens_from(token: Any) -> Tokens:
    access_token = str(token.get("access_token") or "")
    if not access_token:
        raise OAuthError("The token endpoint returned no access token.")
    try:
        expires_at = float(token.get("expires_at") or (time.time() + 3600))
    except (TypeError, ValueError):
        expires_at = time.time() + 3600
    return Tokens(
        access_token=access_token,
        refresh_token=str(token.get("refresh_token") or ""),
        expires_at=expires_at,
        token_type=str(token.get("token_type") or "bearer").lower(),
    )


def _redirect_error(result: dict[str, str]) -> str:
    err = result.get("error", "")
    desc = result.get("error_description")
    if desc:
        return f"Authorization was denied: {err} — {desc}"
    return f"Authorization was denied: {err}"


def _json_or_none(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


def _http_error(payload: Any, status_code: int) -> str:
    if isinstance(payload, dict):
        err = payload.get("error")
        desc = payload.get("error_description")
        if err and desc:
            return f"{err}: {desc}"
        if err:
            return str(err)
    return f"OAuth request failed with HTTP {status_code}."
