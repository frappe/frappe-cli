import pytest

from frappectl.site import InsecureCredentialTransport, SiteURL, endpoint_path


def test_site_url_normalizes_input():
    assert str(SiteURL.parse("  example.com/// ")) == "https://example.com"


@pytest.mark.parametrize("host", ["localhost", "dev.localhost", "127.0.0.1", "[::1]"])
def test_site_url_allows_local_plain_http(host):
    SiteURL.parse(f"http://{host}").require_secure_credentials()


def test_site_url_rejects_remote_plain_http():
    with pytest.raises(InsecureCredentialTransport):
        SiteURL.parse("http://example.com").require_secure_credentials()


def test_endpoint_path_quotes_every_dynamic_segment():
    assert (
        endpoint_path("api", "v2", "document", "Sales Invoice", "SINV/1")
        == "/api/v2/document/Sales%20Invoice/SINV%2F1"
    )
