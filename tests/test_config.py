import time

import pytest

from frappectl.config import ConfigError
from frappectl.oauth import Tokens


def test_env_wins(fake_config, monkeypatch):
    monkeypatch.setenv("FRAPPE_SITE", "erp.example.com")
    monkeypatch.setenv("FRAPPE_API_KEY", "k")
    monkeypatch.setenv("FRAPPE_API_SECRET", "s")
    creds = fake_config.resolve()
    assert creds.source == "env"
    assert creds.site == "https://erp.example.com"  # normalized
    assert creds.token == "k:s"


def test_env_incomplete_errors(fake_config, monkeypatch):
    monkeypatch.setenv("FRAPPE_SITE", "erp.example.com")
    monkeypatch.delenv("FRAPPE_API_KEY", raising=False)
    with pytest.raises(ConfigError):
        fake_config.resolve()


def test_profile_roundtrip(fake_config):
    fake_config.add_profile("acme", "http://acme.test", "key1", "sec1")
    profiles, default = fake_config.list_profiles()
    assert default == "acme"
    assert profiles["acme"]["site"] == "http://acme.test"

    creds = fake_config.resolve()
    assert creds.source == "acme"
    assert creds.token == "key1:sec1"


def test_explicit_profile_overrides_env(fake_config, monkeypatch):
    fake_config.add_profile("acme", "http://acme.test", "k1", "s1")
    monkeypatch.setenv("FRAPPE_SITE", "http://other.test")
    monkeypatch.setenv("FRAPPE_API_KEY", "ek")
    monkeypatch.setenv("FRAPPE_API_SECRET", "es")
    # No profile arg -> env wins.
    assert fake_config.resolve().source == "env"
    # Explicit profile -> profile wins.
    assert fake_config.resolve("acme").source == "acme"


def test_unknown_profile(fake_config):
    with pytest.raises(ConfigError):
        fake_config.resolve("ghost")


def test_no_config_no_env(fake_config):
    with pytest.raises(ConfigError):
        fake_config.resolve()


def test_non_interactive_single_profile_ok(fake_config):
    # A lone authenticated profile is unambiguous, so it is used everywhere.
    fake_config.add_profile("acme", "http://acme.test", "k", "s")
    assert fake_config.resolve(interactive=True).source == "acme"
    assert fake_config.resolve(interactive=False).source == "acme"


def test_non_interactive_multiple_profiles_require_explicit(fake_config):
    # With more than one profile, the default is honoured interactively but
    # agents/scripts must name the site.
    fake_config.add_profile("acme", "http://acme.test", "k", "s")
    fake_config.add_profile("beta", "http://beta.test", "k", "s")
    fake_config.set_default("acme")
    assert fake_config.resolve(interactive=True).source == "acme"
    with pytest.raises(ConfigError):
        fake_config.resolve(interactive=False)


def test_non_interactive_explicit_profile_ok(fake_config):
    fake_config.add_profile("acme", "http://acme.test", "k", "s")
    assert fake_config.resolve("acme", interactive=False).source == "acme"


def test_non_interactive_env_ok(fake_config, monkeypatch):
    monkeypatch.setenv("FRAPPE_SITE", "erp.example.com")
    monkeypatch.setenv("FRAPPE_API_KEY", "k")
    monkeypatch.setenv("FRAPPE_API_SECRET", "s")
    assert fake_config.resolve(interactive=False).source == "env"


def test_first_profile_always_default(fake_config):
    # The very first profile becomes the default even without make_default,
    # so a single-profile setup keeps working out of the box.
    fake_config.add_profile("a", "http://a.test", "k", "s", make_default=False)
    _, default = fake_config.list_profiles()
    assert default == "a"


def test_new_profile_does_not_steal_default(fake_config):
    # Authenticating another site must not silently hijack the default.
    fake_config.add_profile("a", "http://a.test", "k", "s")
    fake_config.add_profile("b", "http://b.test", "k", "s", make_default=False)
    _, default = fake_config.list_profiles()
    assert default == "a"


def test_remove_and_default_shift(fake_config):
    fake_config.add_profile("a", "http://a.test", "k", "s")
    fake_config.add_profile("b", "http://b.test", "k", "s")
    fake_config.set_default("a")
    fake_config.remove_profile("a")
    _, default = fake_config.list_profiles()
    assert default == "b"


def test_description_roundtrip(fake_config):
    fake_config.add_profile(
        "acme", "http://acme.test", "k", "s", description="prod billing"
    )
    profiles, _ = fake_config.list_profiles()
    assert profiles["acme"]["description"] == "prod billing"
    assert fake_config.resolve().description == "prod billing"


def test_set_description_add_and_clear(fake_config):
    fake_config.add_profile("acme", "http://acme.test", "k", "s")
    fake_config.set_description("acme", "staging box")
    assert fake_config.list_profiles()[0]["acme"]["description"] == "staging box"
    # Empty string clears the key entirely rather than storing "".
    fake_config.set_description("acme", "")
    assert "description" not in fake_config.list_profiles()[0]["acme"]


def test_set_description_unknown_profile(fake_config):
    with pytest.raises(ConfigError):
        fake_config.set_description("ghost", "x")


def test_rename_profile_moves_secret_and_default(fake_config):
    fake_config.add_profile("acme", "http://acme.test", "k", "s", description="d")
    fake_config.rename_profile("acme", "prod")
    profiles, default = fake_config.list_profiles()
    assert "acme" not in profiles
    assert profiles["prod"]["site"] == "http://acme.test"
    assert profiles["prod"]["description"] == "d"
    assert default == "prod"
    # Secret follows the rename, and the resolved profile works under the new name.
    assert fake_config.resolve("prod").token == "k:s"


def test_legacy_keyring_secret_migrates_on_read(fake_config):
    # A profile created before the frappectl rename: config entry exists but
    # the secret sits under the old keyring service name.
    fake_config.add_profile("acme", "http://acme.test", "k", "s")
    kr = fake_config._keyring()
    kr.store[("frappe-cli", "acme")] = kr.store.pop(("frappectl", "acme"))

    # The first read finds it via the legacy fallback...
    assert fake_config.resolve("acme").token == "k:s"
    # ...and migrates it forward, leaving nothing under the old service.
    assert kr.store.get(("frappectl", "acme")) == "k:s"
    assert ("frappe-cli", "acme") not in kr.store


def test_delete_profile_clears_legacy_secret(fake_config):
    fake_config.add_profile("acme", "http://acme.test", "k", "s")
    kr = fake_config._keyring()
    kr.store[("frappe-cli", "acme")] = "k:s"
    fake_config.remove_profile("acme")
    assert ("frappe-cli", "acme") not in kr.store
    assert ("frappectl", "acme") not in kr.store


def test_rename_profile_only_updates_default_when_it_was_default(fake_config):
    fake_config.add_profile("a", "http://a.test", "k", "s")
    fake_config.add_profile("b", "http://b.test", "k", "s", make_default=False)
    fake_config.rename_profile("b", "beta")
    _, default = fake_config.list_profiles()
    assert default == "a"


def test_rename_profile_rejects_existing_name(fake_config):
    fake_config.add_profile("a", "http://a.test", "k", "s")
    fake_config.add_profile("b", "http://b.test", "k", "s")
    with pytest.raises(ConfigError):
        fake_config.rename_profile("a", "b")


def test_rename_unknown_profile(fake_config):
    with pytest.raises(ConfigError):
        fake_config.rename_profile("ghost", "x")


def test_read_only_stored_and_resolved(fake_config):
    fake_config.add_profile("acme", "http://acme.test", "k", "s", read_only=True)
    profiles, _ = fake_config.list_profiles()
    assert profiles["acme"]["read_only"] is True
    assert fake_config.resolve("acme").read_only is True


def test_set_read_only_toggles(fake_config):
    fake_config.add_profile("acme", "http://acme.test", "k", "s")
    fake_config.set_read_only("acme", True)
    assert fake_config.resolve("acme").read_only is True
    fake_config.set_read_only("acme", False)
    assert fake_config.resolve("acme").read_only is False
    # Cleared flag is not left dangling in the file.
    profiles, _ = fake_config.list_profiles()
    assert "read_only" not in profiles["acme"]


def test_set_read_only_unknown_profile(fake_config):
    with pytest.raises(ConfigError):
        fake_config.set_read_only("ghost", True)


def test_env_read_only_flag(fake_config, monkeypatch):
    monkeypatch.setenv("FRAPPE_SITE", "erp.example.com")
    monkeypatch.setenv("FRAPPE_API_KEY", "k")
    monkeypatch.setenv("FRAPPE_API_SECRET", "s")
    assert fake_config.resolve().read_only is False
    monkeypatch.setenv("FRAPPE_READ_ONLY", "1")
    assert fake_config.resolve().read_only is True


# --- OAuth profiles --------------------------------------------------------


def _tokens(access="AT", refresh="RT", ttl=3600):
    return Tokens(
        access_token=access,
        refresh_token=refresh,
        expires_at=time.time() + ttl,
        token_type="bearer",
    )


def test_oauth_profile_roundtrip(fake_config):
    fake_config.add_oauth_profile(
        "acme", "http://acme.test", "client-1", _tokens(), description="prod"
    )
    profiles, default = fake_config.list_profiles()
    assert default == "acme"
    assert profiles["acme"]["auth"] == "oauth"
    # The config file holds no secrets, only the site + tag.
    assert "AT" not in fake_config.config_path().read_text()

    creds = fake_config.resolve()
    assert creds.token_type == "bearer"
    assert creds.access_token == "AT"
    assert creds.refresh_token == "RT"
    assert creds.client_id == "client-1"
    assert creds.wire_token == "AT"
    assert creds.description == "prod"


def test_oauth_client_id_and_access_token_helpers(fake_config):
    fake_config.add_oauth_profile("acme", "http://acme.test", "client-1", _tokens())
    assert fake_config.oauth_client_id("acme") == "client-1"
    assert fake_config.oauth_access_token("acme") == "AT"
    # An API-key profile has no OAuth blob to read.
    fake_config.add_profile("keys", "http://keys.test", "k", "s")
    assert fake_config.oauth_client_id("keys") is None


def test_oauth_resolve_refreshes_when_expired(fake_config, monkeypatch):
    fake_config.add_oauth_profile(
        "acme", "http://acme.test", "client-1", _tokens(ttl=-10)
    )

    called = {}

    def fake_refresh(site, client_id, refresh_token):
        called["args"] = (site, client_id, refresh_token)
        return _tokens(access="AT2", refresh="RT2")

    from frappectl import oauth

    monkeypatch.setattr(oauth, "refresh", fake_refresh)

    creds = fake_config.resolve()
    # Refreshed token is returned and persisted for the next command.
    assert creds.access_token == "AT2"
    assert called["args"] == ("http://acme.test", "client-1", "RT")
    assert fake_config.oauth_access_token("acme") == "AT2"
    assert fake_config.oauth_client_id("acme") == "client-1"  # preserved


def test_oauth_resolve_no_refresh_when_fresh(fake_config, monkeypatch):
    fake_config.add_oauth_profile(
        "acme", "http://acme.test", "client-1", _tokens(ttl=3600)
    )

    from frappectl import oauth

    def boom(*a, **k):
        raise AssertionError("should not refresh a fresh token")

    monkeypatch.setattr(oauth, "refresh", boom)
    assert fake_config.resolve().access_token == "AT"


def test_oauth_refresh_failure_surfaces_as_config_error(fake_config, monkeypatch):
    fake_config.add_oauth_profile(
        "acme", "http://acme.test", "client-1", _tokens(ttl=-10)
    )
    from frappectl import oauth

    def fail_refresh(*a, **k):
        raise oauth.OAuthError("token expired")

    monkeypatch.setattr(oauth, "refresh", fail_refresh)
    with pytest.raises(ConfigError, match="refresh"):
        fake_config.resolve()


def test_update_oauth_tokens_preserves_refresh_when_absent(fake_config):
    fake_config.add_oauth_profile("acme", "http://acme.test", "client-1", _tokens())
    # A refresh response with no new refresh token keeps the stored one.
    fake_config.update_oauth_tokens(
        "acme",
        Tokens(access_token="AT2", refresh_token="", expires_at=time.time() + 3600),
    )
    creds = fake_config.resolve()
    assert creds.access_token == "AT2"
    assert creds.refresh_token == "RT"


def test_api_key_profile_stays_token_type(fake_config):
    fake_config.add_profile("acme", "http://acme.test", "k", "s")
    creds = fake_config.resolve()
    assert creds.token_type == "token"
    assert creds.wire_token == "k:s"
