import httpx
import pytest
import respx
from typer.testing import CliRunner

from frappe_cli import client as client_mod
from frappe_cli.cli import app
from frappe_cli.client import FrappeClient
from frappe_cli.errors import FrappeError

BASE = "http://localhost"
runner = CliRunner()


def _client():
    return FrappeClient(BASE, "k:s")


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("FRAPPE_SITE", BASE)
    monkeypatch.setenv("FRAPPE_API_KEY", "k")
    monkeypatch.setenv("FRAPPE_API_SECRET", "s")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    # Never actually sleep during retry tests.
    monkeypatch.setattr(client_mod.time, "sleep", lambda _s: None)


# --- client ---------------------------------------------------------------


@respx.mock
def test_discovery_search_hits_singular_endpoint():
    route = respx.get(f"{BASE}/api/v2/discovery/search").mock(
        return_value=httpx.Response(
            200, json={"data": {"query": "abc", "results": [{"path": "x"}]}}
        )
    )
    out = _client().discovery_search("abc")
    assert out == {"query": "abc", "results": [{"path": "x"}]}
    assert route.calls.last.request.url.params["q"] == "abc"


@respx.mock
def test_discovery_list_uses_singular_method_route():
    respx.get(f"{BASE}/api/v2/discovery/method").mock(
        return_value=httpx.Response(
            200, json={"data": {"type": "method_index", "methods": []}}
        )
    )
    assert _client().discovery_list()["type"] == "method_index"


@respx.mock
def test_discovery_retries_503_then_succeeds():
    route = respx.get(f"{BASE}/api/v2/discovery/method").mock(
        side_effect=[
            httpx.Response(503, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"data": {"methods": []}}),
        ]
    )
    assert _client().discovery_list() == {"methods": []}
    assert route.call_count == 2


@respx.mock
def test_discovery_gives_up_after_max_retries():
    respx.get(f"{BASE}/api/v2/discovery/method").mock(
        return_value=httpx.Response(503, headers={"Retry-After": "0"})
    )
    with pytest.raises(FrappeError) as ei:
        _client().discovery_list()
    assert ei.value.status_code == 503


@respx.mock
def test_discovery_supported_false_on_404():
    respx.get(f"{BASE}/api/v2/discovery").mock(return_value=httpx.Response(404))
    assert _client().discovery_supported() is False


# --- CLI ------------------------------------------------------------------


@respx.mock
def test_method_search_json_is_raw(env):
    respx.get(f"{BASE}/api/v2/discovery/search").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"query": "abc", "results": [{"path": "frappe.ping"}]}},
        )
    )
    result = runner.invoke(app, ["--json", "method", "search", "--query=abc"])
    assert result.exit_code == 0
    assert '"query": "abc"' in result.stdout
    assert "frappe.ping" in result.stdout


@respx.mock
def test_method_list_404_reports_unavailable(env):
    respx.get(f"{BASE}/api/v2/discovery/method").mock(return_value=httpx.Response(404))
    result = runner.invoke(app, ["--json", "method", "list"])
    assert result.exit_code == 1
    assert "not available on this site" in result.stderr


@respx.mock
def test_method_show_404_but_discovery_supported(env):
    respx.get(f"{BASE}/api/v2/discovery/method/frappe.nope").mock(
        return_value=httpx.Response(404)
    )
    respx.get(f"{BASE}/api/v2/discovery").mock(
        return_value=httpx.Response(200, json={"data": {"type": "discovery"}})
    )
    result = runner.invoke(app, ["--json", "method", "show", "frappe.nope"])
    assert result.exit_code == 1
    assert "not found or not visible" in result.stderr


@respx.mock
def test_method_show_404_when_discovery_absent(env):
    respx.get(f"{BASE}/api/v2/discovery/method/frappe.nope").mock(
        return_value=httpx.Response(404)
    )
    respx.get(f"{BASE}/api/v2/discovery").mock(return_value=httpx.Response(404))
    result = runner.invoke(app, ["--json", "method", "show", "frappe.nope"])
    assert result.exit_code == 1
    assert "not available on this site" in result.stderr


def _show_response(source=None):
    data = {
        "type": "method",
        "path": "frappe.ping",
        "name": "ping",
        "http_methods": ["GET"],
        "params": [],
        "endpoint": "/api/v2/method/frappe.ping",
        "allow_guest": True,
    }
    if source is not None:
        data["source"] = source
    return httpx.Response(200, json={"data": data})


@respx.mock
def test_method_show_json_includes_source_when_present(env):
    src = "def ping():\n    return 'pong'\n"
    respx.get(f"{BASE}/api/v2/discovery/method/frappe.ping").mock(
        return_value=_show_response(source=src)
    )
    result = runner.invoke(app, ["--json", "method", "show", "frappe.ping"])
    assert result.exit_code == 0
    assert "def ping()" in result.stdout


def _force_human_output(monkeypatch):
    """Force the human render path.

    Ctx picks JSON when stdout is not a TTY, and CliRunner never presents one,
    so swap in a Ctx that reports itself interactive.
    """
    from frappe_cli import cli as cli_mod
    from frappe_cli.output import Ctx

    def make_ctx(**kwargs):
        kwargs["json_mode"] = False
        c = Ctx(**kwargs)
        c.json = False
        c.is_tty = True
        return c

    monkeypatch.setattr(cli_mod, "Ctx", make_ctx)


@respx.mock
def test_method_show_renders_source_on_tty(env, monkeypatch):
    _force_human_output(monkeypatch)
    respx.get(f"{BASE}/api/v2/discovery/method/frappe.ping").mock(
        return_value=_show_response(source="def ping():\n    return 'pong'\n")
    )
    result = runner.invoke(app, ["method", "show", "frappe.ping"])
    assert result.exit_code == 0
    assert "Source" in result.output
    assert "def ping" in result.output


@respx.mock
def test_method_show_omits_source_section_when_absent(env, monkeypatch):
    _force_human_output(monkeypatch)
    respx.get(f"{BASE}/api/v2/discovery/method/frappe.ping").mock(
        return_value=_show_response(source=None)
    )
    result = runner.invoke(app, ["method", "show", "frappe.ping"])
    assert result.exit_code == 0
    assert "Source" not in result.output


@respx.mock
def test_method_show_json_preserved_after_503_retry(env):
    respx.get(f"{BASE}/api/v2/discovery/method/frappe.ping").mock(
        side_effect=[
            httpx.Response(503, headers={"Retry-After": "0"}),
            httpx.Response(
                200,
                json={
                    "data": {
                        "type": "method",
                        "path": "frappe.ping",
                        "name": "ping",
                        "http_methods": ["GET", "POST"],
                        "params": [],
                        "endpoint": "/api/v2/method/frappe.ping",
                        "allow_guest": True,
                    }
                },
            ),
        ]
    )
    result = runner.invoke(app, ["--json", "method", "show", "frappe.ping"])
    assert result.exit_code == 0
    assert '"path": "frappe.ping"' in result.stdout
