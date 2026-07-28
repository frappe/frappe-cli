import json

import pytest

from frappectl.output import _JSON_WIDTH, print_json

CASES = [
    "Administrator",
    42,
    None,
    True,
    [],
    {},
    ["ToDo", "User", "Item"],
    {"name": "X", "status": "Open"},
    {
        "items": [
            {"item_code": f"ITEM-{i:03d}", "qty": i, "rate": i * 1.5} for i in range(20)
        ]
    },
    {"deep": {"a": {"b": {"c": ["x" * 200, {"d": [1, 2, 3]}]}}}},
    {"unicode": "café ☕", "quote": 'a "quoted" value', "newline": "line\nbreak"},
]


@pytest.mark.parametrize("data", CASES)
def test_print_json_round_trips(data, capsys):
    print_json(data)
    assert json.loads(capsys.readouterr().out) == data


@pytest.mark.parametrize("data", CASES)
def test_print_json_respects_the_width_limit(data, capsys):
    print_json(data)
    lines = capsys.readouterr().out.splitlines()
    # A line holding one long scalar can exceed the limit. Nothing can split it.
    splittable = [line for line in lines if "{" in line or "[" in line]
    assert all(len(line) <= _JSON_WIDTH for line in splittable)


def test_print_json_keeps_a_short_value_on_one_line(capsys):
    print_json({"items": [{"item_code": "A", "qty": 1}]})
    assert capsys.readouterr().out == '{"items":[{"item_code":"A","qty":1}]}\n'


def test_print_json_falls_back_when_the_layout_breaks(capsys, monkeypatch):
    monkeypatch.setattr("frappectl.output._dumps", lambda value, level: "{broken")
    print_json({"name": "X"})
    assert json.loads(capsys.readouterr().out) == {"name": "X"}


def test_print_json_expands_a_wide_value(capsys):
    print_json({"tags": ["tag-" + "x" * 40 for _ in range(6)]})
    out = capsys.readouterr().out
    assert out.startswith("{\n")
    assert len(out.splitlines()) > 1
