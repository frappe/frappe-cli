"""Error types and server-message extraction.

Frappe's v2 API returns errors as ``{"errors": [{"type", "message",
"exception", ...}]}``. Older endpoints (and some methods) lean on
``_server_messages`` — a JSON-encoded list of JSON-encoded dicts whose
``message`` often contains HTML. We strip both down to clean, human text.
"""

from __future__ import annotations

import json
import re

_TAG_RE = re.compile(r"<[^>]+>")

# Ordered (pattern, hint) pairs. The first whose pattern is found in a
# (lower-cased) error message wins, so its tip is appended to the error. The
# message is the only signal we have at the print site, but it already carries
# the semantics we need ("not found", "permission", "mandatory", …). Hints stay
# generic — they point at the command that resolves the class of error rather
# than guessing the exact DocType/field.
_HINTS: tuple[tuple[str, str], ...] = (
    (
        "authentication failed",
        "Set FRAPPE_SITE/FRAPPE_API_KEY/FRAPPE_API_SECRET, or run "
        "'frappe-cli auth login <url>'. Check the active profile with 'frappe-cli auth whoami'.",
    ),
    (
        "permission",
        "Confirm who you're authenticated as with 'frappe-cli auth whoami'.",
    ),
    (
        "not permitted",
        "Confirm who you're authenticated as with 'frappe-cli auth whoami'.",
    ),
    (
        "could not reach",
        "Check the site URL and that it's reachable; 'frappe-cli auth whoami' shows the resolved site.",
    ),
    (
        "does not exist",
        "List records with 'frappe-cli doc list <DocType>', or verify the DocType "
        "with 'frappe-cli doctype list'.",
    ),
    (
        "not found",
        "List records with 'frappe-cli doc list <DocType>', or verify the DocType "
        "with 'frappe-cli doctype list'.",
    ),
    (
        "mandatory",
        "See which fields are required with 'frappe-cli doctype show <DocType>'.",
    ),
    (
        "value missing",
        "See which fields are required with 'frappe-cli doctype show <DocType>'.",
    ),
    (
        "unknown column",
        "List valid fieldnames with 'frappe-cli doctype show <DocType>'.",
    ),
    (
        "invalid field",
        "List valid fieldnames with 'frappe-cli doctype show <DocType>'.",
    ),
)


def error_hint(message: str | None) -> str | None:
    """Return a one-line tip pointing at a command that can help, or None.

    Conservative by design: only well-known error shapes get a hint, so usage
    errors and unrecognised messages stay quiet.
    """
    if not message:
        return None
    low = message.lower()
    for pattern, hint in _HINTS:
        if pattern in low:
            return hint
    return None


class FrappeError(Exception):
    """A server-side error, already reduced to a clean message."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class UsageError(FrappeError):
    """A bad-input error that should map to exit code 2."""


def strip_html(text: str) -> str:
    text = _TAG_RE.sub("", text)
    # Collapse the few entities Frappe commonly emits.
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&#39;", "'"), ("&quot;", '"')):
        text = text.replace(a, b)
    return text.strip()


def _from_server_messages(raw) -> list[str]:
    out: list[str] = []
    try:
        messages = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return out
    for m in messages or []:
        try:
            obj = json.loads(m) if isinstance(m, str) else m
        except (json.JSONDecodeError, TypeError):
            obj = m
        if isinstance(obj, dict):
            text = obj.get("message") or obj.get("title") or ""
        else:
            text = str(obj)
        text = strip_html(text)
        if text:
            out.append(text)
    return out


def _last_traceback_line(exc: str) -> str | None:
    lines = [ln for ln in exc.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    last = lines[-1]
    # "frappe.exceptions.MandatoryError: [ToDo, ...]: description" -> right side
    if ": " in last and "." in last.split(":", 1)[0]:
        return last.split(": ", 1)[1].strip() or last
    return last


def extract_message(body: dict | list | str | None, status_code: int) -> str:
    """Reduce a Frappe error body to the best single human message."""

    if isinstance(body, str):
        text = strip_html(body)
        return text or f"HTTP {status_code}"

    if isinstance(body, dict):
        # v2 style.
        errors = body.get("errors")
        if isinstance(errors, list) and errors:
            parts: list[str] = []
            for err in errors:
                if not isinstance(err, dict):
                    parts.append(strip_html(str(err)))
                    continue
                msg = err.get("message")
                if msg:
                    parts.append(strip_html(msg))
                elif err.get("exception"):
                    line = _last_traceback_line(err["exception"])
                    if line:
                        parts.append(strip_html(line))
            parts = [p for p in parts if p]
            if parts:
                return "\n".join(dict.fromkeys(parts))

        server = _from_server_messages(body.get("_server_messages"))
        if server:
            return "\n".join(dict.fromkeys(server))

        for key in ("exception", "message", "exc_type", "_error_message"):
            val = body.get(key)
            if isinstance(val, str) and val.strip():
                if key == "exception":
                    line = _last_traceback_line(val)
                    if line:
                        return strip_html(line)
                return strip_html(val)

    return f"HTTP {status_code}"
