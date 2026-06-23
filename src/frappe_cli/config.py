"""Profile + credential storage.

Resolution order for the active site/credentials:

1. Environment variables (``FRAPPE_SITE``, ``FRAPPE_API_KEY``, ``FRAPPE_API_SECRET``) always
   win. This is the headless / agent path and never touches the keyring.
2. A stored profile (selected with ``-s/--site`` or the configured default).
   The site URL lives in a plaintext config file; the ``key:secret`` token lives
   in the OS keyring. There is **no plaintext secret fallback** — a broken
   keyring means you must use environment variables.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

KEYRING_SERVICE = "frappe-cli"


class ConfigError(Exception):
    """Raised for unrecoverable configuration / credential problems."""


@dataclass
class Credentials:
    """A resolved site + token, ready to build a client from."""

    site: str
    api_key: str
    api_secret: str
    # Where these came from, for error messages: "env" or a profile name.
    source: str

    @property
    def token(self) -> str:
        return f"{self.api_key}:{self.api_secret}"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return Path(base) / "frappe"


def config_path() -> Path:
    return config_dir() / "config.json"


def _load() -> dict:
    path = config_path()
    if not path.exists():
        return {"default": None, "profiles": {}}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise ConfigError(f"Could not read config at {path}: {e}") from e
    data.setdefault("default", None)
    data.setdefault("profiles", {})
    return data


def _save(data: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    # Config holds only site URLs, but keep it user-only regardless.
    try:
        path.chmod(0o600)
    except OSError:
        pass


# --- keyring helpers -------------------------------------------------------


def _keyring():
    try:
        import keyring

        return keyring
    except Exception as e:  # pragma: no cover - import guard
        raise ConfigError(
            "The 'keyring' package is unavailable. Use environment variables "
            "(FRAPPE_SITE, FRAPPE_API_KEY, FRAPPE_API_SECRET) instead."
        ) from e


def _store_secret(profile: str, token: str) -> None:
    kr = _keyring()
    try:
        kr.set_password(KEYRING_SERVICE, profile, token)
    except Exception as e:
        raise ConfigError(
            "Could not store credentials in the OS keyring "
            f"({e}). Frappe CLI does not write secrets to disk. "
            "On headless machines use FRAPPE_SITE / FRAPPE_API_KEY / FRAPPE_API_SECRET."
        ) from e


def _read_secret(profile: str) -> str | None:
    kr = _keyring()
    try:
        return kr.get_password(KEYRING_SERVICE, profile)
    except Exception as e:
        raise ConfigError(
            f"Could not read credentials from the OS keyring ({e}). "
            "Use FRAPPE_SITE / FRAPPE_API_KEY / FRAPPE_API_SECRET instead."
        ) from e


def _delete_secret(profile: str) -> None:
    kr = _keyring()
    try:
        kr.delete_password(KEYRING_SERVICE, profile)
    except Exception:
        # Deleting a missing secret is fine.
        pass


# --- public profile API ----------------------------------------------------


def list_profiles() -> tuple[dict[str, dict], str | None]:
    data = _load()
    return data["profiles"], data["default"]


def add_profile(
    name: str, site: str, api_key: str, api_secret: str, make_default: bool = True
) -> None:
    data = _load()
    _store_secret(name, f"{api_key}:{api_secret}")
    data["profiles"][name] = {"site": site}
    if make_default or data["default"] is None:
        data["default"] = name
    _save(data)


def remove_profile(name: str) -> None:
    data = _load()
    if name not in data["profiles"]:
        raise ConfigError(f"No such profile: {name}")
    del data["profiles"][name]
    _delete_secret(name)
    if data["default"] == name:
        data["default"] = next(iter(data["profiles"]), None)
    _save(data)


def set_default(name: str) -> None:
    data = _load()
    if name not in data["profiles"]:
        raise ConfigError(f"No such profile: {name}")
    data["default"] = name
    _save(data)


def _normalize_site(site: str) -> str:
    site = site.strip().rstrip("/")
    if not site.startswith(("http://", "https://")):
        site = "https://" + site
    return site


def resolve(profile: str | None = None) -> Credentials:
    """Resolve credentials per the documented precedence."""

    env_site = os.environ.get("FRAPPE_SITE")
    # Env wins, but only when a profile wasn't explicitly requested.
    if profile is None and env_site:
        key = os.environ.get("FRAPPE_API_KEY")
        secret = os.environ.get("FRAPPE_API_SECRET")
        if not key or not secret:
            raise ConfigError(
                "FRAPPE_SITE is set but FRAPPE_API_KEY / FRAPPE_API_SECRET are missing."
            )
        return Credentials(_normalize_site(env_site), key, secret, source="env")

    profiles, default = list_profiles()
    name = profile or default
    if not name:
        raise ConfigError(
            "No site configured. Run 'frappe-cli auth login <url>' or set FRAPPE_SITE, "
            "FRAPPE_API_KEY and FRAPPE_API_SECRET."
        )
    if name not in profiles:
        raise ConfigError(
            f"No such profile: {name}. Run 'frappe-cli auth list' to see profiles."
        )

    token = _read_secret(name)
    if not token or ":" not in token:
        raise ConfigError(
            f"No stored credentials for profile '{name}'. "
            f"Run 'frappe-cli auth login' again for this site."
        )
    api_key, api_secret = token.split(":", 1)
    return Credentials(
        _normalize_site(profiles[name]["site"]), api_key, api_secret, source=name
    )
