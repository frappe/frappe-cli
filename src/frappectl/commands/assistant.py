"""``frappectl assistant`` — launch a CLI coding agent wired up for Frappe.

A thin launcher (in the spirit of the ``barista`` wrapper): it starts a
supported agent tool with a Frappe-flavoured system prompt, so the agent knows
to drive ``frappectl`` and which authenticated sites it can reach.

We never touch the working directory and never write into it. Instead each tool
gets a private config directory under frappectl's own config folder
(``~/.config/frappe/assistant/<tool>``); we point the tool's config-dir env var
at it and populate it just before launch, then ``exec`` the tool so its TUI owns
the terminal. That lets us:

  * codex  — supply a global AGENTS.md (codex has no --append-system-prompt),
             while symlinking auth.json/config.toml so login/config still work.

pi, claude and flow all take --append-system-prompt, so they launch with flags
only and use their own real config dir (auth included) untouched.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import typer

from .. import config
from ..output import err_console, fail
from .guide import render_guide

# System prompt handed to the agent. Mirrors the ``barista`` recipe: the guide
# teaches the CLI surface and lists the configured sites, so the agent can map a
# human's request ("the staging ERP") onto a concrete ``-s <profile>``.
_SYSTEM_PROMPT_TEMPLATE = """\
You are a Frappe assistant.

Use `frappectl` whenever you need to inspect or operate on Frappe sites.

Use this `frappectl guide` output to use `frappectl` effectively:

{guide}"""


@dataclass
class Launch:
    """A fully planned launch: pure data, no side effects until materialised."""

    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    writes: list[tuple[str, str]] = field(default_factory=list)
    symlinks: list[tuple[str, Path]] = field(default_factory=list)


@dataclass
class Tool:
    """A supported agent tool and how to launch it."""

    name: str
    binary: str
    # Plan a launch given the system prompt and this tool's private config dir.
    # Pure: it may READ the filesystem but must not mutate it (dry-run relies on
    # this). Actual writes/symlinks are described in the returned Launch.
    build: Callable[[str, Path], Launch]


def _pi_build(system_prompt: str, tool_dir: Path) -> Launch:
    # Like claude, pi takes --append-system-prompt, so we just launch it with
    # flags and leave its config dir alone. We deliberately do NOT point
    # PI_CODING_AGENT_DIR at our own folder: that hid the user's real config
    # (auth included), breaking authentication. quietStartup is a settings-only
    # option with no flag, so we simply forgo it rather than override the dir.
    return Launch(
        argv=[
            "pi",
            "--name",
            "Frappe assistant",
            "--approve",
            "--offline",
            "--no-skills",
            "--no-context-files",
            "--append-system-prompt",
            system_prompt,
        ]
    )


def _codex_build(system_prompt: str, tool_dir: Path) -> Launch:
    # codex has no --append-system-prompt. It DOES read a global AGENTS.md from
    # $CODEX_HOME, so we point CODEX_HOME at our folder, write AGENTS.md there,
    # and symlink auth.json/config.toml back to the real codex home so the user
    # stays logged in and keeps their config.
    real_home = Path(
        os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    )
    symlinks = [
        (name, real_home / name)
        for name in ("auth.json", "config.toml")
        if (real_home / name).exists()
    ]
    return Launch(
        argv=["codex"],
        env={"CODEX_HOME": str(tool_dir)},
        writes=[("AGENTS.md", system_prompt + "\n")],
        symlinks=symlinks,
    )


def _claude_build(system_prompt: str, tool_dir: Path) -> Launch:
    # claude's chrome is largely fixed; --append-system-prompt is the one lever
    # that matters. Permissions are left at the default (interactive). No config
    # dir needed.
    return Launch(argv=["claude", "--append-system-prompt", system_prompt])


def _flow_build(system_prompt: str, tool_dir: Path) -> Launch:
    # flow is a client/server harness: `flow web` runs the daemon, serves the
    # web UI and opens a browser tab. The prompt goes to every session the UI
    # creates. Its own config dir holds the provider credentials, so we leave
    # it alone.
    return Launch(argv=["flow", "web", "--append-system-prompt", system_prompt])


# Order matters: it decides the auto-pick when no tool is named.
TOOLS: list[Tool] = [
    Tool(name="pi", binary="pi", build=_pi_build),
    Tool(name="claude", binary="claude", build=_claude_build),
    Tool(name="codex", binary="codex", build=_codex_build),
    Tool(name="flow", binary="flow", build=_flow_build),
]
_TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def _system_prompt() -> str:
    return _SYSTEM_PROMPT_TEMPLATE.format(guide=render_guide())


def _assistant_dir(tool_name: str) -> Path:
    return config.config_dir() / "assistant" / tool_name


def _pick_tool(name: Optional[str]) -> Tool:
    if name is not None:
        tool = _TOOLS_BY_NAME.get(name)
        if tool is None:
            supported = ", ".join(_TOOLS_BY_NAME)
            raise fail(f"Unsupported tool: {name}. Supported: {supported}.", 2)
        return tool
    for tool in TOOLS:
        if shutil.which(tool.binary):
            return tool
    supported = ", ".join(t.binary for t in TOOLS)
    raise fail(
        f"No supported agent tool found on PATH. Install one of: {supported}.", 127
    )


def _materialize(launch: Launch, tool_dir: Path) -> None:
    """Apply the launch's planned filesystem side effects."""
    tool_dir.mkdir(parents=True, exist_ok=True)
    for name, content in launch.writes:
        (tool_dir / name).write_text(content)
    for name, target in launch.symlinks:
        link = tool_dir / name
        if link.is_symlink():
            if os.readlink(link) == str(target):
                continue
            link.unlink()
        elif link.exists():
            link.unlink()
        link.symlink_to(target)


def assistant(
    ctx: typer.Context,
    tool: Optional[str] = typer.Argument(
        None,
        help="Agent tool to launch: pi, claude, codex or flow. Defaults to the "
        "first one installed.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print what would run (env, files, command) and exit, without "
        "launching the tool or writing anything.",
    ),
) -> None:
    """Launch a CLI coding agent wired up as a Frappe assistant.

    Starts a supported agent tool (pi, claude, codex or flow) with a Frappe
    system prompt, so it drives `frappectl` against your authenticated sites.
    `flow` opens its web UI in a browser; the others own the terminal. Anything
    after `--` is passed straight through to the tool, e.g.:

    frappectl assistant pi -- "list overdue invoices on staging"
    """
    chosen = _pick_tool(tool)

    if not shutil.which(chosen.binary):
        raise fail(f"'{chosen.binary}' not found on PATH.", 127)
    # The agent shells out to `frappectl`; refuse to launch if it can't.
    if not shutil.which("frappectl"):
        raise fail(
            "'frappectl' not found on PATH; the assistant needs it to reach "
            "your sites.",
            127,
        )

    tool_dir = _assistant_dir(chosen.name)
    launch = chosen.build(_system_prompt(), tool_dir)
    argv = [*launch.argv, *ctx.args]

    if dry_run:
        for key in sorted(launch.env):
            typer.echo(f"env: {key}={launch.env[key]}")
        for name, _ in launch.writes:
            typer.echo(f"write: {tool_dir / name}")
        for name, target in launch.symlinks:
            typer.echo(f"symlink: {tool_dir / name} -> {target}")
        # argv as a JSON array on one line: the system prompt contains newlines,
        # so a shell-quoted string would span lines and defeat parsing.
        typer.echo("cmd: " + json.dumps(argv))
        return

    _materialize(launch, tool_dir)

    err_console.print(f"[dim]starting {chosen.name} as a Frappe assistant…[/dim]")
    os.execvpe(argv[0], argv, {**os.environ, **launch.env})
