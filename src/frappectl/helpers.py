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


def parse_filter(token: str) -> list[Any]:
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


def parse_filters(tokens: list[str]) -> list[Any]:
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


def parse_set(assignments: list[str]) -> dict[str, Any]:
    """Parse repeated ``--set field=value`` into a dict of scalars."""
    out: dict[str, Any] = {}
    for item in assignments:
        if "=" not in item:
            raise UsageError(f"--set expects field=value, got {item!r}")
        field, value = item.split("=", 1)
        out[field.strip()] = _coerce(value)
    return out


def parse_method_params(params: list[str]) -> dict[str, Any]:
    """Parse gh-style ``-F`` params into a dict of typed values.

    Two forms are supported per token:

    * ``key=value`` — best-effort scalar coercion (numbers, booleans, ``null``,
      otherwise a string), matching ``--set`` / filter values::

          -F limit=100          # int 100
          -F unread=true        # bool True

    * ``key:=value`` — ``value`` is parsed as raw JSON, so objects and arrays
      pass through structurally::

          -F filter:='{"inMailbox":"a"}'
          -F emails:='["a@example.com","b@example.com"]'

    Structured values are sent as native JSON in a request body and JSON-encoded
    into the query string for GET requests (see :func:`_clean_params` in the
    transport layer), so the same ``-F`` behaves identically either way.
    """
    out: dict[str, Any] = {}
    for item in params:
        eq = item.find("=")
        if eq < 1:
            raise UsageError(f"-F expects key=value or key:=value, got {item!r}")
        if item[eq - 1] == ":":
            key = item[: eq - 1].strip()
            raw = item[eq + 1 :]
            if not key:
                raise UsageError(f"-F expects key:=value, got {item!r}")
            try:
                out[key] = json.loads(raw)
            except json.JSONDecodeError as e:
                raise UsageError(
                    f"-F {key}:= expects a JSON value, got {raw!r}: {e}"
                ) from e
        else:
            key = item[:eq].strip()
            if not key:
                raise UsageError(f"-F expects key=value, got {item!r}")
            out[key] = _coerce(item[eq + 1 :])
    return out


def default_fields(meta: dict[str, Any]) -> list[str]:
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
