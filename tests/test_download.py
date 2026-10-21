from pathlib import Path
from urllib.parse import quote

import pytest


def authorize(browser_page, server):
    browser_page.context.add_cookies([{"name": "session", "value": "fixture-secret",
        "url": server.url + "/", "httpOnly": True, "sameSite": "Lax"}])


def test_authenticated_download(cli, page, server, tmp_path):
    browser_page, tab = page
    authorize(browser_page, server)
    before = len(browser_page.context.pages)
    output = tmp_path / "nested" / "file.pdf"
    result = cli("--download", "--tab", tab, "--url", server.url + "/protected/file.pdf",
                 "--output", str(output), "--mkdir")
    assert output.read_bytes().startswith(b"%PDF-")
    assert result.data["bytes"] == output.stat().st_size
    assert result.data["status"] == 200 and result.data["content_type"] == "application/pdf"
    assert result.data["final_url"] == server.url + "/protected/file.pdf"
    assert "fixture-secret" not in result.stdout
    assert before == len(browser_page.context.pages)
    assert not list(tmp_path.rglob(".omni-*"))


@pytest.mark.parametrize("route,code", [("/html.pdf", "AUTH_REQUIRED"), ("/disguised.pdf", "AUTH_REQUIRED"),
    ("/wrong.pdf", "DOWNLOAD"), ("/unauthorized", "AUTH_REQUIRED"), ("/loop", "DOWNLOAD")])
def test_rejections_preserve_output(cli, page, server, tmp_path, route, code):
    _, tab = page
    output = tmp_path / "existing.pdf"
    output.write_bytes(b"original")
    result = cli("--download", "--tab", tab, "--url", server.url + route,
                 "--output", str(output), "--overwrite", expected=6)
    assert result.data["error"]["code"] == code
    assert output.read_bytes() == b"original"
    assert not list(tmp_path.glob(".omni-*"))


def test_missing_auth_redirect(cli, page, server, tmp_path):
    browser_page, tab = page
    browser_page.context.clear_cookies()
    result = cli("--download", "--tab", tab, "--url", server.url + "/protected/file.pdf",
                 "--output", str(tmp_path / "missing.pdf"), expected=6)
    assert result.data["error"]["code"] == "AUTH_REQUIRED"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("route", ["/protected/file.pdf", "/stream.pdf"])
def test_streaming_size_cap(cli, page, server, tmp_path, route):
    browser_page, tab = page
    authorize(browser_page, server)
    result = cli("--download", "--tab", tab, "--url", server.url + route, "--output", str(tmp_path / "big.pdf"),
                 "--max-bytes", "1000", expected=6)
    assert result.data["error"]["code"] == "DOWNLOAD"
    assert not list(tmp_path.iterdir())


def test_paths_overwrite_timeout(cli, page, server, tmp_path):
    browser_page, tab = page
    authorize(browser_page, server)
    output = tmp_path / "exists.pdf"
    output.write_bytes(b"existing")
    cli("--download", "--tab", tab, "--url", server.url + "/protected/file.pdf", "--output", str(output), expected=6)
    cli("--download", "--tab", tab, "--url", server.url + "/protected/file.pdf", "--output", str(tmp_path / "absent" / "a.pdf"), expected=6)
    cli("--download", "--tab", tab, "--url", server.url + "/protected/file.pdf", "--output", str(output), "--overwrite")
    assert output.read_bytes().startswith(b"%PDF-")
    timed = cli("--download", "--tab", tab, "--url", server.url + "/slow", "--output", str(tmp_path / "slow.pdf"),
                "--timeout", "100", expected=5)
    assert timed.data["error"]["code"] == "TIMEOUT"
    assert not (tmp_path / "slow.pdf").exists()


def test_cookie_scoping_and_cross_origin_redirect(cli, page, server, tmp_path):
    browser_page, tab = page
    browser_page.context.clear_cookies()
    browser_page.context.add_cookies([
        {"name": "host", "value": "host-secret", "domain": "127.0.0.1", "path": "/", "secure": False},
        {"name": "path", "value": "path-secret", "domain": "127.0.0.1", "path": "/protected", "secure": False},
        {"name": "tls", "value": "tls-secret", "domain": "127.0.0.1", "path": "/", "secure": True}])
    server.requests.clear()
    cli("--download", "--tab", tab, "--url", server.url + "/cookie.pdf", "--output", str(tmp_path / "first.pdf"))
    request = next(r for r in server.requests if r[0] == "/cookie.pdf")
    assert "host=host-secret" in request[1]
    assert "path-secret" not in request[1] and "tls-secret" not in request[1]
    destination = server.url.replace("127.0.0.1", "localhost") + "/cookie.pdf"
    cli("--download", "--tab", tab, "--url", server.url + "/redirect?to=" + quote(destination, safe=""),
        "--output", str(tmp_path / "cross.pdf"))
    request = [r for r in server.requests if r[0] == "/cookie.pdf"][-1]
    assert not request[1] and request[2] is None


def test_host_only_cookie_not_broadened_to_subdomain():
    from omni.commands.download import cookie_jar
    from urllib.request import Request
    jar = cookie_jar([{"name": "s", "value": "secret", "domain": "example.test", "path": "/", "secure": False}])
    request = Request("https://sub.example.test/file")
    jar.add_cookie_header(request)
    assert not request.has_header("Cookie")


def test_slow_progress_is_bounded_and_cleans_partial(cli, page, server, tmp_path):
    result = cli("--download", "--tab", page[1], "--url", server.url + "/drip.pdf", "--output", str(tmp_path / "drip.pdf"),
                 "--timeout", "200", expected=5)
    assert result.data["error"]["code"] == "TIMEOUT"
    assert result.elapsed < 3
    assert not list(tmp_path.iterdir())


def test_pdf_magic_checked_even_with_extensionless_output(cli, page, server, tmp_path):
    result = cli("--download", "--tab", page[1], "--url", server.url + "/wrong.pdf", "--output", str(tmp_path / "output"), expected=6)
    assert result.data["error"]["code"] == "DOWNLOAD"
    assert not list(tmp_path.iterdir())


def test_html_requires_explicit_html_output(cli, page, server, tmp_path):
    result = cli("--download", "--tab", page[1], "--url", server.url + "/login", "--output", str(tmp_path / "no-extension"), expected=6)
    assert result.data["error"]["code"] == "AUTH_REQUIRED"
    result = cli("--download", "--tab", page[1], "--url", server.url + "/login", "--output", str(tmp_path / "page.html"))
    assert result.data["content_type"] == "text/html"


def test_prefixed_login_body_preserves_non_pdf_output(cli, page, server, tmp_path):
    output = tmp_path / "archive.zip"
    output.write_bytes(b"original")
    result = cli("--download", "--tab", page[1], "--url", server.url + "/prefixed-login",
                 "--output", str(output), "--overwrite", expected=6)
    assert result.data["error"]["code"] == "AUTH_REQUIRED"
    assert output.read_bytes() == b"original"
    assert not list(tmp_path.glob('.omni-*'))


def test_post_only_flow_is_explicitly_unsupported(cli, page, server, tmp_path):
    output = tmp_path / "unsupported.pdf"
    result = cli("--download", "--tab", page[1], "--url", server.url + "/post-only",
                 "--output", str(output), expected=2)
    assert result.data["error"]["code"] == "UNSUPPORTED"
    assert not list(tmp_path.iterdir())
