# fr — Frappe CLI

A `gh`-style command-line client for Frappe sites, designed for humans and AI
agents equally. Pure API client (Frappe v15+, API v2); no bench/server-side
coupling.

Status: pre-1.0, experimental. Personal repo, MIT license.

## Driving use cases

1. **AI agents on Frappe sites** — hand `fr` to Claude/agents instead of
   building per-app integrations.
2. **Daily personal workflow across internal Frappe sites** — Helpdesk
   (automated support), Gameplan (summarize unread posts), Raven (summarize
   unread messages), internal ERP (check and approve things interactively).
3. Scratching your own itch: poking at sites, debugging, quick data fixes.

The internal-sites use case implies two first-class requirements:

- **Multi-site profiles** are core UX, not an afterthought. Switching between
  helpdesk/gameplan/raven/erp must be one short flag or a default.
- **The `api` escape hatch will carry real weight** — Helpdesk/Gameplan/Raven
  expose most functionality via whitelisted methods, not doc CRUD. The meta
  suite + `fr api` is how agents reach those.

## Decisions

| Area | Decision |
|---|---|
| Audience | Humans and agents equally; TTY-aware (tables interactive, JSON when piped) |
| Command model | Generic DocType verbs + raw `api` escape hatch |
| Runtime | Python |
| CLI framework | Typer (+ rich for tables) |
| HTTP | httpx |
| Cold start | Write naturally; optimize later if it hurts |
| API target | Frappe v15+, API v2 only |
| Auth (v1) | API key/secret only |
| Credential storage | Env vars (`FR_SITE`, `FR_API_KEY`, `FR_API_SECRET`) always win; stored profiles keep secrets in OS keyring; **no plaintext fallback** — broken keyring ⇒ use env vars |
| Name | Command `fr`; package name e.g. `frappe-cli` (verify `fr` availability on PyPI early); repo/docs lead with "Frappe CLI" for searchability |
| License | MIT |
| Home | Personal repo first; donate to frappe org once the shape is proven |

### Document semantics

- **Verbs**: `fr doc list|get|create|update|delete` on any DocType.
- **Lifecycle**: `submit`, `cancel`, `amend` are first-class verbs. Raw
  `--set docstatus=N` stays possible but undocumented.
- **Input**: `--set field=value` for flat scalars; full JSON via stdin or
  `--input file.json` for child tables/nesting.
- **Concurrency**: optimistic by default — `update` threads the `modified`
  timestamp and fails on conflict with a clear error; `--force` overrides.
- **Safety**: confirmation prompt for destructive ops (delete/cancel) on a
  TTY; explicit `--yes` required when non-interactive.
- **Bulk**: none in v1. No import, no bulk-by-filter. Shell loops cover it.

### Listing

- Default page size 20; `--limit N`; `--all` auto-paginates unbounded
  (progress on stderr for large pulls).
- Default fields are meta-driven: name + title field + `in_list_view`
  columns (≈ the Desk list view). `--fields a,b,c` or `--fields '*'`.
- Filters: repeated `-f status=Paid` / `-f 'grand_total>1000'` for common
  cases; `--filters-json` for full Frappe filter syntax (`in`, `between`,
  child-table filters).

### Beyond documents

- **Introspection (full meta suite)**: `fr doctype list`,
  `fr doctype show <name>` — fields, types, link targets, required, child
  tables, permissions. This is what lets an agent self-orient on an
  unfamiliar site.
- **`fr api`**: gh-style raw access — `fr api method/<path> -F key=value`,
  arbitrary REST paths. Sugar-free power; doc verbs are sugar over the same
  client.
- **Files**: full support in v1 — upload (with `--doctype/--name` attach,
  `--private`) and download.
- **Reports**: first-class — `fr report run "Accounts Receivable" -f company=X`,
  same filter UX and output modes as `list`.
- **Background jobs**: no commands; document that `fr doc list "RQ Job"`
  works (virtual DocType in v15).
- **MCP**: no. The CLI is the interface; keep the internal client library
  clean enough that an MCP wrapper stays possible later.

### Output & scripting contract

- TTY: rich tables, colors, prompts. Piped/`--json`: clean JSON, no prompts
  (mutations require `--yes`).
- No built-in `--jq`/JMESPath — emit clean JSON, pipe to `jq`.
- Exit codes: 0 success, 1 failure, 2 usage error. Details live in the error
  message, not the code.
- Errors: light touch — strip HTML from `_server_messages`, print the raw
  server message on stderr. No structured error JSON in v1; agents parse
  stderr text. (Revisit if agent feedback demands structure.)

## Open items / known tensions

- **Agents + light-touch errors + 0/1/2 exit codes**: chosen for simplicity;
  if agent usage shows error-parsing pain, the upgrade path is a structured
  error envelope under `--json` (additive, non-breaking).
- **Keyring-only storage**: headless environments must use env vars; document
  this prominently in agent setup docs.
- **Workflow actions** ("approve stuff"): approval via Frappe Workflow is an
  action, not a field write. v1 answer is `fr api` against
  `frappe.model.workflow.apply_workflow`; a first-class `fr doc action`
  verb is a likely v1.x addition given the ERP-approvals use case.
- **Unread/summary flows** (Gameplan/Raven): exercise these early via
  `fr api` to find out what generic surface is missing.
- Verify `fr` name availability on PyPI before anything ships.

## Sketch

```sh
fr auth login https://erp.example.com        # prompts for key/secret → keyring
fr auth list                                  # profiles, default marked
fr -s raven doc list "Raven Channel" --json   # -s/--site selects profile

fr doc list "Sales Invoice" -f status=Overdue -f 'grand_total>1000' \
  --fields name,customer,grand_total --all --json
fr doc get "Sales Invoice" SINV-0001
fr doc create ToDo --set description="Follow up" --set priority=High
cat invoice.json | fr doc create "Sales Invoice"
fr doc submit "Sales Invoice" SINV-0001
fr doc delete ToDo abc123 --yes

fr doctype show "Sales Invoice" --json        # agent self-orientation
fr report run "Accounts Receivable" -f company="Frappe" --json
fr file upload ./contract.pdf --doctype "Sales Invoice" --name SINV-0001 --private
fr api method/frappe.client.get_count -F doctype=User
fr api method/gameplan.api.get_unread_count   # escape hatch in anger
```
