# frappectl

[![CI](https://github.com/frappe/frappectl/actions/workflows/ci.yml/badge.svg)](https://github.com/frappe/frappectl/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](#license)

A command-line REST API v2 client for Frappe v16+ — built for humans and AI agents.

Using an agent? Start with `frappectl guide`. It fits site selection, core verbs,
filtering and the raw-API escape hatch on one screen.

```sh
frappectl auth login https://erp.example.com         # key/secret → OS keyring
frappectl -s raven doc list "Raven Channel" --json    # -s/--site selects a profile

frappectl doc list "Sales Invoice" -f status=Overdue -f 'grand_total>1000' \
  --fields name,customer,grand_total --all --json
frappectl doc get "Sales Invoice" SINV-0001
frappectl doc create ToDo --set description="Follow up" --set priority=High
cat invoice.json | frappectl doc create "Sales Invoice"
frappectl doc submit "Sales Invoice" SINV-0001
frappectl doc delete ToDo abc123 --yes

frappectl doctype show "Sales Invoice" --json         # discover the schema first
frappectl report run "Accounts Receivable" -f company="Frappe" --json
frappectl file upload ./contract.pdf --doctype "Sales Invoice" --name SINV-0001 --private
frappectl api method/frappe.client.get_count -F doctype=User
frappectl api method/gameplan.api.get_unread_count    # raw API, when verbs aren't enough
```

## Install

```sh
uv tool install frappectl
# or: pip install frappectl
# development version: uv tool install git+https://github.com/frappe/frappectl
```

This installs two commands: `frappectl` and its short alias `fr`.

## Authentication

frappectl supports 3 authentication paths.

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
frappectl auth login https://erp.example.com
frappectl auth login https://raven.example.com --name raven
frappectl auth login https://raven.example.com --name raven --default
frappectl auth list
frappectl auth default raven                          # change the default
frappectl -s raven doc list "Raven Channel"           # select per command
frappectl auth whoami
```

The site URL lives in `~/.config/frappe/config.json`; the secret lives in the **OS
keyring**. There is no plaintext fallback. If the machine has no keyring, use environment
variables.

### OAuth

Interactive login selects OAuth by default. It stores no secret; short-lived access
tokens refresh automatically.

```sh
frappectl auth login https://erp.example.com
frappectl auth login https://erp.example.com --client-id <public-client-id>
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
frappectl auth login https://prod.example.com --name prod --read-only
frappectl auth configure prod --read-only             # lock an existing profile
frappectl auth configure prod --writable              # allow writes again
```

This blocks `doc create/update/delete/submit`, `file upload`, and method calls through
`api`/`method`. Reads such as `doc list/get`, `doctype show`, and `report run` keep
working.

For environment-variable auth, set `FRAPPE_READ_ONLY=1`. To invoke a whitelisted read
method, make the safe verb explicit: `frappectl api method/… -X GET`.

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
| `frappectl doc list <DocType>` | List documents. Supports `-f`, `--fields`, `--limit` and `--all`. |
| `frappectl doc get <DocType> <name>` | Fetch one document. |
| `frappectl doc create <DocType>` | Create from `--set` scalars and/or piped/`--input` JSON. |
| `frappectl doc update <DocType> <name>` | Update optimistically; use `--force` to override. |
| `frappectl doc delete <DocType> <name>` | Delete after confirmation, or pass `--yes`. |
| `frappectl doc submit\|cancel\|amend` | Run document lifecycle actions. |
| `frappectl doctype list` / `frappectl doctype show <name>` | Discover doctypes and schema. |
| `frappectl report run <name>` | Run a report with the same filter syntax as `doc list`. |
| `frappectl file upload\|download` | Transfer files; upload supports `--doctype/--name` and `--private`. |
| `frappectl api <path>` | Call raw v2 APIs: `frappectl api method/<path> -F key=value`. |
| `frappectl guide` | Print the agent primer; no site or authentication needed. |
| `frappectl assistant [pi\|claude\|codex]` | Launch a coding agent configured as a Frappe assistant. |
| `frappectl update` | Self-upgrade through the `uv`/`pip` backend used for installation. |

### Filtering

Simple filters use `-f`. Repeat the flag to combine them:

```sh
frappectl doc list ToDo -f status=Open -f priority=High
frappectl doc list "Sales Invoice" -f 'grand_total>1000' -f 'customer like %Inc%'
```

For `in`, `between`, child-table filters or anything else that doesn't fit cleanly in a
shell argument, pass full Frappe filter JSON:

```sh
frappectl doc list "Sales Invoice" --filters-json '[["status","in",["Paid","Overdue"]]]'
```

## License

MIT
