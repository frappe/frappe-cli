import pytest

from frappe_cli.config import ConfigError


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


def test_config_has_no_secret(fake_config):
    fake_config.add_profile("a", "http://a.test", "key", "supersecret")
    text = fake_config.config_path().read_text()
    assert "supersecret" not in text
    assert "key" not in text or "key" in '"site"'  # only the word in JSON keys
