import httpx
import pytest
import respx
from typer.testing import CliRunner

from frappe_cli.cli import _hoist_globals, app

BASE = "http://site.test"
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
        (["doc", "list", "X", "--filters-json", "[]"], ["doc", "list", "X", "--filters-json", "[]"]),
    ],
)
def test_hoist_globals(argv, expected):
    assert _hoist_globals(argv) == expected


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("FR_SITE", BASE)
    monkeypatch.setenv("FR_API_KEY", "k")
    monkeypatch.setenv("FR_API_SECRET", "s")


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
                        {"fieldname": "status", "fieldtype": "Select", "in_list_view": 1}
                    ],
                }
            },
        )
    )
    list_route = respx.get(f"{BASE}/api/v2/document/ToDo").mock(
        return_value=httpx.Response(200, json={"data": [{"name": "a"}], "has_next_page": False})
    )
    result = runner.invoke(app, ["--json", "doc", "list", "ToDo"])
    assert result.exit_code == 0
    # meta-driven fields requested
    assert "fields" in list_route.calls.last.request.url.params
    fields = list_route.calls.last.request.url.params["fields"]
    assert "description" in fields and "status" in fields


@respx.mock
def test_delete_requires_yes_noninteractive(env):
    result = runner.invoke(app, ["--json", "doc", "delete", "ToDo", "X"])
    assert result.exit_code == 2  # refuses without --yes when non-interactive


@respx.mock
def test_update_threads_modified(env):
    respx.get(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(200, json={"data": {"name": "X", "modified": "2026-01-01 00:00:00"}})
    )
    patch_route = respx.patch(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(200, json={"data": {"name": "X"}})
    )
    result = runner.invoke(app, ["--json", "doc", "update", "ToDo", "X", "--set", "status=Closed"])
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
        app, ["--json", "doc", "update", "ToDo", "X", "--set", "status=Closed", "--force"]
    )
    assert result.exit_code == 0
    import json

    sent = json.loads(patch_route.calls.last.request.content)
    assert "modified" not in sent


@respx.mock
def test_conflict_message(env):
    respx.get(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(200, json={"data": {"name": "X", "modified": "2026-01-01 00:00:00"}})
    )
    respx.patch(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(
            409, json={"errors": [{"message": "Document has been modified after you have opened it"}]}
        )
    )
    result = runner.invoke(app, ["--json", "doc", "update", "ToDo", "X", "--set", "status=Closed"])
    assert result.exit_code == 1
    assert "modified since you read it" in result.stderr


@respx.mock
def test_api_method_get(env):
    respx.get(f"{BASE}/api/v2/method/frappe.client.get_count").mock(
        return_value=httpx.Response(200, json={"data": 5})
    )
    result = runner.invoke(app, ["api", "method/frappe.client.get_count", "-F", "doctype=User"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "5"
