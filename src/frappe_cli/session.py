"""Bridge from CLI context to a configured :class:`FrappeClient`."""

from __future__ import annotations

from . import config
from .client import FrappeClient
from .errors import FrappeError
from .output import Ctx, fail


def get_client(ctx: Ctx) -> FrappeClient:
    try:
        creds = config.resolve(ctx.profile, interactive=ctx.is_tty)
    except config.ConfigError as e:
        raise fail(str(e), 2)
    return FrappeClient(creds.site, creds.token)


def run(fn):
    """Wrap a command body so FrappeError surfaces as a clean exit-1."""
    try:
        return fn()
    except FrappeError as e:
        raise fail(e.message)
    except config.ConfigError as e:
        raise fail(str(e), 2)
