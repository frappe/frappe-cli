# Frappe CLI

[![CI](https://github.com/frappe/frappe-cli/actions/workflows/test.yml/badge.svg)](https://github.com/frappe/frappe-cli/actions/workflows/test.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](#license)

A command-line client for [Frappe](https://frappeframework.com) sites, built for
humans and AI agents equally. A pure REST API v2 client for Frappe v16; no bench
or server-side coupling required.

New here (or an agent)? Run `frappe-cli guide` for a one-screen primer covering auth,
the core verbs, filtering and the raw-API escape hatch.

```sh
frappe-cli auth login https://erp.example.com         # prompts for key/secret → keyring
frappe-cli -s raven doc list "Raven Channel" --json    # -s/--site selects a profile

frappe-cli doc list "Sales Invoice" -f status=Overdue -f 'grand_total>1000' \
  --fields name,customer,grand_total --all --json
frappe-cli doc get "Sales Invoice" SINV-0001
frappe-cli doc create ToDo --set description="Follow up" --set priority=High
cat invoice.json | frappe-cli doc create "Sales Invoice"
frappe-cli doc submit "Sales Invoice" SINV-0001
frappe-cli doc delete ToDo abc123 --yes

frappe-cli doctype show "Sales Invoice" --json         # agent self-orientation
frappe-cli report run "Accounts Receivable" -f company="Frappe" --json
frappe-cli file upload ./contract.pdf --doctype "Sales Invoice" --name SINV-0001 --private
frappe-cli api method/frappe.client.get_count -F doctype=User
frappe-cli api method/gameplan.api.get_unread_count    # the escape hatch, in anger
```

## Install

```sh
uv tool install git+https://github.com/frappe/frappe-cli      # or: pip install git+https://github.com/frappe/frappe-cli
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
frappe-cli auth login https://erp.example.com          # prompts, verifies, stores
frappe-cli auth login https://raven.example.com --name raven
frappe-cli auth list                                   # default is marked
frappe-cli auth default raven                          # change the default
frappe-cli -s raven doc list "Raven Channel"           # pick a profile per command
frappe-cli auth whoami
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
| `frappe-cli doc list <DocType>` | List documents. `-f` filters, `--fields`, `--limit`, `--all`. |
| `frappe-cli doc get <DocType> <name>` | Fetch one document. |
| `frappe-cli doc create <DocType>` | Create from `--set` scalars and/or piped/`--input` JSON. |
| `frappe-cli doc update <DocType> <name>` | Update; optimistic by default, `--force` to override. |
| `frappe-cli doc delete <DocType> <name>` | Delete (confirms / `--yes`). |
| `frappe-cli doc submit\|cancel\|amend` | Document lifecycle. |
| `frappe-cli doctype list` / `frappe-cli doctype show <name>` | Introspection / meta. |
| `frappe-cli report run <name>` | Run a report with the same filter UX as `list`. |
| `frappe-cli file upload\|download` | Files, with `--doctype/--name` attach and `--private`. |
| `frappe-cli api <path>` | Raw v2 access: `frappe-cli api method/<path> -F key=value`. |
| `frappe-cli guide` | Print a short, self-contained usage primer (no site/auth needed). |

### Filtering

```sh
frappe-cli doc list ToDo -f status=Open -f 'priority=High'
frappe-cli doc list "Sales Invoice" -f 'grand_total>1000' -f 'customer like %Inc%'
# Anything richer (in / between / child tables) → full Frappe filter JSON:
frappe-cli doc list "Sales Invoice" --filters-json '[["status","in",["Paid","Overdue"]]]'
```

## License

MIT
