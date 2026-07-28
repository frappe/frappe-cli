"""``frappectl guide`` — a usage primer for agents.

The text is static and needs no network and no site. An agent can run
``frappectl guide`` as the first step to learn the CLI before it touches a site.
The list of configured site profiles is the one dynamic part, and it comes from
the local config. The guide alone therefore lets an agent map "the staging ERP"
onto a concrete ``-s <profile>``.
"""

from __future__ import annotations

import typer

from .. import config

# Replaced with the rendered profile listing. Not a str.format field: the guide
# body contains literal braces (raw JSON examples).
_SITES_MARKER = "<<SITES>>"

GUIDE = r"""frappectl - an agent client for the Frappe REST API v2.

Use --json or pipe the output to get clean JSON. Errors and --debug traces go to
stderr.

SITE ACCESS
  Access is already configured. Pass -s <profile> when the task names a site.
  Do not run auth commands. Do not modify FRAPPE_* environment variables.
  If access fails, ask the human to configure it.

  Configured profiles:
<<SITES>>

ORIENT YOURSELF (do this before you guess a field name or a DocType name)
  frappectl doctype list                           # all DocTypes on the site
  frappectl doctype list --module HR --custom      # a smaller list
  frappectl doctype show "Sales Invoice"           # fields, types, links, required, child tables
  frappectl doctype show "Sales Invoice" --raw     # the full meta, without processing

DOCUMENTS (create, read, update, delete, and lifecycle)
  frappectl doc list "Sales Invoice" -f status=Overdue -f 'grand_total>1000' \
    --fields name,customer,grand_total --order-by 'creation desc' --limit 50
  frappectl doc list "Sales Invoice" --all --json          # read all pages
  frappectl doc get "Sales Invoice" SINV-0001
  frappectl doc create ToDo --set description="Follow up" --set priority=High
  cat invoice.json | frappectl doc create "Sales Invoice"  # JSON for child tables and nested data
  frappectl doc update ToDo abc123 --set status=Closed     # fails on a concurrent edit, use --force to overwrite
  frappectl doc delete ToDo abc123
  frappectl doc submit|cancel|amend "Sales Invoice" SINV-0001

  To filter, repeat -f field=value. The operators >, <, >=, <=, and like also
  work. For other operators, use --filters-json
  '[["status","in",["Paid","Overdue"]]]'.

REPORTS AND READ-ONLY SQL
  frappectl report run "Accounts Receivable" -f company="Frappe" --json
  frappectl query 'select count(*) as users from tabUser'  # needs the System Manager role or Administrator

FILES
  frappectl file upload ./contract.pdf --doctype "Sales Invoice" --name SINV-0001 --private
  frappectl file download <File name|/files/url> -o out.pdf   # '-o -' writes to stdout

DISCOVER METHODS
  A method has one of two kinds. An rpc method is a dotted path. A doctype
  method is a controller method that runs against an existing document. Each
  listing shows the kind and a `ref` that works for both kinds.
  frappectl method search --query="unread"           # search both kinds
  frappectl method list                              # global rpc and doctype index
  frappectl method list --doctype "User"             # methods on one DocType, read from the site
  frappectl method show frappe.tests.test_api.test   # rpc detail: parameters and endpoint
  frappectl method show --doctype "User" add_comment # doctype method detail
  frappectl method call gameplan.api.get_unread_count -F project=1   # call an rpc method
  frappectl method call add_comment --doctype "User" --name Administrator -F comment_type=Comment

RAW API
  frappectl api method/frappe.client.get_count -F doctype=User    # -F sends a typed value, -f sends a string
  frappectl api method/frappe.client.get_list -F doctype=User -F 'filters:={"enabled":1}'  # := sends raw JSON
  frappectl api method/gameplan.api.get_unread_count
  frappectl api document/ToDo --method GET
"""


def render_sites() -> str:
    """Indented listing of stored profiles, one site per line."""
    try:
        profiles, default = config.list_profiles()
    except config.ConfigError:
        profiles, default = {}, None
    if not profiles:
        return "    (none configured. Ask the human to set up access.)"
    lines = []
    for name, info in profiles.items():
        parts = [f"    - {name}: {info.get('site', '')}"]
        desc = info.get("description", "")
        if desc:
            parts.append(f"— {desc}")
        tags = []
        if name == default:
            tags.append("default")
        if info.get("read_only"):
            tags.append("read-only")
        if tags:
            parts.append(f"[{', '.join(tags)}]")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def render_guide() -> str:
    """The full guide, with the configured sites spliced in."""
    return GUIDE.replace(_SITES_MARKER, render_sites())


def guide() -> None:
    """Print the agent guide to this CLI. It needs no site and no network."""
    typer.echo(render_guide())
