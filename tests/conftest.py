import pytest


class FakeKeyring:
    """In-memory stand-in for the OS keyring."""

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service, user, password):
        self.store[(service, user)] = password

    def get_password(self, service, user):
        return self.store.get((service, user))

    def delete_password(self, service, user):
        self.store.pop((service, user), None)


@pytest.fixture
def fake_config(tmp_path, monkeypatch):
    """Isolated config dir + in-memory keyring."""
    from frappe_cli import config

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for var in ("FRAPPE_SITE", "FRAPPE_API_KEY", "FRAPPE_API_SECRET"):
        monkeypatch.delenv(var, raising=False)
    kr = FakeKeyring()
    monkeypatch.setattr(config, "_keyring", lambda: kr)
    return config
