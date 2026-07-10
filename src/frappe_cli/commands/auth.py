"""``frappe-cli auth`` — manage site profiles and credentials."""

from __future__ import annotations

import sys
from typing import Optional

import typer

from .. import config, oauth
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
        None, "--name", help="Profile name / shorthand (default: the site host)."
    ),
    description: Optional[str] = typer.Option(
        None,
        "--description",
        help="Note describing the site; assistant mode uses it to pick a site.",
    ),
    set_default: bool = typer.Option(
        False,
        "--default/--no-default",
        help="Make this the default profile (the first profile is always the default).",
    ),
    read_only: Optional[bool] = typer.Option(
        None,
        "--read-only/--writable",
        help="Refuse any write (create/update/delete/method call) through this profile.",
    ),
    use_oauth: bool = typer.Option(
        False,
        "--oauth",
        help="Log in via OAuth in the browser instead of an API key/secret.",
    ),
    client_id: Optional[str] = typer.Option(
        None,
        "--client-id",
        help="OAuth public client id to use when the site has no dynamic "
        "registration (must already be registered with the loopback redirect URI).",
    ),
    oauth_port: int = typer.Option(
        oauth.DEFAULT_LOOPBACK_PORT,
        "--oauth-port",
        help="Fixed loopback port for the OAuth redirect (must match the "
        "registered redirect URI).",
    ),
) -> None:
    """Store credentials for a site in the OS keyring.

    Two auth methods. By default, login is an interactive API key/secret prompt:
    the key and secret are never accepted as flags or piped in, since that leaks
    them into shell history, the process list and CI logs. With --oauth, login
    runs an OAuth 2.0 authorization-code flow in your browser instead — no secret
    is ever stored, and tokens refresh automatically.

    Both methods need a terminal (and OAuth needs a local browser). For headless
    / agent use, set FRAPPE_SITE / FRAPPE_API_KEY / FRAPPE_API_SECRET in the
    environment instead — those never touch the keyring.
    """
    # Refuse anything that isn't a real terminal so credentials can't be fed in
    # by pipe, heredoc or redirect (all of which end up in history or logs), and
    # because the OAuth flow needs a browser on this machine.
    if not sys.stdin.isatty():
        raise fail(
            "auth login is interactive only and needs a terminal (OAuth also "
            "needs a local browser). For headless / agent use set FRAPPE_SITE, "
            "FRAPPE_API_KEY and FRAPPE_API_SECRET in the environment instead.",
            2,
        )

    norm_site = config._normalize_site(site)

    # A friendly shorthand and a description are both prompted for (with sane
    # defaults) unless supplied as flags, so a stored site is easy to pick
    # later — by a human at `auth list` or by an agent in assistant mode.
    profile = (
        name
        if name is not None
        else typer.prompt("Profile name", default=_default_profile_name(norm_site))
    )
    if description is None:
        description = typer.prompt(
            "Description (used by assistant mode; optional)", default=""
        )
    # Read-only is prompted like the other fields when not set explicitly;
    # default No so a plain Enter keeps the profile writable.
    if read_only is None:
        read_only = typer.confirm(
            "Read-only? (refuse all writes through this profile)", default=False
        )

    if use_oauth:
        _login_oauth(
            profile,
            norm_site,
            client_id=client_id,
            oauth_port=oauth_port,
            set_default=set_default,
            description=description or "",
            read_only=bool(read_only),
        )
        return

    api_key = typer.prompt("API key")
    api_secret = typer.prompt("API secret", hide_input=True)

    # Verify before storing so we never persist dead credentials.
    try:
        with FrappeClient(norm_site, f"{api_key}:{api_secret}") as client:
            who = client.get_logged_user()
    except FrappeError as e:
        raise fail(f"Could not authenticate against {norm_site}: {e.message}")

    try:
        config.add_profile(
            profile,
            norm_site,
            api_key,
            api_secret,
            make_default=set_default,
            description=description or "",
            read_only=bool(read_only),
        )
    except config.ConfigError as e:
        raise fail(str(e), 2)

    _report_login(who, profile, read_only=bool(read_only))


def _login_oauth(
    profile: str,
    norm_site: str,
    *,
    client_id: Optional[str],
    oauth_port: int,
    set_default: bool,
    description: str,
    read_only: bool,
) -> None:
    """Run the OAuth browser flow, verify identity, and persist the tokens."""
    # Reuse a client id already registered for this profile so re-logins don't
    # litter the site with new OAuth Client rows; an explicit --client-id wins.
    resolved_client_id = client_id or config.oauth_client_id(profile)

    def announce(url: str) -> None:
        err_console.print(
            "[dim]Opening your browser to authorize. If it does not open, "
            f"visit:[/dim]\n{url}"
        )

    try:
        tokens, used_client_id = oauth.login(
            norm_site,
            client_id=resolved_client_id,
            port=oauth_port,
            announce=announce,
        )
    except oauth.OAuthError as e:
        raise fail(f"OAuth login against {norm_site} failed: {e}")

    # Verify before storing so we never persist a token we cannot use.
    try:
        with FrappeClient(
            norm_site, tokens.access_token, token_type="bearer"
        ) as client:
            who = client.get_logged_user()
    except FrappeError as e:
        raise fail(f"Could not authenticate against {norm_site}: {e.message}")

    try:
        config.add_oauth_profile(
            profile,
            norm_site,
            used_client_id,
            tokens,
            make_default=set_default,
            description=description,
            read_only=read_only,
        )
    except config.ConfigError as e:
        raise fail(str(e), 2)

    _report_login(who, profile, read_only=read_only, oauth=True)


def _report_login(
    who: str, profile: str, *, read_only: bool, oauth: bool = False
) -> None:
    # The profile may still be the default even without --default: the very
    # first profile always becomes the default (see config.add_profile).
    _, default = config.list_profiles()
    is_default = default == profile
    err_console.print(
        f"[green]logged in[/green] as {who} — profile '{profile}'"
        + (" [oauth]" if oauth else "")
        + (" (default)" if is_default else "")
        + (" [read-only]" if read_only else "")
    )


@app.command("list")
def list_profiles(ctx: typer.Context) -> None:
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
            "auth": info.get("auth", "api_key"),
            "description": info.get("description", ""),
            "read_only": bool(info.get("read_only", False)),
            "default": name == default,
        }
        for name, info in profiles.items()
    ]
    emit_list(
        c, rows, ["profile", "site", "auth", "description", "read_only", "default"]
    )
    if not rows and not c.json:
        err_console.print(
            "[dim]No profiles. Run 'frappe-cli auth login <url>' or use FRAPPE_SITE env vars.[/dim]"
        )


@app.command("logout")
def logout(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Profile to remove."),
) -> None:
    """Remove a stored profile and its credentials.

    For an OAuth profile the access token is revoked on the server first
    (best-effort) so logging out actually ends the session, not just forgets it
    locally.
    """
    profiles, _ = config.list_profiles()
    if profiles.get(name, {}).get("auth") == "oauth":
        token = config.oauth_access_token(name)
        if token:
            oauth.revoke(config._normalize_site(profiles[name]["site"]), token)
    try:
        config.remove_profile(name)
    except config.ConfigError as e:
        raise fail(str(e), 2)
    err_console.print(f"[green]removed[/green] profile '{name}'")


@app.command("default")
def set_default(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Profile to make default."),
) -> None:
    """Set the default profile."""
    try:
        config.set_default(name)
    except config.ConfigError as e:
        raise fail(str(e), 2)
    err_console.print(f"[green]default[/green] is now '{name}'")


@app.command("configure")
def configure(
    ctx: typer.Context,
    profile: str = typer.Argument(..., help="Profile to reconfigure."),
    name: Optional[str] = typer.Option(
        None, "--name", help="New profile name / shorthand."
    ),
    description: Optional[str] = typer.Option(
        None,
        "--description",
        help="New description; pass an empty string to clear it.",
    ),
    read_only: Optional[bool] = typer.Option(
        None,
        "--read-only/--writable",
        help="Make this profile read-only (refuse writes) or writable again.",
    ),
) -> None:
    """Reconfigure a stored site: rename it, change its description, or toggle
    read-only.

    With no flags on a terminal, name and description are prompted for with
    their current values as defaults (read-only is left unchanged unless the
    flag is passed). Credentials are never touched here — use 'auth login' to
    re-enter an API key/secret.
    """
    try:
        profiles, _ = config.list_profiles()
    except config.ConfigError as e:
        raise fail(str(e), 2)
    if profile not in profiles:
        raise fail(
            f"No such profile: {profile}. Run 'frappe-cli auth list' to see profiles.",
            2,
        )
    current_desc = profiles[profile].get("description", "")

    # Nothing on the command line: prompt interactively, or refuse when there
    # is no terminal to prompt at (a flag would then be required).
    if name is None and description is None and read_only is None:
        if not sys.stdin.isatty():
            raise fail(
                "Nothing to change. Pass --name, --description and/or "
                "--read-only/--writable (this command only prompts on a terminal).",
                2,
            )
        name = typer.prompt("Profile name", default=profile)
        description = typer.prompt("Description", default=current_desc)

    try:
        if name is not None and name != profile:
            config.rename_profile(profile, name)
            profile = name
        if description is not None:
            config.set_description(profile, description)
        if read_only is not None:
            config.set_read_only(profile, read_only)
    except config.ConfigError as e:
        raise fail(str(e), 2)

    err_console.print(f"[green]updated[/green] profile '{profile}'")


@app.command("whoami")
def whoami(ctx: typer.Context) -> None:
    """Show the resolved site and logged-in user for the active profile."""
    c = get_ctx(ctx)
    try:
        creds = config.resolve(c.profile, interactive=c.is_tty)
    except config.ConfigError as e:
        raise fail(str(e), 2)
    try:
        with FrappeClient(
            creds.site, creds.wire_token, token_type=creds.token_type
        ) as client:
            user = client.get_logged_user()
    except FrappeError as e:
        raise fail(e.message)
    from ..output import emit_record

    record: dict[str, object] = {
        "site": creds.site,
        "user": user,
        "source": creds.source,
        "auth": "oauth" if creds.token_type == "bearer" else "api_key",
        "description": creds.description,
        "read_only": creds.read_only,
    }
    if creds.token_type == "bearer" and creds.expires_at:
        import datetime

        record["token_expires_at"] = datetime.datetime.fromtimestamp(
            creds.expires_at, tz=datetime.timezone.utc
        ).isoformat()
    emit_record(c, record)
