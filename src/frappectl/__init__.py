"""frappectl — a command-line client for Frappe sites."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("frappectl")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
