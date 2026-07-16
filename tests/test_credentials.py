import pytest

from frappectl.config import OAuthCredential
from frappectl.credentials import (
    ApiKeyProvider,
    CredentialRefreshError,
    OAuthProvider,
)


def credential(access="AT", refresh="RT", expires=200, client_id="client"):
    return OAuthCredential(access, refresh, expires, client_id)


def test_api_key_provider_never_refreshes():
    provider = ApiKeyProvider("key", "secret")
    assert provider.authorization_header() == "token key:secret"
    assert provider.refresh() is False


def test_oauth_provider_refreshes_before_expiry_and_persists():
    persisted = []
    provider = OAuthProvider(
        "https://site.test",
        credential(expires=100),
        lambda site, client_id, refresh: credential("AT2", "RT2", 500, client_id),
        persisted.append,
        clock=lambda: 50,
    )

    assert provider.authorization_header() == "Bearer AT2"
    assert persisted == [credential("AT2", "RT2", 500)]


def test_oauth_provider_keeps_refreshed_token_when_persistence_fails():
    def fail_persist(_credential):
        raise OSError("keyring unavailable")

    provider = OAuthProvider(
        "https://site.test",
        credential(expires=500),
        lambda site, client_id, refresh: credential("AT2", "RT2", 600, client_id),
        fail_persist,
        clock=lambda: 0,
    )

    assert provider.refresh() is True
    assert provider.authorization_header() == "Bearer AT2"


def test_oauth_provider_hides_refresh_failure_details():
    def fail_refresh(site, client_id, refresh):
        raise RuntimeError(f"server rejected {refresh}")

    provider = OAuthProvider(
        "https://site.test",
        credential(expires=10),
        fail_refresh,
        lambda value: None,
        clock=lambda: 10,
    )

    with pytest.raises(CredentialRefreshError) as exc:
        provider.authorization_header()
    assert "RT" not in str(exc.value)
