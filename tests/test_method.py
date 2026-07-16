import httpx
import pytest
import respx
from typer.testing import CliRunner

from frappectl import client as client_mod
from frappectl.cli import app
from frappectl.client import FrappeClient
from frappectl.errors import FrappeError

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
    monkeypatch.setattr(client_mod.time, "sleep", lambda _s: None)


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
    from frappectl import cli as cli_mod
    from frappectl.output import Ctx

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
def test_method_list_renders_both_kinds_human(env, monkeypatch):
    _force_human_output(monkeypatch)
    respx.get(f"{BASE}/api/v2/discovery/method").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "type": "method_index",
                    "methods": [
                        {
                            "kind": "rpc",
                            "path": "frappe.tests.test_api.test",
                            "description": "Exercise RPC.",
                        },
                        {
                            "kind": "doctype",
                            "doctype": "User",
                            "method": "populate_role_profile_roles",
                        },
                    ],
                }
            },
        )
    )
    result = runner.invoke(app, ["method", "list"])
    assert result.exit_code == 0
    assert "frappe.tests.test_api.test" in result.output
    assert "User.populate_role_profile_roles" in result.output


@respx.mock
def test_method_search_renders_doctype_kind(env, monkeypatch):
    _force_human_output(monkeypatch)
    respx.get(f"{BASE}/api/v2/discovery/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "results": [
                        {
                            "kind": "doctype",
                            "doctype": "User",
                            "method": "populate_role_profile_roles",
                            "description": "First line.",
                        }
                    ]
                }
            },
        )
    )
    result = runner.invoke(
        app, ["method", "search", "-q", "User populate_role_profile_roles"]
    )
    assert result.exit_code == 0
    assert "doctype" in result.output
    assert "User.populate_role_profile_roles" in result.output


@respx.mock
def test_method_list_doctype_hits_scoped_endpoint(env):
    route = respx.get(f"{BASE}/api/v2/discovery/doctype/User").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "type": "method_index",
                    "doctype": "User",
                    "methods": [
                        {"kind": "doctype", "doctype": "User", "method": "add_comment"}
                    ],
                }
            },
        )
    )
    result = runner.invoke(app, ["--json", "method", "list", "--doctype", "User"])
    assert result.exit_code == 0
    assert route.called
    assert "add_comment" in result.stdout


@respx.mock
def test_method_list_doctype_url_encodes_name(env):
    route = respx.get(f"{BASE}/api/v2/discovery/doctype/Sales%20Invoice").mock(
        return_value=httpx.Response(
            200, json={"data": {"type": "method_index", "methods": []}}
        )
    )
    result = runner.invoke(
        app, ["--json", "method", "list", "--doctype", "Sales Invoice"]
    )
    assert result.exit_code == 0
    assert route.called


@respx.mock
def test_method_list_doctype_404_when_supported_reports_unknown(env):
    respx.get(f"{BASE}/api/v2/discovery/doctype/Nope").mock(
        return_value=httpx.Response(404)
    )
    respx.get(f"{BASE}/api/v2/discovery").mock(
        return_value=httpx.Response(200, json={"data": {"type": "discovery"}})
    )
    result = runner.invoke(app, ["--json", "method", "list", "--doctype", "Nope"])
    assert result.exit_code == 1
    assert "not found or has no discoverable methods" in result.stderr


@respx.mock
def test_method_show_doctype_detail_json(env):
    respx.get(f"{BASE}/api/v2/discovery/doctype/User/method/add_comment").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "type": "method",
                    "kind": "doctype",
                    "doctype": "User",
                    "method": "add_comment",
                    "defined_in": "frappe.model.document.Document",
                    "endpoint": "/api/v2/document/User/{name}/method/add_comment",
                    "http_methods": ["GET", "POST"],
                    "permission": {"GET": "read", "POST": "write"},
                    "params": [],
                }
            },
        )
    )
    result = runner.invoke(
        app, ["--json", "method", "show", "--doctype", "User", "add_comment"]
    )
    assert result.exit_code == 0
    assert "frappe.model.document.Document" in result.stdout


@respx.mock
def test_method_show_doctype_detail_human_shows_defined_in(env, monkeypatch):
    _force_human_output(monkeypatch)
    respx.get(f"{BASE}/api/v2/discovery/doctype/User/method/add_comment").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "kind": "doctype",
                    "doctype": "User",
                    "method": "add_comment",
                    "defined_in": "frappe.model.document.Document",
                    "http_methods": ["GET", "POST"],
                    "permission": {"GET": "read", "POST": "write"},
                    "params": [
                        {"name": "comment_type", "required": False, "type": "str"}
                    ],
                }
            },
        )
    )
    result = runner.invoke(app, ["method", "show", "--doctype", "User", "add_comment"])
    assert result.exit_code == 0
    assert "defined_in" in result.output
    assert "comment_type" in result.output


@respx.mock
def test_method_call_picks_post_and_invokes_document_endpoint(env):
    invoke = respx.post(
        f"{BASE}/api/v2/document/User/Administrator/method/add_comment"
    ).mock(return_value=httpx.Response(200, json={"data": {"name": "c1"}}))
    result = runner.invoke(
        app,
        [
            "--json",
            "method",
            "call",
            "add_comment",
            "--doctype",
            "User",
            "--name",
            "Administrator",
            "-F",
            "comment_type=Comment",
        ],
    )
    assert result.exit_code == 0, result.stderr
    assert invoke.called
    import json as _json

    assert _json.loads(invoke.calls.last.request.content)["comment_type"] == "Comment"


@respx.mock
def test_method_call_explicit_get_skips_detail_fetch(env):
    detail = respx.get(
        f"{BASE}/api/v2/discovery/doctype/User/method/get_something"
    ).mock(return_value=httpx.Response(200, json={"data": {}}))
    invoke = respx.get(
        f"{BASE}/api/v2/document/User/Administrator/method/get_something"
    ).mock(return_value=httpx.Response(200, json={"data": {"ok": 1}}))
    result = runner.invoke(
        app,
        [
            "--json",
            "method",
            "call",
            "get_something",
            "--doctype",
            "User",
            "--name",
            "Administrator",
            "-X",
            "GET",
        ],
    )
    assert result.exit_code == 0
    assert invoke.called
    assert not detail.called


@respx.mock
def test_method_call_url_encodes_document_name(env):
    invoke = respx.post(
        f"{BASE}/api/v2/document/User/a%2Fb%40x/method/add_comment"
    ).mock(return_value=httpx.Response(200, json={"data": {}}))
    result = runner.invoke(
        app,
        [
            "--json",
            "method",
            "call",
            "add_comment",
            "--doctype",
            "User",
            "--name",
            "a/b@x",
            "-X",
            "POST",
        ],
    )
    assert result.exit_code == 0
    assert invoke.called


@respx.mock
def test_method_call_rpc_defaults_to_post(env):
    invoke = respx.post(f"{BASE}/api/v2/method/gameplan.api.get_unread_count").mock(
        return_value=httpx.Response(200, json={"data": {"count": 3}})
    )
    result = runner.invoke(
        app,
        [
            "--json",
            "method",
            "call",
            "gameplan.api.get_unread_count",
            "-F",
            "project=1",
        ],
    )
    assert result.exit_code == 0, result.stderr
    assert invoke.called
    import json as _json

    assert _json.loads(invoke.calls.last.request.content)["project"] == 1


@respx.mock
def test_method_call_rpc_explicit_get_hits_method_endpoint(env):
    detail = respx.get(f"{BASE}/api/v2/discovery/method/frappe.ping").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    invoke = respx.get(f"{BASE}/api/v2/method/frappe.ping").mock(
        return_value=httpx.Response(200, json={"data": "pong"})
    )
    result = runner.invoke(
        app, ["--json", "method", "call", "frappe.ping", "-X", "GET"]
    )
    assert result.exit_code == 0, result.stderr
    assert invoke.called
    assert not detail.called


@respx.mock
def test_method_call_read_only_defaults_to_get(monkeypatch):
    monkeypatch.setenv("FRAPPE_SITE", BASE)
    monkeypatch.setenv("FRAPPE_API_KEY", "k")
    monkeypatch.setenv("FRAPPE_API_SECRET", "s")
    monkeypatch.setenv("FRAPPE_READ_ONLY", "1")
    invoke = respx.get(f"{BASE}/api/v2/method/frappe.client.get_count").mock(
        return_value=httpx.Response(200, json={"data": 3})
    )
    result = runner.invoke(
        app,
        ["--json", "method", "call", "frappe.client.get_count", "-F", "doctype=User"],
    )
    assert result.exit_code == 0, result.stderr
    assert invoke.called


@respx.mock
def test_method_call_rejects_doctype_without_name(env):
    result = runner.invoke(
        app, ["--json", "method", "call", "add_comment", "--doctype", "User"]
    )
    assert result.exit_code == 2
    assert "--name is required" in result.stderr


@respx.mock
def test_method_call_rejects_name_without_doctype(env):
    result = runner.invoke(
        app, ["--json", "method", "call", "add_comment", "--name", "Administrator"]
    )
    assert result.exit_code == 2
    assert "only valid together with --doctype" in result.stderr


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
