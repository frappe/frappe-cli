"""Public Frappe client facade."""

from __future__ import annotations

import time

from .transport import Document, Filters, FrappeTransport

__all__ = ["Document", "Filters", "FrappeClient", "time"]


class FrappeClient(FrappeTransport):
    """Compatibility facade while API resources migrate off the transport."""
