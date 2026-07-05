# Philosophy

- **For humans and agents, equally.** A TTY gets rich tables and prompts; a pipe (or `--json`) gets clean JSON on stdout and nothing else. Same command, both audiences.
- **A thin, honest wrapper.** Verbs map to the Frappe REST API v2 with little magic, and `api` is always the escape hatch — the CLI never hides the platform underneath.
- **Predictable I/O contract.** stdout is data, stderr is chatter. Exit `0`/`1`/`2`. Errors are plain text with a tip pointing at the fix.
- **Safe by default.** Mutations refuse to run non-interactively without `--yes`; updates are optimistic unless `--force`.
- **Credentials are the human's job.** Key/secret live in env or the OS keyring — never plaintext, never mutated by the CLI, never set up for you.
- **Self-orienting.** `guide` and `doctype show` let anyone learn the surface before touching a live site. Orient, don't guess.
