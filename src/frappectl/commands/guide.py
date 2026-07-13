"""``frappectl guide`` — a self-contained usage primer for agents.

This is deliberately a single, static, dependency-free command: it needs no
site, no auth and no network, so an agent can run ``frappectl guide`` as its very
first step to learn the CLI surface before touching a live site.
"""

from __future__ import annotations

import typer

GUIDE = r"""frappectl — an agent-friendly client for Frappe REST API v2.

Use --json or pipe output for clean JSON; errors and --debug traces go to stderr.

SITE ACCESS
  Access is preconfigured. Pass -s <profile> when a site is specified; with
  multiple profiles, never guess. Do not run auth commands or modify FRAPPE_*
  environment variables. If access fails, ask the human to configure it.

ORIENT YOURSELF (do this before guessing field or DocType names)
  frappectl doctype list                          # all DocTypes on the site
  frappectl doctype list --module HR --custom      # narrow it down
  frappectl doctype show "Sales Invoice"           # fields, types, links, required, child tables
  frappectl doctype show "Sales Invoice" --raw     # full unprocessed meta

DOCUMENTS (CRUD + lifecycle)
  frappectl doc list "Sales Invoice" -f status=Overdue -f 'grand_total>1000' \
    --fields name,customer,grand_total --order-by 'creation desc' --limit 50
  frappectl doc list "Sales Invoice" --all --json          # auto-paginate everything
  frappectl doc get "Sales Invoice" SINV-0001
  frappectl doc create ToDo --set description="Follow up" --set priority=High
  cat invoice.json | frappectl doc create "Sales Invoice"  # JSON for child tables / nesting
  frappectl doc update ToDo abc123 --set status=Closed     # optimistic; --force to overwrite
  frappectl doc delete ToDo abc123 --yes
  frappectl doc submit|cancel|amend "Sales Invoice" SINV-0001

  Filters: repeat -f field=value (also >, <, >=, <=, like), or use
  --filters-json '[["status","in",["Paid","Overdue"]]]'.

REPORTS
  frappectl report run "Accounts Receivable" -f company="Frappe" --json

FILES
  frappectl file upload ./contract.pdf --doctype "Sales Invoice" --name SINV-0001 --private
  frappectl file download <File name|/files/url> -o out.pdf   # '-o -' streams to stdout

DISCOVER METHODS
  Methods come in two kinds: rpc (a dotted path) and doctype (a controller method
  run against an existing document). Listings show a `kind` and a unified `ref`.
  frappectl method search --query="unread"           # search across both kinds
  frappectl method list                              # global rpc + doctype index
  frappectl method list --doctype "User"             # methods on one DocType (live)
  frappectl method show frappe.tests.test_api.test   # rpc detail: params, endpoint
  frappectl method show --doctype "User" add_comment # doctype method detail
  frappectl method call "User" Administrator add_comment -F comment_type=Comment

RAW API
  frappectl api method/frappe.client.get_count -F doctype=User    # -F typed, -f string
  frappectl api method/gameplan.api.get_unread_count
  frappectl api document/ToDo --method GET
  # Workflow actions: frappectl api method/frappe.model.workflow.apply_workflow
  # Background jobs:   frappectl doc list "RQ Job"

TIPS FOR AGENTS
  - Mutations refuse to run non-interactively without --yes.
  - `frappectl <command> --help` documents every flag.
"""


def guide() -> None:
    """Print the agent guide to this CLI (no site or auth required)."""
    typer.echo(GUIDE)
