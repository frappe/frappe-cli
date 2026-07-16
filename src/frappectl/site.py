"""Normalized Frappe site URLs and endpoint path construction."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlsplit

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class InsecureCredentialTransport(ValueError):
    """Raised before a credential can be sent over remote plain HTTP."""


@dataclass(frozen=True)
class SiteURL:
    value: str

    @classmethod
    def parse(cls, raw: str) -> "SiteURL":
        value = raw.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            value = "https://" + value
        return cls(value)

    @property
    def host(self) -> str:
        return urlsplit(self.value).hostname or ""

    @property
    def is_local(self) -> bool:
        host = self.host.lower()
        return host in _LOCAL_HOSTS or host.endswith(".localhost")

    def require_secure_credentials(self) -> None:
        if urlsplit(self.value).scheme == "http" and not self.is_local:
            raise InsecureCredentialTransport(self.value)

    def __str__(self) -> str:
        return self.value


def endpoint_path(*segments: str, trailing_slash: bool = False) -> str:
    path = "/" + "/".join(quote(segment, safe="") for segment in segments)
    return path + "/" if trailing_slash else path
