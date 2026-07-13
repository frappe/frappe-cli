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
    flag is needed. Note ``--json``/``--yes``/``--site`` are *global* options and
    must precede the command group when passed explicitly (e.g. ``--yes doc
    delete ...``).
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
    rows = run_json("doctype", "list")
    assert DOCTYPE in {r["name"] for r in rows}


def test_doctype_show_exposes_fields():
    meta = run_json("doctype", "show", DOCTYPE)
    assert meta["name"] == DOCTYPE
    fieldnames = {f["fieldname"] for f in meta["fields"]}
    assert {"description", "status"} <= fieldnames


def test_api_escape_hatch():
    # The raw escape hatch that agents lean on for whitelisted methods.
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

        # Server-side filter round-trips to exactly the doc we created.
        rows = run_json("doc", "list", DOCTYPE, "-f", f"description={marker}")
        assert [r["name"] for r in rows] == [name]

        updated = run_json("doc", "update", DOCTYPE, name, "--set", "status=Closed")
        assert updated["status"] == "Closed"
    finally:
        # --yes is a global option and must precede the command group.
        deleted = run("--yes", "doc", "delete", DOCTYPE, name)
        assert deleted.returncode == 0, deleted.stderr

    # After deletion the doc is gone: a get fails with exit code 1.
    missing = run("doc", "get", DOCTYPE, name)
    assert missing.returncode == 1


# frappe.ping is the ideal smoke method: guest-accessible, parameter-free, and
# present on every Frappe site that exposes discovery at all.
PING = "frappe.ping"


def test_method_list_includes_ping():
    result = run_json("method", "list")
    paths = {m["path"] for m in result["methods"]}
    assert PING in paths


def test_method_search_finds_ping():
    result = run_json("method", "search", "-q", "ping")
    hit = next(r for r in result["results"] if r["path"] == PING)
    assert hit["allow_guest"] is True


def test_method_show_exposes_contract():
    result = run_json("method", "show", PING)
    assert result["path"] == PING
    assert result["allow_guest"] is True
    assert result["endpoint"] == f"/api/v2/method/{PING}"
    assert "GET" in result["http_methods"]


def test_method_show_missing_reports_clean_error():
    proc = run("method", "show", f"frappe.does_not_exist_{uuid.uuid4().hex}")
    assert proc.returncode == 1
    assert proc.stdout.strip() == ""  # no half-baked JSON on stdout
    assert proc.stderr.strip()  # a human-readable error on stderr


def test_delete_refuses_without_confirmation():
    # Non-interactive delete without --yes must refuse (usage error, code 2)
    # before it ever touches the server.
    proc = run("doc", "delete", DOCTYPE, "does-not-exist")
    assert proc.returncode == 2
    assert "--yes" in proc.stderr


def test_missing_doc_reports_clean_error():
    proc = run("doc", "get", DOCTYPE, f"missing-{uuid.uuid4().hex}")
    assert proc.returncode == 1
    assert proc.stdout.strip() == ""  # no half-baked JSON on stdout
    assert proc.stderr.strip()  # a human-readable error on stderr
