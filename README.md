# Frappe CLI (`frappe`)

A command-line client for [Frappe](https://frappeframework.com)
sites, built for humans and AI agents equally. Pure API client (Frappe v15+,
REST API v2); no bench or server-side coupling.

> Status: pre-1.0, experimental. MIT licensed.

New here (or an agent)? Run `frappe guide` for a one-screen primer covering auth,
the core verbs, filtering and the raw-API escape hatch.

```sh
frappe auth login https://erp.example.com         # prompts for key/secret → keyring
frappe -s raven doc list "Raven Channel" --json    # -s/--site selects a profile

frappe doc list "Sales Invoice" -f status=Overdue -f 'grand_total>1000' \
  --fields name,customer,grand_total --all --json
frappe doc get "Sales Invoice" SINV-0001
frappe doc create ToDo --set description="Follow up" --set priority=High
cat invoice.json | frappe doc create "Sales Invoice"
frappe doc submit "Sales Invoice" SINV-0001
frappe doc delete ToDo abc123 --yes

frappe doctype show "Sales Invoice" --json         # agent self-orientation
frappe report run "Accounts Receivable" -f company="Frappe" --json
frappe file upload ./contract.pdf --doctype "Sales Invoice" --name SINV-0001 --private
frappe api method/frappe.client.get_count -F doctype=User
frappe api method/gameplan.api.get_unread_count    # the escape hatch, in anger
```

## Install

```sh
uv tool install frappe-cli      # or: pipx install frappe-cli
```

## Authentication

Generate an **API key + secret** for your user in Frappe (User → Settings → API
Access). There are two ways to give `frappe` those credentials:

**1. Environment variables (headless / agents).** These always win and never
touch the keyring:

```sh
export FRAPPE_SITE=https://erp.example.com
export FRAPPE_API_KEY=xxxxxxxx
export FRAPPE_API_SECRET=yyyyyyyy
```

**2. Stored profiles (interactive).** The site URL is stored in
`~/.config/frappe/config.json`; the secret is stored in your **OS keyring**. There
is no plaintext secret fallback — if the keyring is unavailable, use env vars.

```sh
frappe auth login https://erp.example.com          # prompts, verifies, stores
frappe auth login https://raven.example.com --name raven
frappe auth list                                   # default is marked
frappe auth default raven                          # change the default
frappe -s raven doc list "Raven Channel"           # pick a profile per command
frappe auth whoami
```

## Output & scripting

- On a TTY you get rich tables, colours and confirmation prompts.
- When piped, or with `--json`, you get clean JSON on stdout and nothing else —
  pipe it to `jq`. Mutations refuse to run non-interactively without `--yes`.
- Exit codes: `0` success, `1` failure, `2` usage error. Error detail goes to
  stderr as plain text (HTML stripped from server messages).

## Commands

| Command | What it does |
|---|---|
| `frappe doc list <DocType>` | List documents. `-f` filters, `--fields`, `--limit`, `--all`. |
| `frappe doc get <DocType> <name>` | Fetch one document. |
| `frappe doc create <DocType>` | Create from `--set` scalars and/or piped/`--input` JSON. |
| `frappe doc update <DocType> <name>` | Update; optimistic by default, `--force` to override. |
| `frappe doc delete <DocType> <name>` | Delete (confirms / `--yes`). |
| `frappe doc submit\|cancel\|amend` | Document lifecycle. |
| `frappe doctype list` / `frappe doctype show <name>` | Introspection / meta. |
| `frappe report run <name>` | Run a report with the same filter UX as `list`. |
| `frappe file upload\|download` | Files, with `--doctype/--name` attach and `--private`. |
| `frappe api <path>` | Raw v2 access: `frappe api method/<path> -F key=value`. |
| `frappe guide` | Print a short, self-contained usage primer (no site/auth needed). |

### Filtering

```sh
frappe doc list ToDo -f status=Open -f 'priority=High'
frappe doc list "Sales Invoice" -f 'grand_total>1000' -f 'customer like %Inc%'
# Anything richer (in / between / child tables) → full Frappe filter JSON:
frappe doc list "Sales Invoice" --filters-json '[["status","in",["Paid","Overdue"]]]'
```

## Notes

- **Background jobs**: no dedicated command — `frappe doc list "RQ Job"` works
  (virtual DocType in v15).
- **Workflow actions** (approvals): use
  `frappe api method/frappe.model.workflow.apply_workflow` for now.
- **MCP**: not built in; the internal client library is kept clean enough that
  an MCP wrapper stays possible later.
