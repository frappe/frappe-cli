"""Profile + credential storage.

Resolution order for the active site/credentials:

1. Environment variables (``FRAPPE_SITE``, ``FRAPPE_API_KEY``, ``FRAPPE_API_SECRET``) always
   win. This is the headless / agent path and never touches the keyring.
2. A stored profile (selected with ``-s/--site`` or the configured default).
   The site URL lives in a plaintext config file; the credential lives in the OS
   keyring. Two credential shapes are supported: an API-key ``key:secret`` string
   (the default) or, for profiles tagged ``"auth": "oauth"``, a JSON blob of
   OAuth tokens (``see`` :mod:`frappectl.oauth`). There is **no plaintext secret
   fallback** — a broken keyring means you must use environment variables.

OAuth access tokens are short-lived, so :func:`resolve` refreshes them
proactively (before they expire) and persists the new tokens before returning.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Callable, Protocol, TypeAlias, cast

from .site import SiteURL

if TYPE_CHECKING:
    from . import oauth

KEYRING_SERVICE = "frappectl"
# Pre-rename installs stored secrets under this service name; reads fall back
# to it (and migrate forward) so existing logins survive the rename.
_LEGACY_KEYRING_SERVICE = "frappe-cli"


class ConfigError(Exception):
    """Raised for unrecoverable configuration / credential problems."""


class AuthKind(str, Enum):
    API_KEY = "api_key"
    OAUTH = "oauth"


@dataclass(frozen=True)
class Profile:
    name: str
    site: SiteURL
    description: str = ""
    read_only: bool = False
    auth: AuthKind = AuthKind.API_KEY


@dataclass(frozen=True)
class ApiKeyCredential:
    api_key: str
    api_secret: str

    def serialize(self) -> str:
        return f"{self.api_key}:{self.api_secret}"


@dataclass(frozen=True)
class OAuthCredential:
    access_token: str
    refresh_token: str
    expires_at: float
    client_id: str

    def serialize(self) -> str:
        return json.dumps(
            {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at,
                "token_type": "bearer",
                "client_id": self.client_id,
            }
        )


StoredCredential: TypeAlias = ApiKeyCredential | OAuthCredential
ConfigData: TypeAlias = dict[str, Any]


class ConfigStore(Protocol):
    def load(self) -> ConfigData: ...

    def save(self, data: ConfigData) -> None: ...


class SecretStore(Protocol):
    def get(self, profile: str) -> str | None: ...

    def set(self, profile: str, secret: str) -> None: ...

    def delete(self, profile: str) -> None: ...


class JsonConfigStore:
    def __init__(self, path_factory: Callable[[], Path] | None = None):
        self._path_factory = path_factory or config_path

    def load(self) -> ConfigData:
        path = self._path_factory()
        if not path.exists():
            return {"default": None, "profiles": {}}
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            raise ConfigError(f"Could not read config at {path}: {e}") from e
        data.setdefault("default", None)
        data.setdefault("profiles", {})
        return cast("ConfigData", data)

    def save(self, data: ConfigData) -> None:
        path = self._path_factory()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")
        try:
            path.chmod(0o600)
        except OSError:
            pass


class KeyringSecretStore:
    def __init__(self, keyring_factory: Callable[[], ModuleType] | None = None):
        self._keyring_factory = keyring_factory or _keyring

    def get(self, profile: str) -> str | None:
        kr = self._keyring_factory()
        try:
            secret = cast("str | None", kr.get_password(KEYRING_SERVICE, profile))
            if secret is None:
                secret = cast(
                    "str | None", kr.get_password(_LEGACY_KEYRING_SERVICE, profile)
                )
                if secret is not None:
                    kr.set_password(KEYRING_SERVICE, profile, secret)
                    self._delete_from(kr, _LEGACY_KEYRING_SERVICE, profile)
            return secret
        except Exception as e:
            raise ConfigError(
                f"Could not read credentials from the OS keyring ({e}). "
                "Use FRAPPE_SITE / FRAPPE_API_KEY / FRAPPE_API_SECRET instead."
            ) from e

    def set(self, profile: str, secret: str) -> None:
        try:
            self._keyring_factory().set_password(KEYRING_SERVICE, profile, secret)
        except Exception as e:
            raise ConfigError(
                "Could not store credentials in the OS keyring "
                f"({e}). frappectl does not write secrets to disk. "
                "On headless machines use FRAPPE_SITE / FRAPPE_API_KEY / "
                "FRAPPE_API_SECRET."
            ) from e

    def delete(self, profile: str) -> None:
        kr = self._keyring_factory()
        self._delete_from(kr, KEYRING_SERVICE, profile)
        self._delete_from(kr, _LEGACY_KEYRING_SERVICE, profile)

    @staticmethod
    def _delete_from(kr: ModuleType, service: str, profile: str) -> None:
        try:
            kr.delete_password(service, profile)
        except Exception:
            # Backends disagree on how deleting a missing password is reported.
            pass


@dataclass(frozen=True)
class ProfileCollection:
    profiles: dict[str, Profile]
    default: str | None


class ProfileRepository:
    """Own profile metadata and its matching keyring secret as one unit."""

    def __init__(self, config_store: ConfigStore, secret_store: SecretStore):
        self.config_store = config_store
        self.secret_store = secret_store

    def list(self) -> ProfileCollection:
        data = self.config_store.load()
        profiles = {
            name: _profile_from_entry(name, entry)
            for name, entry in cast(
                "dict[str, dict[str, Any]]", data["profiles"]
            ).items()
        }
        return ProfileCollection(profiles, cast("str | None", data["default"]))

    def add(
        self,
        profile: Profile,
        credential: StoredCredential,
        *,
        make_default: bool = True,
    ) -> None:
        data = self.config_store.load()
        previous_secret = self.secret_store.get(profile.name)
        self.secret_store.set(profile.name, credential.serialize())
        data["profiles"][profile.name] = _profile_entry(profile)
        if make_default or data["default"] is None:
            data["default"] = profile.name
        try:
            self.config_store.save(data)
        except Exception as e:
            self._restore_secret(profile.name, previous_secret)
            raise ConfigError(
                f"Could not save profile '{profile.name}'; its credential was restored."
            ) from e

    def rename(self, old_name: str, new_name: str) -> None:
        data = self.config_store.load()
        profiles = cast("dict[str, dict[str, Any]]", data["profiles"])
        if old_name not in profiles:
            raise ConfigError(f"No such profile: {old_name}")
        if new_name == old_name:
            return
        if not new_name:
            raise ConfigError("New profile name must not be empty.")
        if new_name in profiles:
            raise ConfigError(f"A profile named '{new_name}' already exists.")

        secret = self.secret_store.get(old_name)
        previous_new_secret = self.secret_store.get(new_name)
        if secret is not None:
            self.secret_store.set(new_name, secret)
        profiles[new_name] = profiles.pop(old_name)
        if data["default"] == old_name:
            data["default"] = new_name
        try:
            self.config_store.save(data)
        except Exception as e:
            self._restore_secret(new_name, previous_new_secret)
            raise ConfigError(
                f"Could not rename profile '{old_name}'; its credential was restored."
            ) from e
        if secret is not None:
            try:
                self.secret_store.delete(old_name)
            except Exception as e:
                profiles[old_name] = profiles.pop(new_name)
                if data["default"] == new_name:
                    data["default"] = old_name
                self.config_store.save(data)
                self._restore_secret(new_name, previous_new_secret)
                raise ConfigError(
                    f"Could not rename profile '{old_name}'; config was restored."
                ) from e

    def remove(self, name: str) -> None:
        data = self.config_store.load()
        original = deepcopy(data)
        profiles = cast("dict[str, dict[str, Any]]", data["profiles"])
        if name not in profiles:
            raise ConfigError(f"No such profile: {name}")
        del profiles[name]
        if data["default"] == name:
            data["default"] = next(iter(profiles), None)
        self.config_store.save(data)
        try:
            self.secret_store.delete(name)
        except Exception as e:
            try:
                self.config_store.save(original)
            except Exception:
                pass
            raise ConfigError(
                f"Could not remove profile '{name}'; config was restored."
            ) from e

    def set_default(self, name: str) -> None:
        data = self.config_store.load()
        if name not in data["profiles"]:
            raise ConfigError(f"No such profile: {name}")
        data["default"] = name
        self.config_store.save(data)

    def update(self, profile: Profile) -> None:
        data = self.config_store.load()
        if profile.name not in data["profiles"]:
            raise ConfigError(f"No such profile: {profile.name}")
        data["profiles"][profile.name] = _profile_entry(profile)
        self.config_store.save(data)

    def credential(self, name: str) -> str | None:
        return self.secret_store.get(name)

    def store_credential(self, name: str, credential: StoredCredential) -> None:
        self.secret_store.set(name, credential.serialize())

    def _restore_secret(self, name: str, secret: str | None) -> None:
        try:
            if secret is None:
                self.secret_store.delete(name)
            else:
                self.secret_store.set(name, secret)
        except Exception:
            pass


def _profile_from_entry(name: str, entry: dict[str, Any]) -> Profile:
    return Profile(
        name=name,
        site=SiteURL.parse(str(entry["site"])),
        description=str(entry.get("description", "")),
        read_only=bool(entry.get("read_only", False)),
        auth=AuthKind.OAUTH if entry.get("auth") == "oauth" else AuthKind.API_KEY,
    )


def _profile_entry(profile: Profile) -> dict[str, Any]:
    entry: dict[str, Any] = {"site": str(profile.site)}
    if profile.auth is AuthKind.OAUTH:
        entry["auth"] = "oauth"
    if profile.description:
        entry["description"] = profile.description
    if profile.read_only:
        entry["read_only"] = True
    return entry


def _repository() -> ProfileRepository:
    return ProfileRepository(JsonConfigStore(), KeyringSecretStore())


@dataclass(frozen=True)
class Credentials:
    """A resolved site plus exactly one valid credential shape."""

    site: str
    credential: StoredCredential
    source: str
    description: str = ""
    read_only: bool = False

    @property
    def api_key(self) -> str:
        return (
            self.credential.api_key
            if isinstance(self.credential, ApiKeyCredential)
            else ""
        )

    @property
    def api_secret(self) -> str:
        return (
            self.credential.api_secret
            if isinstance(self.credential, ApiKeyCredential)
            else ""
        )

    @property
    def access_token(self) -> str:
        return (
            self.credential.access_token
            if isinstance(self.credential, OAuthCredential)
            else ""
        )

    @property
    def refresh_token(self) -> str:
        return (
            self.credential.refresh_token
            if isinstance(self.credential, OAuthCredential)
            else ""
        )

    @property
    def expires_at(self) -> float:
        return (
            self.credential.expires_at
            if isinstance(self.credential, OAuthCredential)
            else 0.0
        )

    @property
    def client_id(self) -> str:
        return (
            self.credential.client_id
            if isinstance(self.credential, OAuthCredential)
            else ""
        )

    @property
    def token_type(self) -> str:
        return "bearer" if isinstance(self.credential, OAuthCredential) else "token"

    @property
    def token(self) -> str:
        return self.credential.serialize()

    @property
    def wire_token(self) -> str:
        """The credential the client puts on the wire, per ``token_type``."""
        return self.access_token if self.token_type == "bearer" else self.token


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return Path(base) / "frappe"


def config_path() -> Path:
    return config_dir() / "config.json"


def _load() -> dict[str, Any]:
    return JsonConfigStore().load()


def _save(data: dict[str, Any]) -> None:
    JsonConfigStore().save(data)


def _keyring() -> ModuleType:
    try:
        import keyring

        return keyring
    except Exception as e:  # pragma: no cover - import guard
        raise ConfigError(
            "The 'keyring' package is unavailable. Use environment variables "
            "(FRAPPE_SITE, FRAPPE_API_KEY, FRAPPE_API_SECRET) instead."
        ) from e


def _store_secret(profile: str, token: str) -> None:
    KeyringSecretStore().set(profile, token)


def _read_secret(profile: str) -> str | None:
    return KeyringSecretStore().get(profile)


def _delete_secret(profile: str) -> None:
    KeyringSecretStore().delete(profile)


def _delete_legacy_secret(profile: str) -> None:
    KeyringSecretStore._delete_from(_keyring(), _LEGACY_KEYRING_SERVICE, profile)


def list_profiles() -> tuple[dict[str, dict[str, Any]], str | None]:
    collection = _repository().list()
    return (
        {
            name: _profile_entry(profile)
            for name, profile in collection.profiles.items()
        },
        collection.default,
    )


def add_profile(
    name: str,
    site: str,
    api_key: str,
    api_secret: str,
    make_default: bool = True,
    description: str = "",
    read_only: bool = False,
) -> None:
    _repository().add(
        Profile(name, SiteURL.parse(site), description, read_only),
        ApiKeyCredential(api_key, api_secret),
        make_default=make_default,
    )


def add_oauth_profile(
    name: str,
    site: str,
    client_id: str,
    tokens: oauth.Tokens,
    make_default: bool = True,
    description: str = "",
    read_only: bool = False,
) -> None:
    """Store an OAuth profile: a JSON token blob in the keyring, tagged config.

    The config entry gets ``"auth": "oauth"``; the keyring holds
    ``{access_token, refresh_token, expires_at, token_type, client_id}`` under
    the same service/name an API-key profile would use. ``client_id`` is
    persisted so later logins reuse the same registered client.
    """
    _repository().add(
        Profile(name, SiteURL.parse(site), description, read_only, AuthKind.OAUTH),
        OAuthCredential(
            tokens.access_token,
            tokens.refresh_token,
            tokens.expires_at,
            client_id,
        ),
        make_default=make_default,
    )


def update_oauth_tokens(name: str, tokens: oauth.Tokens) -> None:
    """Persist refreshed OAuth tokens, preserving the stored ``client_id``.

    A refresh response may omit a new refresh token (Frappe reuses the old one),
    so the previous refresh token is kept when the fresh blob lacks one.
    """
    existing = _read_oauth_blob(name)
    client_id = existing.get("client_id", "")
    if not tokens.refresh_token:
        tokens = tokens.with_refresh_token(existing.get("refresh_token", ""))
    _repository().store_credential(
        name,
        OAuthCredential(
            tokens.access_token,
            tokens.refresh_token,
            tokens.expires_at,
            client_id,
        ),
    )


def store_oauth_credential(name: str, credential: OAuthCredential) -> None:
    """Persist an OAuth provider's current credential."""
    _repository().store_credential(name, credential)


def oauth_client_id(name: str) -> str | None:
    """The client_id stored for an OAuth profile, if any (for reuse on login)."""
    return _read_oauth_blob(name).get("client_id") or None


def oauth_access_token(name: str) -> str | None:
    """The stored OAuth access token for a profile, if any (for revocation)."""
    return _read_oauth_blob(name).get("access_token") or None


def _oauth_blob(tokens: oauth.Tokens, client_id: str) -> str:
    return json.dumps(
        {
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "expires_at": tokens.expires_at,
            "token_type": tokens.token_type,
            "client_id": client_id,
        }
    )


def _read_oauth_blob(name: str) -> dict[str, Any]:
    raw = _repository().credential(name)
    if not raw:
        return {}
    try:
        blob = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return cast("dict[str, Any]", blob) if isinstance(blob, dict) else {}


def rename_profile(name: str, new_name: str) -> None:
    """Rename a profile, moving its secret and default pointer with it."""
    _repository().rename(name, new_name)


def set_description(name: str, description: str) -> None:
    """Set (or clear, with an empty string) a profile's description."""
    collection = _repository().list()
    try:
        profile = collection.profiles[name]
    except KeyError as e:
        raise ConfigError(f"No such profile: {name}") from e
    _repository().update(
        Profile(name, profile.site, description, profile.read_only, profile.auth)
    )


def set_read_only(name: str, read_only: bool) -> None:
    """Mark a profile read-only (or clear the mark)."""
    collection = _repository().list()
    try:
        profile = collection.profiles[name]
    except KeyError as e:
        raise ConfigError(f"No such profile: {name}") from e
    _repository().update(
        Profile(name, profile.site, profile.description, read_only, profile.auth)
    )


def remove_profile(name: str) -> None:
    _repository().remove(name)


def set_default(name: str) -> None:
    _repository().set_default(name)


def _env_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_site(site: str) -> str:
    """Compatibility shim for callers that have not migrated to ``SiteURL``."""
    return str(SiteURL.parse(site))


class ProfileResolver:
    """Resolve environment/profile precedence without owning persistence."""

    def resolve(
        self, profile: str | None = None, interactive: bool = True
    ) -> Credentials:
        return _resolve(profile, interactive)


def resolve(profile: str | None = None, interactive: bool = True) -> Credentials:
    """Compatibility shim over the default profile resolver."""
    return ProfileResolver().resolve(profile, interactive)


def _resolve(profile: str | None = None, interactive: bool = True) -> Credentials:
    """Resolve credentials per the documented precedence.

    The configured default profile is a convenience for humans at a terminal.
    When ``interactive`` is false (piped / agent / script invocation) the
    default is only honoured when it is unambiguous — i.e. exactly one profile
    is authenticated. With more than one profile the caller must pick a site
    explicitly with ``-s/--site`` or the ``FRAPPE_*`` environment variables.
    """

    env_site = os.environ.get("FRAPPE_SITE")
    if profile is None and env_site:
        key = os.environ.get("FRAPPE_API_KEY")
        secret = os.environ.get("FRAPPE_API_SECRET")
        if not key or not secret:
            raise ConfigError(
                "FRAPPE_SITE is set but FRAPPE_API_KEY / FRAPPE_API_SECRET are missing."
            )
        return Credentials(
            _normalize_site(env_site),
            ApiKeyCredential(key, secret),
            source="env",
            read_only=_env_truthy(os.environ.get("FRAPPE_READ_ONLY")),
        )

    profiles, default = list_profiles()
    # Non-interactive runs must be unambiguous. A single authenticated profile
    # has no ambiguity, so it is used; with several, agents/scripts must name
    # the site they operate on rather than lean on the configured default.
    if profile is None and not interactive and len(profiles) > 1:
        raise ConfigError(
            "Multiple profiles are configured. Non-interactive invocations must "
            "pick a site explicitly: pass -s/--site <profile>, or set FRAPPE_SITE, "
            "FRAPPE_API_KEY and FRAPPE_API_SECRET. The configured default "
            "profile is only auto-selected interactively or when it is the only one."
        )
    if profile is None and not interactive and len(profiles) == 1:
        default = next(iter(profiles))
    name = profile or default
    if not name:
        raise ConfigError(
            "No site configured. Run 'frappectl auth login <url>' or set FRAPPE_SITE, "
            "FRAPPE_API_KEY and FRAPPE_API_SECRET."
        )
    if name not in profiles:
        raise ConfigError(
            f"No such profile: {name}. Run 'frappectl auth list' to see profiles."
        )

    if profiles[name].get("auth") == "oauth":
        return _resolve_oauth(name, profiles[name])

    token = _read_secret(name)
    if not token or ":" not in token:
        raise ConfigError(
            f"No stored credentials for profile '{name}'. "
            f"Run 'frappectl auth login' again for this site."
        )
    api_key, api_secret = token.split(":", 1)
    return Credentials(
        _normalize_site(profiles[name]["site"]),
        ApiKeyCredential(api_key, api_secret),
        source=name,
        description=profiles[name].get("description", ""),
        read_only=bool(profiles[name].get("read_only", False)),
    )


def _resolve_oauth(name: str, entry: dict[str, Any]) -> Credentials:
    """Resolve stored OAuth state; the provider owns refresh timing."""
    blob = _read_oauth_blob(name)
    access_token = blob.get("access_token", "")
    refresh_token = blob.get("refresh_token", "")
    client_id = blob.get("client_id", "")
    try:
        expires_at = float(blob.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0.0
    site = _normalize_site(entry["site"])

    if not access_token:
        raise ConfigError(
            f"No stored OAuth credentials for profile '{name}'. "
            f"Run 'frappectl auth login {site}' again for this site."
        )

    return Credentials(
        site=site,
        credential=OAuthCredential(access_token, refresh_token, expires_at, client_id),
        source=name,
        description=entry.get("description", ""),
        read_only=bool(entry.get("read_only", False)),
    )
