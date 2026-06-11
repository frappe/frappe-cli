from frappe_cli.errors import extract_message, strip_html


def test_strip_html():
    assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"
    assert strip_html("a &amp; b") == "a & b"


def test_v2_message_error():
    body = {"errors": [{"type": "DoesNotExistError", "message": "ToDo X not found"}]}
    assert extract_message(body, 404) == "ToDo X not found"


def test_v2_message_html_stripped():
    body = {"errors": [{"message": "<div>Value missing for <b>X</b></div>"}]}
    assert extract_message(body, 417) == "Value missing for X"


def test_v2_exception_fallback():
    exc = (
        "Traceback (most recent call last):\n"
        '  File "x.py", line 1\n'
        "frappe.exceptions.MandatoryError: [ToDo, T1]: description\n"
    )
    body = {"errors": [{"type": "MandatoryError", "exception": exc}]}
    assert extract_message(body, 417) == "[ToDo, T1]: description"


def test_server_messages():
    import json

    body = {
        "_server_messages": json.dumps(
            [json.dumps({"message": "<b>Not allowed</b>", "title": "Error"})]
        )
    }
    assert extract_message(body, 403) == "Not allowed"


def test_plain_fallback():
    assert extract_message({}, 500) == "HTTP 500"
    assert extract_message("boom", 500) == "boom"
