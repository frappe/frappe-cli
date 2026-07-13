"""Bridge from CLI context to a configured :class:`FrappeClient`."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from . import config
from .client import FrappeClient
from .errors import FrappeError
from .output import Ctx, fail

T = TypeVar("T")


def get_client(ctx: Ctx) -> FrappeClient:
    try:
        creds = config.resolve(ctx.profile, interactive=ctx.is_tty)
    except config.ConfigError as e:
        raise fail(str(e), 2)
    on_unauthorized = _oauth_refresher(creds) if creds.token_type == "bearer" else None
    return FrappeClient(
        creds.site,
        creds.wire_token,
        debug=ctx.debug,
        read_only=creds.read_only,
        token_type=creds.token_type,
        on_unauthorized=on_unauthorized,
    )


def _oauth_refresher(creds: config.Credentials) -> Callable[[], str | None]:
    """A 401 handler that refreshes an OAuth access token and persists it.

    Resolution already refreshes proactively when the token has expired; this is
    the reactive backstop for a token the server rejects early (revoked,
    clock skew). Returns None on any failure so the original 401 surfaces.
    """
    # Track the refresh token locally so a rotated token is used on a retry.
    state = {"refresh_token": creds.refresh_token}

    def refresh_once() -> str | None:
        from . import oauth

        try:
            tokens = oauth.refresh(creds.site, creds.client_id, state["refresh_token"])
        except oauth.OAuthError:
            return None
        state["refresh_token"] = tokens.refresh_token or state["refresh_token"]
        try:
            config.update_oauth_tokens(creds.source, tokens)
        except config.ConfigError:
            # A keyring write failure must not defeat an otherwise-valid token.
            pass
        return tokens.access_token

    return refresh_once


def run(fn: Callable[[], T]) -> T:
    """Wrap a command body so FrappeError surfaces as a clean exit-1."""
    try:
        return fn()
    except FrappeError as e:
        raise fail(e.message)
    except config.ConfigError as e:
        raise fail(str(e), 2)
