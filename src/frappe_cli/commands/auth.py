"""``frappe-cli auth`` — manage site profiles and credentials."""

from __future__ import annotations

import sys
from typing import Optional

import typer

from .. import config
from ..client import FrappeClient
from ..errors import FrappeError
from ..output import emit_list, err_console, fail, get_ctx

app = typer.Typer(no_args_is_help=True, help="Manage site profiles and credentials.")


def _default_profile_name(site: str) -> str:
    host = site.split("://", 1)[-1].split("/", 1)[0]
    return host.split(":", 1)[0]


@app.command("login")
def login(
    ctx: typer.Context,
    site: str = typer.Argument(..., help="Site URL, e.g. https://erp.example.com"),
    name: Optional[str] = typer.Option(
        None, "--name", help="Profile name (default: the site host)."
    ),
    set_default: bool = typer.Option(
        True, "--default/--no-default", help="Make this the default."
    ),
):
    """Store credentials for a site in the OS keyring.

    Login is interactive by design: the API key and secret are always prompted
    for, never accepted as flags or piped in. Passing secrets on the command
    line leaks them into shell history, the process list and CI logs. For
    headless / agent use, set FRAPPE_SITE / FRAPPE_API_KEY / FRAPPE_API_SECRET
    in the environment instead — those never touch the keyring.
    """
    # Refuse anything that isn't a real terminal so credentials can't be fed in
    # by pipe, heredoc or redirect (all of which end up in history or logs).
    if not sys.stdin.isatty():
        raise fail(
            "auth login is interactive only and needs a terminal to prompt for "
            "the API key and secret. For headless / agent use set FRAPPE_SITE, "
            "FRAPPE_API_KEY and FRAPPE_API_SECRET in the environment instead.",
            2,
        )

    profile = name or _default_profile_name(config._normalize_site(site))

    api_key = typer.prompt("API key")
    api_secret = typer.prompt("API secret", hide_input=True)

    norm_site = config._normalize_site(site)

    # Verify before storing so we never persist dead credentials.
    try:
        with FrappeClient(norm_site, f"{api_key}:{api_secret}") as client:
            who = client.call_method("frappe.auth.get_logged_user", http_method="GET")
    except FrappeError as e:
        raise fail(f"Could not authenticate against {norm_site}: {e.message}")

    try:
        config.add_profile(
            profile, norm_site, api_key, api_secret, make_default=set_default
        )
    except config.ConfigError as e:
        raise fail(str(e), 2)

    err_console.print(
        f"[green]logged in[/green] as {who} — profile '{profile}'"
        + (" (default)" if set_default else "")
    )


@app.command("list")
def list_profiles(ctx: typer.Context):
    """List stored profiles; the default is marked."""
    c = get_ctx(ctx)
    try:
        profiles, default = config.list_profiles()
    except config.ConfigError as e:
        raise fail(str(e), 2)

    rows = [
        {
            "profile": name,
            "site": info.get("site", ""),
            "default": name == default,
        }
        for name, info in profiles.items()
    ]
    emit_list(c, rows, ["profile", "site", "default"])
    if not rows and not c.json:
        err_console.print(
            "[dim]No profiles. Run 'frappe-cli auth login <url>' or use FRAPPE_SITE env vars.[/dim]"
        )


@app.command("logout")
def logout(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Profile to remove."),
):
    """Remove a stored profile and its credentials."""
    try:
        config.remove_profile(name)
    except config.ConfigError as e:
        raise fail(str(e), 2)
    err_console.print(f"[green]removed[/green] profile '{name}'")


@app.command("default")
def set_default(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Profile to make default."),
):
    """Set the default profile."""
    try:
        config.set_default(name)
    except config.ConfigError as e:
        raise fail(str(e), 2)
    err_console.print(f"[green]default[/green] is now '{name}'")


@app.command("whoami")
def whoami(ctx: typer.Context):
    """Show the resolved site and logged-in user for the active profile."""
    c = get_ctx(ctx)
    try:
        creds = config.resolve(c.profile)
    except config.ConfigError as e:
        raise fail(str(e), 2)
    try:
        with FrappeClient(creds.site, creds.token) as client:
            user = client.call_method("frappe.auth.get_logged_user", http_method="GET")
    except FrappeError as e:
        raise fail(e.message)
    from ..output import emit_record

    emit_record(c, {"site": creds.site, "user": user, "source": creds.source})
