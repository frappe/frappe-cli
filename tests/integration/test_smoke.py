"""End-to-end smoke tests against a live Frappe site.

These drive the *real* CLI as a subprocess (``python -m frappectl ...``), the
same way a human or agent would, and talk to an actual Frappe site over HTTP.
Auth comes from the ``FRAPPE_SITE`` / ``FRAPPE_API_KEY`` / ``FRAPPE_API_SECRET``
environment variables; the whole module is skipped when they aren't set, so a
normal ``pytest`` run (and the unit-test CI) simply skips it.

This is deliberately a smoke test — it exercises the load-bearing paths (auth,
introspection, the CRUD lifecycle, the raw ``api`` escape hatch and the safety
guards) rather than every flag. See ``.github/workflows/integration.yml`` for
how the site is provisioned.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("FRAPPE_SITE"),
        reason="live site required: set FRAPPE_SITE / FRAPPE_API_KEY / FRAPPE_API_SECRET",
    ),
]

# ToDo is the ideal smoke DocType: CRUD-able, no mandatory fields, present on
# every Frappe site.
DOCTYPE = "ToDo"


def run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    """Invoke the installed CLI as a subprocess and capture its output.

    Output is captured (not a TTY), so the CLI auto-selects JSON — no ``--json``
    flag is needed. Note ``--json``/``--site`` are *global* options and must
    precede the command group when passed explicitly (e.g. ``--site foo doc
    list ...``).
    """
    return subprocess.run(
        [sys.executable, "-m", "frappectl", *args],
        input=stdin,
        capture_output=True,
        text=True,
    )


def run_json(*args: str, stdin: str | None = None):
    """Run the CLI, assert success, and parse stdout as JSON."""
    proc = run(*args, stdin=stdin)
    assert proc.returncode == 0, (
        f"`frappectl {' '.join(args)}` exited {proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    return json.loads(proc.stdout)


def test_guide_runs_without_network():
    # The primer must work with no auth and no network — the first thing an
    # agent runs to orient itself.
    proc = run("guide")
    assert proc.returncode == 0


def test_whoami_authenticates():
    who = run_json("auth", "whoami")
    assert who["user"] == "Administrator"
    assert who["source"] == "env"


def test_doctype_list_includes_todo():
    names = run_json("doctype", "list")
    assert DOCTYPE in set(names)


def test_doctype_show_exposes_fields():
    meta = run_json("doctype", "show", DOCTYPE)
    assert meta["name"] == DOCTYPE
    fieldnames = {f["fieldname"] for f in meta["fields"]}
    assert {"description", "status"} <= fieldnames


def test_api_escape_hatch():
    user = run_json("api", "method/frappe.auth.get_logged_user")
    assert user == "Administrator"


def test_doc_crud_lifecycle():
    # A unique, space-free marker so we can find exactly our record back.
    marker = f"frappectl-smoke-{uuid.uuid4().hex}"

    created = run_json("doc", "create", DOCTYPE, "--set", f"description={marker}")
    name = created["name"]

    try:
        got = run_json("doc", "get", DOCTYPE, name)
        assert got["name"] == name
        assert marker in (got.get("description") or "")

        rows = run_json("doc", "list", DOCTYPE, "-f", f"description={marker}")
        assert [r["name"] for r in rows] == [name]

        updated = run_json("doc", "update", DOCTYPE, name, "--set", "status=Closed")
        assert updated["status"] == "Closed"
    finally:
        deleted = run("doc", "delete", DOCTYPE, name)
        assert deleted.returncode == 0, deleted.stderr

    missing = run("doc", "get", DOCTYPE, name)
    assert missing.returncode == 1


# frappe.ping is the ideal smoke method: guest-accessible, parameter-free, and
# present on every Frappe site that exposes discovery at all.
PING = "frappe.ping"

# Every doctype inherits add_comment from the Document base class, so it's the
# ideal smoke doctype method: whitelisted, universal, and observable (the
# returned Comment carries the text back).
DOC_METHOD = "add_comment"


def test_method_list_includes_both_kinds():
    methods = run_json("method", "list")["methods"]
    rpc_paths = {m.get("path") for m in methods if m.get("kind") == "rpc"}
    assert PING in rpc_paths
    assert any(m.get("kind") == "doctype" for m in methods)


def test_method_search_finds_ping():
    result = run_json("method", "search", "-q", "ping")
    hit = next(r for r in result["results"] if r.get("path") == PING)
    assert hit["kind"] == "rpc"


def test_method_show_exposes_contract():
    result = run_json("method", "show", PING)
    assert result["path"] == PING
    assert result["allow_guest"] is True
    assert result["endpoint"] == f"/api/v2/method/{PING}"
    assert "GET" in result["http_methods"]


def test_method_list_doctype_includes_inherited_standard_methods():
    methods = run_json("method", "list", "--doctype", DOCTYPE)["methods"]
    assert DOC_METHOD in {m.get("method") for m in methods}


def test_method_show_doctype_exposes_contract():
    result = run_json("method", "show", "--doctype", DOCTYPE, DOC_METHOD)
    assert result["kind"] == "doctype"
    assert result["doctype"] == DOCTYPE
    assert result["method"] == DOC_METHOD
    assert "POST" in result["http_methods"]


def test_method_call_invokes_doctype_method():
    marker = f"frappectl-smoke-{uuid.uuid4().hex}"
    created = run_json("doc", "create", DOCTYPE, "--set", "description=smoke")
    name = created["name"]
    try:
        comment = run_json(
            "method",
            "call",
            DOC_METHOD,
            "--doctype",
            DOCTYPE,
            "--name",
            name,
            "-f",
            f"text={marker}",
        )
        assert marker in json.dumps(comment)
    finally:
        deleted = run("doc", "delete", DOCTYPE, name)
        assert deleted.returncode == 0, deleted.stderr


def test_method_show_missing_reports_clean_error():
    proc = run("method", "show", f"frappe.does_not_exist_{uuid.uuid4().hex}")
    assert proc.returncode == 1
    assert proc.stdout.strip() == ""
    assert proc.stderr.strip()


def test_missing_doc_reports_clean_error():
    proc = run("doc", "get", DOCTYPE, f"missing-{uuid.uuid4().hex}")
    assert proc.returncode == 1
    assert proc.stdout.strip() == ""
    assert proc.stderr.strip()
