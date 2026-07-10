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
def test_redirect_is_followed():
    # A redirect (e.g. Frappe's trailing-slash normalization) is followed
    # transparently rather than surfaced as an error.
    respx.get(f"{BASE}/api/v2/document/ToDo/X").mock(
        return_value=httpx.Response(
            301, headers={"location": f"{BASE}/api/v2/document/ToDo/X/"}
        )
    )
    respx.get(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(200, json={"data": {"name": "X"}})
    )
    # Request the unslashed path; the client should follow the 301 to it.
    assert client().request("GET", "/api/v2/document/ToDo/X") == {"name": "X"}


@respx.mock
def test_post_redirect_replays_body_on_308():
    # 308 must preserve method and body when following the redirect.
    respx.post(f"{BASE}/api/v2/document/ToDo").mock(
        return_value=httpx.Response(
            308, headers={"location": f"{BASE}/api/v2/document/ToDo/"}
        )
    )
    landing = respx.post(f"{BASE}/api/v2/document/ToDo/").mock(
        return_value=httpx.Response(200, json={"data": {"name": "new1"}})
    )
    out = client().create_document("ToDo", {"description": "hi"})
    assert out["name"] == "new1"
    assert landing.calls.last.request.content  # body replayed to the new URL


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
def test_cross_host_redirect_does_not_forward_credential():
    # A redirect to a different host is followed, but httpx strips the
    # Authorization header on cross-origin redirects, so the API secret is
    # never handed to a host the user did not configure.
    evil = "https://evil.test"
    respx.get(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(302, headers={"Location": f"{evil}/steal"})
    )
    landing = respx.get(f"{evil}/steal").mock(
        return_value=httpx.Response(200, json={"data": {"name": "X"}})
    )
    client().get_document("ToDo", "X")
    assert "authorization" not in landing.calls.last.request.headers


@respx.mock
def test_debug_requests_sql_and_emits_server_debug(capsys):
    # With debug on, list requests ask the server for SQL (debug=true) and the
    # returned debug messages are printed to stderr.
    route = respx.get(f"{BASE}/api/v2/document/ToDo").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"name": "a"}],
                "has_next_page": False,
                "debug": [
                    {"message": "SELECT `name` FROM `tabToDo` LIMIT 1"},
                    {"message": "Execution time: 0.2 ms"},
                ],
            },
        )
    )
    FrappeClient(BASE, "k:s", debug=True).list_documents("ToDo", fields=["name"])
    assert route.calls.last.request.url.params["debug"] == "true"
    err = capsys.readouterr().err
    assert "→ GET" in err  # request traced
    assert "authorization: token ***" in err  # credential redacted
    assert "[server] SELECT `name` FROM `tabToDo` LIMIT 1" in err  # SQL surfaced


@respx.mock
def test_no_debug_param_or_output_by_default(capsys):
    route = respx.get(f"{BASE}/api/v2/document/ToDo").mock(
        return_value=httpx.Response(200, json={"data": [], "has_next_page": False})
    )
    client().list_documents("ToDo", fields=["name"])
    assert "debug" not in route.calls.last.request.url.params
    assert capsys.readouterr().err == ""


def test_refuses_plain_http_for_remote_host():
    with pytest.raises(FrappeError) as ei:
        FrappeClient("http://erp.example.com", "k:s")
    assert "cleartext" in ei.value.message.lower()


def test_allows_plain_http_on_localhost():
    # Local development over http is fine — the secret never leaves the box.
    for site in ("http://localhost:8000", "http://127.0.0.1", "http://dev.localhost"):
        FrappeClient(site, "k:s").close()


# --- read-only profile guard ----------------------------------------------


def ro_client():
    return FrappeClient(BASE, "k:s", read_only=True)


@respx.mock
def test_read_only_allows_get():
    respx.get(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(200, json={"data": {"name": "X"}})
    )
    assert ro_client().get_document("ToDo", "X") == {"name": "X"}


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.create_document("ToDo", {"description": "hi"}),
        lambda c: c.update_document("ToDo", "X", {"description": "hi"}),
        lambda c: c.delete_document("ToDo", "X"),
        lambda c: c.run_doc_method("ToDo", "X", "some_method"),
        lambda c: c.call_method("frappe.client.set_value", params={"a": 1}),
    ],
)
@respx.mock
def test_read_only_refuses_writes(call):
    # A catch-all route ensures the guard, not the network, is what stops us:
    # if any request escaped it would 500 here rather than raise our message.
    respx.route().mock(return_value=httpx.Response(500))
    with pytest.raises(FrappeError) as ei:
        call(ro_client())
    assert "read-only" in ei.value.message.lower()


@respx.mock
def test_read_only_get_method_call_allowed():
    route = respx.get(f"{BASE}/api/v2/method/frappe.client.get_count").mock(
        return_value=httpx.Response(200, json={"data": 3})
    )
    assert ro_client().call_method("frappe.client.get_count", http_method="GET") == 3
    assert route.called
