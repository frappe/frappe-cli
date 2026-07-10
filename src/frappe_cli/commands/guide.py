"""``frappe-cli guide`` — a self-contained usage primer for agents.

This is deliberately a single, static, dependency-free command: it needs no
site, no auth and no network, so an agent can run ``frappe-cli guide`` as its very
first step to learn the CLI surface before touching a live site.
"""

from __future__ import annotations

import typer

GUIDE = r"""frappe-cli — an agent-friendly client for Frappe REST API v2.

Use --json or pipe output for clean JSON; errors and --debug traces go to stderr.

SITE ACCESS
  Access is preconfigured. Pass -s <profile> when a site is specified; with
  multiple profiles, never guess. Do not run auth commands or modify FRAPPE_*
  environment variables. If access fails, ask the human to configure it.

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

  Filters: repeat -f field=value (also >, <, >=, <=, like), or use
  --filters-json '[["status","in",["Paid","Overdue"]]]'.

REPORTS
  frappe-cli report run "Accounts Receivable" -f company="Frappe" --json

FILES
  frappe-cli file upload ./contract.pdf --doctype "Sales Invoice" --name SINV-0001 --private
  frappe-cli file download <File name|/files/url> -o out.pdf   # '-o -' streams to stdout

DISCOVER METHODS
  frappe-cli method search --query="unread"          # search path/description/docstring
  frappe-cli method list                              # all methods visible to this session
  frappe-cli method show frappe.tests.test_api.test   # params, http methods, endpoint

RAW API
  frappe-cli api method/frappe.client.get_count -F doctype=User    # -F typed, -f string
  frappe-cli api method/gameplan.api.get_unread_count
  frappe-cli api document/ToDo --method GET
  # Workflow actions: frappe-cli api method/frappe.model.workflow.apply_workflow
  # Background jobs:   frappe-cli doc list "RQ Job"

TIPS FOR AGENTS
  - Mutations refuse to run non-interactively without --yes.
  - `frappe-cli <command> --help` documents every flag.
"""


def guide() -> None:
    """Print the agent guide to this CLI (no site or auth required)."""
    typer.echo(GUIDE)
