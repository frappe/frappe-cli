import pytest

from frappectl import helpers
from frappectl.errors import UsageError


@pytest.mark.parametrize(
    "token,expected",
    [
        ("status=Paid", ["status", "=", "Paid"]),
        ("grand_total>1000", ["grand_total", ">", 1000]),
        ("amount>=10.5", ["amount", ">=", 10.5]),
        ("qty<=3", ["qty", "<=", 3]),
        ("name!=foo", ["name", "!=", "foo"]),
        ("subject like %report%", ["subject", "like", "%report%"]),
        ("subject not like %x%", ["subject", "not like", "%x%"]),
        ('label="quoted value"', ["label", "=", "quoted value"]),
        ("flag=true", ["flag", "=", True]),
        ("blank=null", ["blank", "=", None]),
    ],
)
def test_parse_filter(token, expected):
    assert helpers.parse_filter(token) == expected


def test_parse_filter_bad():
    with pytest.raises(UsageError):
        helpers.parse_filter("justaword")


def test_parse_set():
    assert helpers.parse_set(["a=1", "b=x", "c=true"]) == {"a": 1, "b": "x", "c": True}


@pytest.mark.parametrize(
    "token,expected",
    [
        # Leading zeros must survive: pincodes, phone numbers, item codes.
        ("pincode=01234", ["pincode", "=", "01234"]),
        ("phone=0123456789", ["phone", "=", "0123456789"]),
        ("code=007", ["code", "=", "007"]),
        ("neg=-007", ["neg", "=", "-007"]),
        ("n=0", ["n", "=", 0]),
        ("f=0.5", ["f", "=", 0.5]),
        ("n=10", ["n", "=", 10]),
    ],
)
def test_parse_filter_preserves_leading_zeros(token, expected):
    assert helpers.parse_filter(token) == expected


def test_parse_set_bad():
    with pytest.raises(UsageError):
        helpers.parse_set(["nope"])


def test_parse_fields():
    assert helpers.parse_fields("a,b, c") == ["a", "b", "c"]
    assert helpers.parse_fields("*") == ["*"]
    assert helpers.parse_fields(None) is None


def test_default_fields():
    meta = {
        "title_field": "subject",
        "fields": [
            {"fieldname": "subject", "fieldtype": "Data", "in_list_view": 1},
            {"fieldname": "status", "fieldtype": "Select", "in_list_view": 1},
            {"fieldname": "sb", "fieldtype": "Section Break", "in_list_view": 1},
            {"fieldname": "secret", "fieldtype": "Data", "in_list_view": 0},
        ],
    }
    assert helpers.default_fields(meta) == ["name", "subject", "status"]


def test_filters_json():
    assert helpers.parse_filters_json('[["a","in",[1,2]]]') == [["a", "in", [1, 2]]]
    with pytest.raises(UsageError):
        helpers.parse_filters_json("{bad}")
