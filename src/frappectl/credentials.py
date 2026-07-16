"""Credential providers consumed by the HTTP transport."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from .config import OAuthCredential

OAUTH_EXPIRY_MARGIN = 60.0


class CredentialProvider(Protocol):
    def authorization_header(self) -> str: ...

    def refresh(self) -> bool: ...


class CredentialRefreshError(Exception):
    """Raised when an expiring credential cannot be refreshed safely."""


class ApiKeyProvider:
    def __init__(self, api_key: str, api_secret: str):
        self._header = f"token {api_key}:{api_secret}"

    def authorization_header(self) -> str:
        return self._header

    def refresh(self) -> bool:
        return False


class OAuthProvider:
    """Own OAuth token rotation, refresh timing, and persistence."""

    def __init__(
        self,
        site: str,
        credential: OAuthCredential,
        refresh_service: Callable[[str, str, str], OAuthCredential],
        persist: Callable[[OAuthCredential], None],
        *,
        clock: Callable[[], float] = time.time,
    ):
        self._site = site
        self._credential = credential
        self._refresh_service = refresh_service
        self._persist = persist
        self._clock = clock

    def authorization_header(self) -> str:
        if (
            self._credential.refresh_token
            and self._credential.expires_at
            and self._credential.expires_at - OAUTH_EXPIRY_MARGIN <= self._clock()
        ):
            if not self._refresh(raise_on_failure=True):
                raise CredentialRefreshError("Could not refresh the OAuth session.")
        return f"Bearer {self._credential.access_token}"

    def refresh(self) -> bool:
        return self._refresh(raise_on_failure=False)

    def _refresh(self, *, raise_on_failure: bool) -> bool:
        if not self._credential.refresh_token:
            return False
        try:
            credential = self._refresh_service(
                self._site,
                self._credential.client_id,
                self._credential.refresh_token,
            )
        except Exception as e:
            if raise_on_failure:
                raise CredentialRefreshError(
                    "Could not refresh the OAuth session. Log in again."
                ) from e
            return False
        if not credential.refresh_token:
            credential = OAuthCredential(
                credential.access_token,
                self._credential.refresh_token,
                credential.expires_at,
                credential.client_id,
            )
        self._credential = credential
        try:
            self._persist(credential)
        except Exception:
            # The fresh token is still usable for this process. A broken
            # keyring must not turn a successful refresh into a failed request.
            pass
        return True
