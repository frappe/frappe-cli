# Frappe CLI

[![CI](https://github.com/frappe/frappe-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/frappe/frappe-cli/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](#license)

A command-line REST API v2 client for Frappe v16+ — built for humans and AI agents.

Using an agent? Start with `frappe-cli guide`. It fits site selection, core verbs,
filtering and the raw-API escape hatch on one screen.

```sh
frappe-cli auth login https://erp.example.com         # key/secret → OS keyring
frappe-cli -s raven doc list "Raven Channel" --json    # -s/--site selects a profile

frappe-cli doc list "Sales Invoice" -f status=Overdue -f 'grand_total>1000' \
  --fields name,customer,grand_total --all --json
frappe-cli doc get "Sales Invoice" SINV-0001
frappe-cli doc create ToDo --set description="Follow up" --set priority=High
cat invoice.json | frappe-cli doc create "Sales Invoice"
frappe-cli doc submit "Sales Invoice" SINV-0001
frappe-cli doc delete ToDo abc123 --yes

frappe-cli doctype show "Sales Invoice" --json         # discover the schema first
frappe-cli report run "Accounts Receivable" -f company="Frappe" --json
frappe-cli file upload ./contract.pdf --doctype "Sales Invoice" --name SINV-0001 --private
frappe-cli api method/frappe.client.get_count -F doctype=User
frappe-cli api method/gameplan.api.get_unread_count    # raw API, when verbs aren't enough
```

## Install

```sh
uv tool install git+https://github.com/frappe/frappe-cli
# or: pip install git+https://github.com/frappe/frappe-cli
```

## Authentication

Frappe CLI supports 3 authentication paths.

### Environment variables

Environment variables always win and never touch the keyring.

```sh
export FRAPPE_SITE=https://erp.example.com
export FRAPPE_API_KEY=xxxxxxxx
export FRAPPE_API_SECRET=yyyyyyyy
```

### Stored profiles

Generate an API key + secret from **User → Settings → API Access**, then log in:

```sh
frappe-cli auth login https://erp.example.com
frappe-cli auth login https://raven.example.com --name raven
frappe-cli auth login https://raven.example.com --name raven --default
frappe-cli auth list
frappe-cli auth default raven                          # change the default
frappe-cli -s raven doc list "Raven Channel"           # select per command
frappe-cli auth whoami
```

The site URL lives in `~/.config/frappe/config.json`; the secret lives in the **OS
keyring**. There is no plaintext fallback. If the machine has no keyring, use environment
variables.

### OAuth

Interactive login selects OAuth by default. It stores no secret; short-lived access
tokens refresh automatically.

```sh
frappe-cli auth login https://erp.example.com
frappe-cli auth login https://erp.example.com --client-id <public-client-id>
```

Sites with dynamic client registration create the client automatically. Frappe v15+
supports this by default; other sites need a pre-registered public `--client-id`.

OAuth needs a terminal and local browser, so it isn't the headless path. Use `--oauth`
to skip the authentication-method prompt.

### Read-only profiles

A read-only profile refuses every unsafe HTTP method **before the request leaves your
machine**. This removes the obvious production footgun: an exploratory command can't
accidentally mutate the site.

```sh
frappe-cli auth login https://prod.example.com --name prod --read-only
frappe-cli auth configure prod --read-only             # lock an existing profile
frappe-cli auth configure prod --writable              # allow writes again
```

This blocks `doc create/update/delete/submit`, `file upload`, and method calls through
`api`/`method`. Reads such as `doc list/get`, `doctype show`, and `report run` keep
working.

For environment-variable auth, set `FRAPPE_READ_ONLY=1`. To invoke a whitelisted read
method, make the safe verb explicit: `frappe-cli api method/… -X GET`.

## Output and scripting

- TTY → rich tables, colours and confirmation prompts.
- Pipe or `--json` → clean JSON on stdout. Logs and errors stay on stderr, so piping to
  `jq` is safe.
- Mutations → refuse to run non-interactively without `--yes`.
- Exit codes → `0` success, `1` failure, `2` usage error.
- `--debug` → traces method, URL and headers to stderr. Credentials are redacted. For
  endpoints that expose it, such as `doc list`, it also prints server-side SQL.

Server error detail is plain text; HTML is stripped. `--debug` never touches stdout, so
it won't corrupt `--json` output.

## Commands

| Command | What it does |
|---|---|
| `frappe-cli doc list <DocType>` | List documents. Supports `-f`, `--fields`, `--limit` and `--all`. |
| `frappe-cli doc get <DocType> <name>` | Fetch one document. |
| `frappe-cli doc create <DocType>` | Create from `--set` scalars and/or piped/`--input` JSON. |
| `frappe-cli doc update <DocType> <name>` | Update optimistically; use `--force` to override. |
| `frappe-cli doc delete <DocType> <name>` | Delete after confirmation, or pass `--yes`. |
| `frappe-cli doc submit\|cancel\|amend` | Run document lifecycle actions. |
| `frappe-cli doctype list` / `frappe-cli doctype show <name>` | Discover doctypes and schema. |
| `frappe-cli report run <name>` | Run a report with the same filter syntax as `doc list`. |
| `frappe-cli file upload\|download` | Transfer files; upload supports `--doctype/--name` and `--private`. |
| `frappe-cli api <path>` | Call raw v2 APIs: `frappe-cli api method/<path> -F key=value`. |
| `frappe-cli guide` | Print the agent primer; no site or authentication needed. |
| `frappe-cli assistant [pi\|claude\|codex]` | Launch a coding agent configured as a Frappe assistant. |
| `frappe-cli update` | Self-upgrade through the `uv`/`pip` backend used for installation. |

### Filtering

Simple filters use `-f`. Repeat the flag to combine them:

```sh
frappe-cli doc list ToDo -f status=Open -f priority=High
frappe-cli doc list "Sales Invoice" -f 'grand_total>1000' -f 'customer like %Inc%'
```

For `in`, `between`, child-table filters or anything else that doesn't fit cleanly in a
shell argument, pass full Frappe filter JSON:

```sh
frappe-cli doc list "Sales Invoice" --filters-json '[["status","in",["Paid","Overdue"]]]'
```

## License

MIT
