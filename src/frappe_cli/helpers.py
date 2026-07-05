"""Shared parsing helpers: ``-f`` filters, ``--set`` assignments, default
fields derived from DocType meta.
"""

from __future__ import annotations

import json
from typing import Any

from .errors import UsageError

# Order matters: match the longest operators first.
_OPERATORS = [">=", "<=", "!=", "=", ">", "<"]

# Layout-only fieldtypes never make sense as list columns.
_LAYOUT_FIELDTYPES = {
    "Section Break",
    "Column Break",
    "Tab Break",
    "HTML",
    "Heading",
    "Button",
    "Fold",
}


def parse_filter(token: str) -> list:
    """Parse a single ``-f`` token into ``[field, op, value]``.

    Supports symbolic operators (``=``, ``!=``, ``>``, ``<``, ``>=``, ``<=``)
    and the word operator ``like`` (``-f 'subject like %report%'``). Anything
    richer (``in``, ``between``, child-table filters) goes through
    ``--filters-json``.
    """
    stripped = token.strip()
    lowered = stripped.lower()
    for word_op in (" not like ", " like "):
        idx = lowered.find(word_op)
        if idx != -1:
            field = stripped[:idx].strip()
            value = stripped[idx + len(word_op) :].strip()
            return [field, word_op.strip(), _coerce(value)]

    for op in _OPERATORS:
        idx = stripped.find(op)
        if idx > 0:
            field = stripped[:idx].strip()
            value = stripped[idx + len(op) :].strip()
            return [field, op, _coerce(value)]

    raise UsageError(
        f"Could not parse filter {token!r}. Use field=value, field>value, "
        "'field like %x%', or pass --filters-json for complex filters."
    )


def parse_filters(tokens: list[str]) -> list:
    return [parse_filter(t) for t in tokens]


def parse_filters_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise UsageError(f"--filters-json is not valid JSON: {e}") from e


def _coerce(value: str) -> Any:
    """Best-effort scalar coercion for filter / set values."""
    if value == "":
        return ""
    if (value[0] == value[-1]) and value[0] in {'"', "'"} and len(value) >= 2:
        return value[1:-1]
    low = value.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in {"null", "none"}:
        return None
    # Leave identifier-like values (leading zeros: pincodes, phone numbers, item
    # codes like "007") as strings — coercing to int would silently drop the
    # zeros and corrupt the written value. Quote to force a string otherwise.
    if not _has_leading_zero(value):
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass
    return value


def _has_leading_zero(value: str) -> bool:
    """True for integer-looking strings whose zeros must be preserved.

    "0" and decimals like "0.5" are fine; "007" / "0123" are not.
    """
    digits = value[1:] if value[:1] in {"+", "-"} else value
    return len(digits) > 1 and digits[0] == "0" and digits.isdigit()


def parse_set(assignments: list[str]) -> dict:
    """Parse repeated ``--set field=value`` into a dict of scalars."""
    out: dict[str, Any] = {}
    for item in assignments:
        if "=" not in item:
            raise UsageError(f"--set expects field=value, got {item!r}")
        field, value = item.split("=", 1)
        out[field.strip()] = _coerce(value)
    return out


def parse_method_params(params: list[str]) -> dict:
    """Parse gh-style ``-F key=value`` method parameters."""
    return parse_set(params)


def default_fields(meta: dict) -> list[str]:
    """Compute Desk-like default list columns from DocType meta.

    name + title field + ``in_list_view`` columns.
    """
    fields: list[str] = ["name"]
    title_field = meta.get("title_field")
    if title_field:
        fields.append(title_field)

    for df in meta.get("fields", []):
        if not df.get("in_list_view"):
            continue
        if df.get("fieldtype") in _LAYOUT_FIELDTYPES:
            continue
        fname = df.get("fieldname")
        if fname and fname not in fields:
            fields.append(fname)

    # status is almost always useful and frequently not in_list_view.
    return list(dict.fromkeys(fields))


def parse_fields(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    if raw.strip() == "*":
        return ["*"]
    return [f.strip() for f in raw.split(",") if f.strip()]
