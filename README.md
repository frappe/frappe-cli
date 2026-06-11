# Frappe CLI (`fr`)

A command-line client for [Frappe](https://frappeframework.com)
sites, built for humans and AI agents equally. Pure API client (Frappe v15+,
REST API v2); no bench or server-side coupling.

> Status: pre-1.0, experimental. MIT licensed.

```sh
fr auth login https://erp.example.com         # prompts for key/secret → keyring
fr -s raven doc list "Raven Channel" --json    # -s/--site selects a profile

fr doc list "Sales Invoice" -f status=Overdue -f 'grand_total>1000' \
  --fields name,customer,grand_total --all --json
fr doc get "Sales Invoice" SINV-0001
fr doc create ToDo --set description="Follow up" --set priority=High
cat invoice.json | fr doc create "Sales Invoice"
fr doc submit "Sales Invoice" SINV-0001
fr doc delete ToDo abc123 --yes

fr doctype show "Sales Invoice" --json         # agent self-orientation
fr report run "Accounts Receivable" -f company="Frappe" --json
fr file upload ./contract.pdf --doctype "Sales Invoice" --name SINV-0001 --private
fr api method/frappe.client.get_count -F doctype=User
fr api method/gameplan.api.get_unread_count    # the escape hatch, in anger
```

## Install

```sh
uv tool install frappe-cli      # or: pipx install frappe-cli
```

## Authentication

Generate an **API key + secret** for your user in Frappe (User → Settings → API
Access). There are two ways to give `fr` those credentials:

**1. Environment variables (headless / agents).** These always win and never
touch the keyring:

```sh
export FR_SITE=https://erp.example.com
export FR_API_KEY=xxxxxxxx
export FR_API_SECRET=yyyyyyyy
```

**2. Stored profiles (interactive).** The site URL is stored in
`~/.config/fr/config.json`; the secret is stored in your **OS keyring**. There
is no plaintext secret fallback — if the keyring is unavailable, use env vars.

```sh
fr auth login https://erp.example.com          # prompts, verifies, stores
fr auth login https://raven.example.com --name raven
fr auth list                                   # default is marked
fr auth default raven                          # change the default
fr -s raven doc list "Raven Channel"           # pick a profile per command
fr auth whoami
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
| `fr doc list <DocType>` | List documents. `-f` filters, `--fields`, `--limit`, `--all`. |
| `fr doc get <DocType> <name>` | Fetch one document. |
| `fr doc create <DocType>` | Create from `--set` scalars and/or piped/`--input` JSON. |
| `fr doc update <DocType> <name>` | Update; optimistic by default, `--force` to override. |
| `fr doc delete <DocType> <name>` | Delete (confirms / `--yes`). |
| `fr doc submit\|cancel\|amend` | Document lifecycle. |
| `fr doctype list` / `fr doctype show <name>` | Introspection / meta. |
| `fr report run <name>` | Run a report with the same filter UX as `list`. |
| `fr file upload\|download` | Files, with `--doctype/--name` attach and `--private`. |
| `fr api <path>` | Raw v2 access: `fr api method/<path> -F key=value`. |

### Filtering

```sh
fr doc list ToDo -f status=Open -f 'priority=High'
fr doc list "Sales Invoice" -f 'grand_total>1000' -f 'customer like %Inc%'
# Anything richer (in / between / child tables) → full Frappe filter JSON:
fr doc list "Sales Invoice" --filters-json '[["status","in",["Paid","Overdue"]]]'
```

## Notes

- **Background jobs**: no dedicated command — `fr doc list "RQ Job"` works
  (virtual DocType in v15).
- **Workflow actions** (approvals): use
  `fr api method/frappe.model.workflow.apply_workflow` for now.
- **MCP**: not built in; the internal client library is kept clean enough that
  an MCP wrapper stays possible later.
