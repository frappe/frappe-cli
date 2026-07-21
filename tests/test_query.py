import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from frappectl.cli import app

BASE = "http://localhost"
METHOD = "frappe.desk.doctype.system_console.system_console.execute_code"
URL = f"{BASE}/api/v2/method/{METHOD}"
runner = CliRunner()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("FRAPPE_SITE", BASE)
    monkeypatch.setenv("FRAPPE_API_KEY", "k")
    monkeypatch.setenv("FRAPPE_API_SECRET", "s")


def _response(rows):
    return httpx.Response(200, json={"data": {"output": json.dumps(rows)}})


@respx.mock
def test_query_sends_system_console_sql_and_emits_json(env):
    route = respx.post(URL).mock(return_value=_response([{"count(*)": 4}]))

    result = runner.invoke(app, ["--json", "query", "select count(*) from tabUser"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == [{"count(*)": 4}]
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "doc": {
            "doctype": "System Console",
            "type": "SQL",
            "console": "select count(*) from tabUser",
            "commit": 0,
        }
    }


@respx.mock
def test_query_renders_table_for_human_output(env, monkeypatch):
    from frappectl import cli as cli_mod
    from frappectl.output import Ctx

    def make_ctx(**kwargs):
        kwargs["json_mode"] = False
        ctx = Ctx(**kwargs)
        ctx.json = False
        ctx.is_tty = True
        return ctx

    monkeypatch.setattr(cli_mod, "Ctx", make_ctx)
    respx.post(URL).mock(
        return_value=_response([{"name": "Administrator", "enabled": 1}])
    )

    result = runner.invoke(app, ["query", "select name, enabled from tabUser"])

    assert result.exit_code == 0
    assert "name" in result.stdout
    assert "enabled" in result.stdout
    assert "Administrator" in result.stdout


@respx.mock
def test_query_reads_stdin(env):
    route = respx.post(URL).mock(return_value=_response([]))

    result = runner.invoke(app, ["--json", "query", "-"], input="select 1\n")

    assert result.exit_code == 0
    sent = json.loads(route.calls.last.request.content)
    assert sent["doc"]["console"] == "select 1\n"


@respx.mock
def test_query_surfaces_console_error(env):
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "output": "Traceback (most recent call last):\nPermissionError: Only read-only queries are allowed"
                }
            },
        )
    )

    result = runner.invoke(app, ["query", "delete from tabUser"])

    assert result.exit_code == 1
    assert "PermissionError: Only read-only queries are allowed" in result.stderr


def test_query_rejects_empty_input(env):
    result = runner.invoke(app, ["query", "-"], input="")
    assert result.exit_code == 2
    assert "must not be empty" in result.stderr
