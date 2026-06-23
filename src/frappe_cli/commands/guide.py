"""``frappe-cli guide`` — a self-contained usage primer for humans and agents.

This is deliberately a single, static, dependency-free command: it needs no
site, no auth and no network, so an agent can run ``frappe-cli guide`` as its very
first step to learn the whole surface area before touching a live site.
"""

from __future__ import annotations

import typer

GUIDE = r"""frappe-cli — a command-line client for Frappe sites (REST API v2).

Everything is a thin, scriptable wrapper over the API. On a TTY you get tables
and colour; when piped or with --json you get clean JSON on stdout and nothing
else. Exit codes: 0 ok, 1 failure, 2 usage error. Errors go to stderr as plain
text and usually include a "Tip:" pointing at the command that can help.

AUTHENTICATION
  Two ways to provide credentials (an API key + secret from User > API Access):
    1. Env vars (headless / agents) — always win, never touch the keyring:
         export FRAPPE_SITE=https://erp.example.com
         export FRAPPE_API_KEY=xxxx
         export FRAPPE_API_SECRET=yyyy
    2. Stored profiles (interactive):
         frappe-cli auth login https://erp.example.com      # prompts, verifies, stores
         frappe-cli auth list                                # default is marked
         frappe-cli auth default <profile>                   # change the default
         frappe-cli -s <profile> doc list ToDo               # pick a profile per command
  Check who/where you are:  frappe-cli auth whoami

ORIENT YOURSELF (do this before guessing field or DocType names)
  frappe-cli doctype list                          # all DocTypes on the site
  frappe-cli doctype list --module HR --custom      # narrow it down
  frappe-cli doctype show "Sales Invoice"           # fields, types, links, required, child tables
  frappe-cli doctype show "Sales Invoice" --raw     # full unprocessed meta

DOCUMENTS (CRUD + lifecycle)
  frappe-cli doc list "Sales Invoice" -f status=Overdue -f 'grand_total>1000' \
    --fields name,customer,grand_total --order-by 'creation desc' --limit 50
  frappe-cli doc list "Sales Invoice" --all --json          # auto-paginate everything
  frappe-cli doc get "Sales Invoice" SINV-0001
  frappe-cli doc create ToDo --set description="Follow up" --set priority=High
  cat invoice.json | frappe-cli doc create "Sales Invoice"  # JSON for child tables / nesting
  frappe-cli doc update ToDo abc123 --set status=Closed     # optimistic; --force to overwrite
  frappe-cli doc delete ToDo abc123 --yes
  frappe-cli doc submit|cancel|amend "Sales Invoice" SINV-0001

FILTERING
  -f field=value                # repeatable; also >, <, >=, <=, 'like %x%'
  --filters-json '[["status","in",["Paid","Overdue"]]]'   # full Frappe filter syntax

REPORTS
  frappe-cli report run "Accounts Receivable" -f company="Frappe" --json

FILES
  frappe-cli file upload ./contract.pdf --doctype "Sales Invoice" --name SINV-0001 --private
  frappe-cli file download <File name|/files/url> -o out.pdf   # '-o -' streams to stdout

RAW API (the escape hatch for anything the verbs above don't cover)
  frappe-cli api method/frappe.client.get_count -F doctype=User    # -F typed, -f string
  frappe-cli api method/gameplan.api.get_unread_count
  frappe-cli api document/ToDo --method GET
  # Workflow actions: frappe-cli api method/frappe.model.workflow.apply_workflow
  # Background jobs:   frappe-cli doc list "RQ Job"

TIPS FOR AGENTS
  - Pass --json (or just pipe) for machine-readable output everywhere.
  - Mutations refuse to run non-interactively without --yes.
  - Stuck on a name or field? Run `frappe-cli doctype show <DocType>` first.
  - `frappe-cli <command> --help` documents every flag.
"""


def guide() -> None:
    """Print a short guide to using this CLI (no site or auth required)."""
    typer.echo(GUIDE)
