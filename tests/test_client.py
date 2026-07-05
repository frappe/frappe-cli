import httpx
import pytest
import respx

from frappe_cli.client import FrappeClient
from frappe_cli.errors import FrappeError

BASE = "http://localhost"


def client():
    return FrappeClient(BASE, "k:s")


@respx.mock
def test_list_documents():
    respx.get(f"{BASE}/api/v2/document/ToDo").mock(
        return_value=httpx.Response(
            200, json={"data": [{"name": "a"}, {"name": "b"}], "has_next_page": True}
        )
    )
    rows, has_next = client().list_documents("ToDo", fields=["name"], limit=2)
    assert rows == [{"name": "a"}, {"name": "b"}]
    assert has_next is True


@respx.mock
def test_get_document_unwraps_data():
    respx.get(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(200, json={"data": {"name": "X", "status": "Open"}})
    )
    assert client().get_document("ToDo", "X") == {"name": "X", "status": "Open"}


@respx.mock
def test_create_document():
    route = respx.post(f"{BASE}/api/v2/document/ToDo").mock(
        return_value=httpx.Response(200, json={"data": {"name": "new1"}})
    )
    out = client().create_document("ToDo", {"description": "hi"})
    assert out["name"] == "new1"
    assert route.calls.last.request.content  # body sent


@respx.mock
def test_error_extracted_and_raised():
    respx.get(f"{BASE}/api/v2/document/ToDo/Z/").mock(
        return_value=httpx.Response(
            404, json={"errors": [{"message": "ToDo Z not found"}]}
        )
    )
    with pytest.raises(FrappeError) as ei:
        client().get_document("ToDo", "Z")
    assert ei.value.message == "ToDo Z not found"
    assert ei.value.status_code == 404


@respx.mock
def test_401_message():
    respx.get(f"{BASE}/api/v2/document/ToDo/Z/").mock(
        return_value=httpx.Response(401, json={"errors": [{"message": "nope"}]})
    )
    with pytest.raises(FrappeError) as ei:
        client().get_document("ToDo", "Z")
    assert ei.value.status_code == 401
    assert "Authentication failed" in ei.value.message


@respx.mock
def test_call_method_get():
    respx.get(f"{BASE}/api/v2/method/frappe.client.get_count").mock(
        return_value=httpx.Response(200, json={"data": 42})
    )
    assert (
        client().call_method(
            "frappe.client.get_count", params={"doctype": "User"}, http_method="GET"
        )
        == 42
    )


@respx.mock
def test_redirect_is_an_error_not_success():
    # An auth failure that 302s to a login page must surface as an error,
    # not a spurious success returning the login HTML.
    respx.get(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(302, headers={"location": "/login"})
    )
    with pytest.raises(FrappeError) as ei:
        client().get_document("ToDo", "X")
    assert ei.value.status_code == 302
    assert "redirect" in ei.value.message.lower()


@respx.mock
def test_stream_download_follows_redirects_and_chunks():
    # File URLs may redirect to object storage; stream_download must follow,
    # write via the callback, and report the total byte count.
    respx.get(f"{BASE}/private/files/x.bin").mock(
        return_value=httpx.Response(302, headers={"location": f"{BASE}/cdn/x.bin"})
    )
    respx.get(f"{BASE}/cdn/x.bin").mock(
        return_value=httpx.Response(200, content=b"payload")
    )
    chunks: list[bytes] = []
    total = client().stream_download("/private/files/x.bin", chunks.append)
    assert b"".join(chunks) == b"payload"
    assert total == len(b"payload")


@respx.mock
def test_stream_download_raises_on_error():
    respx.get(f"{BASE}/private/files/missing.bin").mock(
        return_value=httpx.Response(404, json={"errors": [{"message": "gone"}]})
    )
    with pytest.raises(FrappeError) as ei:
        client().stream_download("/private/files/missing.bin", lambda _b: None)
    assert ei.value.status_code == 404


@respx.mock
def test_count():
    respx.get(f"{BASE}/api/v2/doctype/ToDo/count").mock(
        return_value=httpx.Response(200, json={"data": 7})
    )
    assert client().get_count("ToDo") == 7


@respx.mock
def test_redirect_is_refused_and_secret_never_forwarded():
    # A redirect must hard-fail; the redirect target must never be requested,
    # so the credential can never reach it.
    evil = "https://evil.test"
    respx.get(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(302, headers={"Location": f"{evil}/steal"})
    )
    landing = respx.get(f"{evil}/steal").mock(
        return_value=httpx.Response(200, json={"data": {"name": "X"}})
    )
    with pytest.raises(FrappeError) as ei:
        client().get_document("ToDo", "X")
    assert "redirect" in ei.value.message.lower()
    assert not landing.called  # never even contacted the redirect target


def test_refuses_plain_http_for_remote_host():
    with pytest.raises(FrappeError) as ei:
        FrappeClient("http://erp.example.com", "k:s")
    assert "cleartext" in ei.value.message.lower()


def test_allows_plain_http_on_localhost():
    # Local development over http is fine — the secret never leaves the box.
    for site in ("http://localhost:8000", "http://127.0.0.1", "http://dev.localhost"):
        FrappeClient(site, "k:s").close()
