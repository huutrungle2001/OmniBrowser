import pytest


def test_clean_content(cli, page, server):
    browser_page, tab = page
    cli("--goto", server.url + "/content.html", "--tab", tab)
    result = cli("--observe", "--tab", tab).data
    for value in ("A useful heading", "First paragraph", "First item", "Column A", "One"):
        assert value in result["content"]
    for value in ("REDUNDANT", "HIDDEN_CONTENT", "INERT_CONTENT", "SCRIPT_SECRET", "NOSCRIPT_SECRET", "TEMPLATE_SECRET"):
        assert value not in result["content"]
    assert "\n" in result["content"]
    assert result["links"][0]["href"] == server.url + "/absolute"
    assert result["truncated"] is False


def test_controls_shadow_dialog_secrets(cli, page, server):
    _, tab = page
    result = cli("--observe", "--tab", tab)
    data = result.data
    names = {c["name"]: c for c in data["controls"]}
    for name in ("Search label", "Password", "Notes", "Editor", "Submit", "Disabled", "ARIA disabled",
                 "Choice", "Offscreen", "Shadow field", "Shadow button", "Accept cookies"):
        assert name in names
    assert names["Disabled"]["disabled"] and names["ARIA disabled"]["disabled"]
    for name in ("Hidden button", "Inert button", "Invisible button", "Frame-only control", "Download PDF"):
        assert name not in names
    assert "PRIVATE_" not in result.stdout
    assert "Shadow content" in data["content"] and "Cookie consent" in data["content"]
    link = next(l for l in data["links"] if l["text"] == "Download PDF")
    assert link["href"] == server.url + "/protected/file.pdf" and link["download_hint"]
    assert data["coverage"]["unsupported_frames"] == 1
    again = cli("--observe", "--tab", tab).data
    assert [c["ref"] for c in again["controls"]] == [c["ref"] for c in data["controls"]]


@pytest.mark.parametrize("size,pretty", [(2048, False), (15360, False), (65536, False), (2048, True)])
def test_budget(cli, page, server, size, pretty):
    _, tab = page
    cli("--goto", server.url + "/dense", "--tab", tab)
    args = ("--pretty",) if pretty else ()
    result = cli("--observe", "--tab", tab, "--max-bytes", str(size), *args)
    assert len(result.stdout.encode("utf-8")) <= size
    assert result.data["truncated"]
    cov = result.data["coverage"]
    assert cov["content_truncated"]
    assert len(result.data["links"]) + cov["links_omitted"] == 200
    assert len(result.data["controls"]) + cov["controls_omitted"] == 200


def test_truncate_content_first():
    from omni.observe import budget
    result = {"tab_id": "a", "url": "http://localhost", "title": "T", "content": "測試" * 2000,
              "links": [{"ref": "d.n1", "text": "Link", "href": "http://localhost"}],
              "controls": [{"ref": "d.n2", "role": "button", "name": "Keep me", "disabled": False}],
              "truncated": False, "coverage": {"content_truncated": False, "links_omitted": 0,
              "controls_omitted": 0, "unsupported_frames": 0}}
    output = budget(result, 2048)
    assert output["content"] and output["links"] and output["controls"]
    assert output["coverage"]["links_omitted"] == output["coverage"]["controls_omitted"] == 0


def test_content_fallback_and_display_contents(cli, page):
    browser_page, tab = page
    browser_page.set_content('<nav>REDUNDANT NAV</nav><div><h1>Largest region</h1><p>' +
        'Readable words ' * 30 + '</p><div style="display:contents">Visible boxless content</div></div>')
    result = cli("--observe", "--tab", tab).data
    assert "Largest region" in result["content"] and "Visible boxless content" in result["content"]
    assert "REDUNDANT" not in result["content"]


def test_metadata_budget_is_explicit_error(cli, page):
    browser_page, tab = page
    browser_page.evaluate("document.title='x'.repeat(3000)")
    result = cli("--observe", "--tab", tab, "--max-bytes", "2048", expected=2)
    assert len(result.stdout.encode()) <= 2048 and result.data["error"]["code"] == "OUTPUT_BUDGET"


def test_scan_execution_timeout(cli, page):
    browser_page, tab = page
    browser_page.evaluate("""() => {
        window.originalRects = Element.prototype.getClientRects;
        Element.prototype.getClientRects = function () {
            const start = performance.now();
            while (performance.now() - start < 100) {}
            return window.originalRects.call(this);
        };
    }""")
    result = cli("--observe", "--tab", tab, "--timeout", "1000", expected=5)
    assert result.data["error"]["code"] == "TIMEOUT"
    assert result.elapsed < 4
    browser_page.evaluate("() => { Element.prototype.getClientRects = window.originalRects; }")
    assert cli("--observe", "--tab", tab).data["controls"]


def test_refs_without_secure_context_uuid(cli, page):
    browser_page, tab = page
    # randomUUID is secure-context-only; getRandomValues also works on HTTP.
    # Simulate that browser capability here without visiting a real website.
    browser_page.evaluate("""() => {
        Object.defineProperty(crypto, 'randomUUID', {value: undefined, configurable: true});
    }""")
    first = cli("--observe", "--tab", tab).data
    ref = next(c["ref"] for c in first["controls"] if c["name"] == "Search label")
    again = cli("--observe", "--tab", tab).data
    assert ref == next(c["ref"] for c in again["controls"] if c["name"] == "Search label")
    cli("--act", "--tab", tab, "--ref", ref, "--action", "fill", "--value", "fixture")
    assert browser_page.locator('#query').input_value() == 'fixture'
