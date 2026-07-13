from frappectl.errors import error_hint, extract_message, strip_html


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


def test_error_hint_known_shapes():
    def hint_for(msg: str) -> str:
        h = error_hint(msg)
        assert h is not None
        return h

    assert "frappectl doctype list" in hint_for("ToDo X not found")
    assert "frappectl doctype show" in hint_for("[ToDo]: description is mandatory")
    assert "frappectl auth whoami" in hint_for("Permission denied (403).")
    assert "frappectl auth login" in hint_for(
        "Authentication failed (401). Check the API key/secret."
    )
    assert "reachable" in hint_for("Could not reach http://x: timeout")


def test_error_hint_quiet_on_unknown():
    assert error_hint(None) is None
    assert error_hint("") is None
    assert error_hint("stdin is not valid JSON") is None
    assert error_hint("--name requires --doctype.") is None
