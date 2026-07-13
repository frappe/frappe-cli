import httpx
import pytest
import respx
from typer.testing import CliRunner

from frappectl.cli import _hoist_globals, app
from frappectl.client import FrappeClient
from frappectl.commands import auth
from frappectl.errors import FrappeError

BASE = "http://localhost"
runner = CliRunner()


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["doc", "list", "ToDo", "--json"], ["--json", "doc", "list", "ToDo"]),
        (["-s", "raven", "doc", "list", "X"], ["--site", "raven", "doc", "list", "X"]),
        (["doc", "list", "X", "-s", "raven"], ["--site", "raven", "doc", "list", "X"]),
        (["doc", "delete", "ToDo", "x", "-y"], ["--yes", "doc", "delete", "ToDo", "x"]),
        (["--site=acme", "doc", "list", "X"], ["--site", "acme", "doc", "list", "X"]),
        # tokens after `--` are left alone
        (["api", "p", "--", "--json"], ["api", "p", "--", "--json"]),
        # substrings are not hoisted
        (
            ["doc", "list", "X", "--filters-json", "[]"],
            ["doc", "list", "X", "--filters-json", "[]"],
        ),
    ],
)
def test_hoist_globals(argv, expected):
    assert _hoist_globals(argv) == expected


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("FRAPPE_SITE", BASE)
    monkeypatch.setenv("FRAPPE_API_KEY", "k")
    monkeypatch.setenv("FRAPPE_API_SECRET", "s")


@respx.mock
def test_doc_get_json(env):
    respx.get(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(200, json={"data": {"name": "X", "status": "Open"}})
    )
    result = runner.invoke(app, ["--json", "doc", "get", "ToDo", "X"])
    assert result.exit_code == 0
    assert '"name": "X"' in result.stdout


@respx.mock
def test_doc_list_meta_driven_fields(env):
    respx.get(f"{BASE}/api/v2/doctype/ToDo/meta").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "title_field": "description",
                    "fields": [
                        {
                            "fieldname": "status",
                            "fieldtype": "Select",
                            "in_list_view": 1,
                        }
                    ],
                }
            },
        )
    )
    list_route = respx.get(f"{BASE}/api/v2/document/ToDo").mock(
        return_value=httpx.Response(
            200, json={"data": [{"name": "a"}], "has_next_page": False}
        )
    )
    result = runner.invoke(app, ["--json", "doc", "list", "ToDo"])
    assert result.exit_code == 0
    # meta-driven fields requested
    assert "fields" in list_route.calls.last.request.url.params
    fields = list_route.calls.last.request.url.params["fields"]
    assert "description" in fields and "status" in fields
    # sorts by creation desc by default
    assert list_route.calls.last.request.url.params["order_by"] == "creation desc"


@respx.mock
def test_delete_requires_yes_noninteractive(env):
    result = runner.invoke(app, ["--json", "doc", "delete", "ToDo", "X"])
    assert result.exit_code == 2  # refuses without --yes when non-interactive


@respx.mock
def test_update_threads_modified(env):
    respx.get(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(
            200, json={"data": {"name": "X", "modified": "2026-01-01 00:00:00"}}
        )
    )
    patch_route = respx.patch(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(200, json={"data": {"name": "X"}})
    )
    result = runner.invoke(
        app, ["--json", "doc", "update", "ToDo", "X", "--set", "status=Closed"]
    )
    assert result.exit_code == 0
    import json

    sent = json.loads(patch_route.calls.last.request.content)
    assert sent["modified"] == "2026-01-01 00:00:00"
    assert sent["status"] == "Closed"


@respx.mock
def test_update_force_skips_modified(env):
    patch_route = respx.patch(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(200, json={"data": {"name": "X"}})
    )
    result = runner.invoke(
        app,
        ["--json", "doc", "update", "ToDo", "X", "--set", "status=Closed", "--force"],
    )
    assert result.exit_code == 0
    import json

    sent = json.loads(patch_route.calls.last.request.content)
    assert "modified" not in sent


@respx.mock
def test_conflict_message(env):
    respx.get(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(
            200, json={"data": {"name": "X", "modified": "2026-01-01 00:00:00"}}
        )
    )
    respx.patch(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(
            409,
            json={
                "errors": [
                    {"message": "Document has been modified after you have opened it"}
                ]
            },
        )
    )
    result = runner.invoke(
        app, ["--json", "doc", "update", "ToDo", "X", "--set", "status=Closed"]
    )
    assert result.exit_code == 1
    assert "modified since you read it" in result.stderr


def test_guide_runs_without_auth():
    # No env / profile configured: guide must still work (no client, no network).
    result = runner.invoke(app, ["guide"])
    assert result.exit_code == 0
    assert "frappectl doctype show" in result.stdout
    assert "frappectl api" in result.stdout


def test_guide_tells_agents_not_to_touch_credentials():
    result = runner.invoke(app, ["guide"])
    assert result.exit_code == 0
    # Authentication is a human concern; the guide only states the boundary.
    assert "Do not run auth commands" in result.stdout
    assert "modify FRAPPE_*" in result.stdout
    assert "auth login" not in result.stdout


def test_login_refuses_non_interactive():
    # No TTY (the test runner has none): login must refuse rather than read a
    # secret from the pipe, and it must never reach the keyring or network.
    result = runner.invoke(
        app, ["auth", "login", "https://erp.example.com"], input="key\nsecret\n"
    )
    assert result.exit_code == 2
    assert "interactive only" in result.stderr
    assert "FRAPPE_API_SECRET" in result.stderr


def test_login_defaults_authentication_choice_to_oauth(monkeypatch):
    prompted = {}

    def prompt(message, *, default):
        prompted.update(message=message, default=default)
        return default

    monkeypatch.setattr(auth.typer, "prompt", prompt)

    assert auth._choose_oauth(False) is True
    assert prompted == {
        "message": "Authentication method [oauth/api-key]",
        "default": "oauth",
    }


def test_login_can_select_api_key_authentication(monkeypatch):
    monkeypatch.setattr(auth.typer, "prompt", lambda *args, **kwargs: "api-key")

    assert auth._choose_oauth(False) is False


def test_oauth_flag_skips_authentication_choice(monkeypatch):
    def unexpected_prompt(*args, **kwargs):
        raise AssertionError("--oauth should skip the authentication prompt")

    monkeypatch.setattr(auth.typer, "prompt", unexpected_prompt)

    assert auth._choose_oauth(True) is True


@respx.mock
def test_get_logged_user_only_trusts_non_guest_data():
    route = respx.get(f"{BASE}/api/v2/method/frappe.auth.get_logged_user")
    with FrappeClient(BASE, "k:s") as client:
        route.mock(return_value=httpx.Response(200, json={"data": "user@example.com"}))
        assert client.get_logged_user() == "user@example.com"

        route.mock(return_value=httpx.Response(200, json={"data": "Guest"}))
        with pytest.raises(FrappeError):
            client.get_logged_user()

        route.mock(return_value=httpx.Response(200, text="<html>login</html>"))
        with pytest.raises(FrappeError):
            client.get_logged_user()


def test_configure_renames_and_describes(fake_config):
    fake_config.add_profile("acme", "http://acme.test", "k", "s")
    result = runner.invoke(
        app,
        ["auth", "configure", "acme", "--name", "prod", "--description", "billing box"],
    )
    assert result.exit_code == 0
    profiles, default = fake_config.list_profiles()
    assert "acme" not in profiles
    assert profiles["prod"]["description"] == "billing box"
    assert default == "prod"


def test_configure_unknown_profile_errors(fake_config):
    result = runner.invoke(app, ["auth", "configure", "ghost", "--name", "x"])
    assert result.exit_code == 2
    assert "No such profile" in result.stderr


def test_configure_no_flags_non_interactive_errors(fake_config):
    # No TTY in the test runner and no flags: nothing to change, must refuse.
    fake_config.add_profile("acme", "http://acme.test", "k", "s")
    result = runner.invoke(app, ["auth", "configure", "acme"])
    assert result.exit_code == 2
    assert "Nothing to change" in result.stderr


def test_configure_clears_description(fake_config):
    fake_config.add_profile("acme", "http://acme.test", "k", "s", description="old")
    result = runner.invoke(app, ["auth", "configure", "acme", "--description", ""])
    assert result.exit_code == 0
    assert "description" not in fake_config.list_profiles()[0]["acme"]


def test_list_shows_description(fake_config):
    fake_config.add_profile(
        "acme", "http://acme.test", "k", "s", description="prod erp"
    )
    result = runner.invoke(app, ["--json", "auth", "list"])
    assert result.exit_code == 0
    assert "prod erp" in result.stdout


@respx.mock
def test_error_includes_hint(env):
    respx.get(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(
            404,
            json={
                "errors": [{"type": "DoesNotExistError", "message": "ToDo X not found"}]
            },
        )
    )
    result = runner.invoke(app, ["--json", "doc", "get", "ToDo", "X"])
    assert result.exit_code == 1
    assert "not found" in result.stderr
    assert "tip:" in result.stderr
    assert "frappectl doctype list" in result.stderr


@respx.mock
def test_api_method_get(env):
    respx.get(f"{BASE}/api/v2/method/frappe.client.get_count").mock(
        return_value=httpx.Response(200, json={"data": 5})
    )
    result = runner.invoke(
        app, ["api", "method/frappe.client.get_count", "-F", "doctype=User"]
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "5"
