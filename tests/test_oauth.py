"""OAuth login engine: discovery, PKCE wiring, exchange, refresh, and DCR.

The browser leg is driven by posting the authorization code straight to the
loopback server (no real browser), exactly as the plan describes. respx mocks
the httpx-backed server calls; the loopback callback is hit with urllib so it
bypasses respx and reaches the real one-shot server.
"""

from __future__ import annotations

import threading
import time
import urllib.parse
import urllib.request

import httpx
import pytest
import respx

from frappectl import oauth

SITE = "https://site.test"
WELL_KNOWN = f"{SITE}/.well-known/oauth-authorization-server"
AUTHORIZE = f"{SITE}/authorize"
TOKEN = f"{SITE}/token"
REGISTER = f"{SITE}/register"
REVOKE = f"{SITE}/revoke"

# Each test uses a distinct loopback port to avoid TIME_WAIT rebind races.
_next_port = iter(range(9840, 9899))


def _metadata(*, with_registration: bool = True) -> dict[str, str]:
    doc = {
        "authorization_endpoint": AUTHORIZE,
        "token_endpoint": TOKEN,
        "revocation_endpoint": REVOKE,
    }
    if with_registration:
        doc["registration_endpoint"] = REGISTER
    return doc


def _token_response(**overrides: object) -> httpx.Response:
    body = {
        "access_token": "AT",
        "refresh_token": "RT",
        "expires_in": 3600,
        "token_type": "Bearer",
        "scope": "openid all",
    }
    body.update(overrides)
    return httpx.Response(200, json=body)


def _drive_login(**login_kwargs: object) -> tuple[oauth.Tokens, str, dict[str, str]]:
    """Run oauth.login, posting the code to the loopback once it is listening.

    Returns ``(tokens, client_id, captured)`` where ``captured`` holds the
    authorize URL query params so tests can assert on PKCE etc.
    """
    port = next(_next_port)
    captured: dict[str, str] = {}

    def announce(url: str) -> None:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        captured.update({k: v[0] for k, v in params.items()})

        def post() -> None:
            # Give the server loop a moment to start handling requests.
            time.sleep(0.2)
            cb = f"http://127.0.0.1:{port}/callback?" + urllib.parse.urlencode(
                {"code": "AUTHCODE", "state": captured["state"]}
            )
            urllib.request.urlopen(cb, timeout=5).read()  # noqa: S310 (loopback)

        threading.Thread(target=post, daemon=True).start()

    tokens, client_id = oauth.login(
        SITE,
        port=port,
        open_browser=False,
        announce=announce,
        timeout=10,
        **login_kwargs,  # type: ignore[arg-type]
    )
    return tokens, client_id, captured


@respx.mock
def test_discover_reads_metadata():
    respx.get(WELL_KNOWN).mock(return_value=httpx.Response(200, json=_metadata()))
    meta = oauth.discover(SITE)
    assert meta.authorization_endpoint == AUTHORIZE
    assert meta.token_endpoint == TOKEN
    assert meta.registration_endpoint == REGISTER


@respx.mock
def test_discover_falls_back_on_404():
    respx.get(WELL_KNOWN).mock(return_value=httpx.Response(404))
    meta = oauth.discover(SITE)
    assert meta.token_endpoint.endswith("frappe.integrations.oauth2.get_token")
    assert meta.authorization_endpoint.endswith("frappe.integrations.oauth2.authorize")
    assert meta.registration_endpoint == ""


@respx.mock
def test_discover_raises_on_server_error():
    respx.get(WELL_KNOWN).mock(return_value=httpx.Response(500))
    with pytest.raises(oauth.OAuthError):
        oauth.discover(SITE)


def test_refuses_oauth_over_plain_http_remote():
    with pytest.raises(oauth.OAuthError, match="cleartext"):
        oauth.discover("http://erp.example.com")


@respx.mock
def test_allows_plain_http_on_localhost():
    well_known = "http://dev.localhost:8000/.well-known/oauth-authorization-server"
    respx.get(well_known).mock(
        return_value=httpx.Response(
            200,
            json={
                "authorization_endpoint": "http://dev.localhost:8000/authorize",
                "token_endpoint": "http://dev.localhost:8000/token",
            },
        )
    )
    meta = oauth.discover("http://dev.localhost:8000")
    assert meta.token_endpoint == "http://dev.localhost:8000/token"


@respx.mock
def test_login_dcr_registers_and_exchanges():
    respx.get(WELL_KNOWN).mock(return_value=httpx.Response(200, json=_metadata()))
    reg = respx.post(REGISTER).mock(
        return_value=httpx.Response(200, json={"client_id": "dcr-client"})
    )
    token_route = respx.post(TOKEN).mock(return_value=_token_response())

    tokens, client_id, captured = _drive_login()

    assert client_id == "dcr-client"
    assert tokens.access_token == "AT"
    assert tokens.refresh_token == "RT"
    assert tokens.token_type == "bearer"
    assert tokens.expires_at > time.time()
    assert reg.called
    assert captured["code_challenge_method"] == "S256"
    assert captured["code_challenge"]
    assert captured["response_type"] == "code"
    assert token_route.called


@respx.mock
def test_login_uses_configured_client_without_dcr():
    respx.get(WELL_KNOWN).mock(
        return_value=httpx.Response(200, json=_metadata(with_registration=False))
    )
    reg = respx.post(REGISTER).mock(return_value=httpx.Response(200))
    respx.post(TOKEN).mock(return_value=_token_response())

    tokens, client_id, _ = _drive_login(client_id="fixed-pub")

    assert client_id == "fixed-pub"
    assert tokens.access_token == "AT"
    assert not reg.called


@respx.mock
def test_login_without_client_or_dcr_errors():
    respx.get(WELL_KNOWN).mock(
        return_value=httpx.Response(200, json=_metadata(with_registration=False))
    )
    with pytest.raises(oauth.OAuthError, match="dynamic client registration"):
        oauth.login(SITE, port=next(_next_port), open_browser=False, timeout=5)


@respx.mock
def test_login_surfaces_token_error():
    respx.get(WELL_KNOWN).mock(return_value=httpx.Response(200, json=_metadata()))
    respx.post(REGISTER).mock(
        return_value=httpx.Response(200, json={"client_id": "dcr-client"})
    )
    respx.post(TOKEN).mock(
        return_value=httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "bad code"}
        )
    )
    with pytest.raises(oauth.OAuthError, match="invalid_grant"):
        _drive_login()


@respx.mock
def test_refresh_exchanges_refresh_token():
    respx.get(WELL_KNOWN).mock(return_value=httpx.Response(200, json=_metadata()))
    route = respx.post(TOKEN).mock(
        return_value=_token_response(access_token="AT2", refresh_token="RT2")
    )
    tokens = oauth.refresh(SITE, "dcr-client", "RT")
    assert tokens.access_token == "AT2"
    assert tokens.refresh_token == "RT2"
    body = route.calls.last.request.content.decode()
    assert "grant_type=refresh_token" in body
    assert "refresh_token=RT" in body


def test_refresh_without_token_errors():
    with pytest.raises(oauth.OAuthError):
        oauth.refresh(SITE, "cid", "")


@respx.mock
def test_register_client_posts_public_client():
    route = respx.post(REGISTER).mock(
        return_value=httpx.Response(201, json={"client_id": "new-client"})
    )
    meta = oauth.Metadata(
        authorization_endpoint=AUTHORIZE,
        token_endpoint=TOKEN,
        revocation_endpoint=REVOKE,
        userinfo_endpoint="",
        registration_endpoint=REGISTER,
    )
    redirect = oauth.redirect_uri(9876)
    client_id = oauth.register_client(meta, redirect)
    assert client_id == "new-client"
    sent = route.calls.last.request
    import json

    payload = json.loads(sent.content)
    assert payload["token_endpoint_auth_method"] == "none"
    assert payload["redirect_uris"] == [redirect]
    assert set(payload["grant_types"]) == {"authorization_code", "refresh_token"}


@respx.mock
def test_register_client_error_surfaces():
    respx.post(REGISTER).mock(
        return_value=httpx.Response(400, json={"error": "invalid_redirect_uri"})
    )
    meta = oauth.Metadata(AUTHORIZE, TOKEN, REVOKE, "", REGISTER)
    with pytest.raises(oauth.OAuthError, match="invalid_redirect_uri"):
        oauth.register_client(meta, oauth.redirect_uri())


@respx.mock
def test_revoke_posts_token_and_swallows_errors():
    respx.get(WELL_KNOWN).mock(return_value=httpx.Response(200, json=_metadata()))
    route = respx.post(REVOKE).mock(return_value=httpx.Response(200))
    oauth.revoke(SITE, "AT")
    assert route.called
    assert "token=AT" in route.calls.last.request.content.decode()


@respx.mock
def test_revoke_never_raises_on_failure():
    respx.get(WELL_KNOWN).mock(return_value=httpx.Response(500))
    oauth.revoke(SITE, "AT")
