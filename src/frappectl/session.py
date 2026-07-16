"""Bridge from CLI context to a configured :class:`FrappeClient`."""

from __future__ import annotations

from functools import partial

from . import config
from .client import FrappeClient
from .credentials import ApiKeyProvider, OAuthProvider
from .output import ApplicationContext, fail


class ClientFactory:
    def create(
        self, profile: str | None, *, interactive: bool, debug: bool
    ) -> FrappeClient:
        try:
            creds = config.resolve(profile, interactive=interactive)
        except config.ConfigError as e:
            raise fail(str(e), 2)
        provider = (
            OAuthProvider(
                creds.site,
                config.OAuthCredential(
                    creds.access_token,
                    creds.refresh_token,
                    creds.expires_at,
                    creds.client_id,
                ),
                _refresh_oauth,
                partial(config.store_oauth_credential, creds.source),
            )
            if creds.token_type == "bearer"
            else ApiKeyProvider(creds.api_key, creds.api_secret)
        )
        return FrappeClient(
            creds.site,
            creds.wire_token,
            debug=debug,
            read_only=creds.read_only,
            token_type=creds.token_type,
            credential_provider=provider,
        )


def get_client(ctx: ApplicationContext) -> FrappeClient:
    return ctx.client_factory.create(
        ctx.profile, interactive=ctx.is_tty, debug=ctx.debug
    )


def _refresh_oauth(
    site: str, client_id: str, refresh_token: str
) -> config.OAuthCredential:
    from . import oauth

    tokens = oauth.refresh(site, client_id, refresh_token)
    return config.OAuthCredential(
        tokens.access_token, tokens.refresh_token, tokens.expires_at, client_id
    )
