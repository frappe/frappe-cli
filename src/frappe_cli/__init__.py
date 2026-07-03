"""frappe-cli — Frappe CLI."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("frappe-cli")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
