def refs(cli, tab):
    return {c["name"]: c["ref"] for c in cli("--observe", "--tab", tab).data["controls"]}


def test_separate_process_fill_click_and_mutation(cli, page):
    browser_page, tab = page
    controls = refs(cli, tab)
    browser_page.evaluate("document.body.appendChild(document.createElement('button')).textContent='Unrelated'")
    count = browser_page.evaluate("window.__omni_v1_document__.next")
    filled = cli("--act", "--tab", tab, "--ref", controls["Search label"], "--action", "fill", "--value", "SECRET_FILL")
    assert "SECRET_FILL" not in filled.stdout
    assert browser_page.locator("#query").input_value() == "SECRET_FILL"
    cli("--act", "--tab", tab, "--ref", controls["Submit"], "--action", "click")
    assert browser_page.locator("#result").inner_text() == "Submitted"
    assert browser_page.locator("#submit").get_attribute("data-clicks") == "1"
    assert browser_page.evaluate("window.__omni_v1_document__.next") == count  # no hidden observe
    cli("--act", "--tab", tab, "--ref", controls["Shadow field"], "--action", "fill", "--value", "shadow")
    assert browser_page.locator("#inside").input_value() == "shadow"
    cli("--act", "--tab", tab, "--ref", controls["Shadow button"], "--action", "click")
    cli("--act", "--tab", tab, "--ref", controls["Offscreen"], "--action", "click")
    assert browser_page.locator("#offscreen").inner_text() == "Offscreen clicked"


def test_stale_navigation_and_reobserve(cli, page, server):
    _, tab = page
    before = refs(cli, tab)
    cli("--goto", server.url + "/interactive.html?new", "--tab", tab)
    stale = cli("--act", "--tab", tab, "--ref", before["Submit"], "--action", "click", expected=4)
    assert stale.data["error"]["code"] == "STALE_REF"
    after = refs(cli, tab)
    assert before["Submit"] != after["Submit"]
    cli("--act", "--tab", tab, "--ref", after["Submit"], "--action", "click")


def test_detach_changed_identity_and_cross_tab(cli, page, chrome, server):
    browser_page, tab = page
    observed = cli("--observe", "--tab", tab).data
    button = next(c["ref"] for c in observed["controls"] if c["name"] == "Submit")
    link = next(l["ref"] for l in observed["links"] if l["text"] == "Other page")
    browser_page.locator("#submit").evaluate("el => el.replaceWith(el.cloneNode(true))")
    assert cli("--act", "--tab", tab, "--ref", button, "--action", "click", expected=4).data["error"]["code"] == "STALE_REF"
    browser_page.locator("#other").evaluate("el => el.href='/changed'")
    assert cli("--act", "--tab", tab, "--ref", link, "--action", "click", expected=4).data["error"]["code"] == "STALE_REF"
    second = cli("--goto", server.url + "/interactive.html", "--new-tab").data["tab_id"]
    try:
        assert cli("--act", "--tab", second, "--ref", link, "--action", "click", expected=4).data["error"]["code"] == "STALE_REF"
    finally:
        browser_page.evaluate("1")  # Receive the independently created target event.
        next(p for p in browser_page.context.pages if p != browser_page and p.url.endswith("interactive.html")).close()


def test_disabled_invalid_hidden_and_secret_errors(cli, page):
    browser_page, tab = page
    controls = refs(cli, tab)
    for name in ("Disabled", "ARIA disabled"):
        assert cli("--act", "--tab", tab, "--ref", controls[name], "--action", "click", expected=4).data["error"]["code"] == "INVALID_ACTION"
    invalid = cli("--act", "--tab", tab, "--ref", controls["Submit"], "--action", "fill", "--value", "PRIVATE_VALUE", expected=2)
    assert "PRIVATE_VALUE" not in invalid.stdout
    browser_page.locator("#query").evaluate("el => el.readOnly=true")
    timed = cli("--act", "--tab", tab, "--ref", controls["Search label"], "--action", "fill", "--value", "PRIVATE_VALUE", "--timeout", "100", expected=5)
    assert "PRIVATE_VALUE" not in timed.stdout
    browser_page.locator("#submit").evaluate("el => el.hidden=true")
    cli("--act", "--tab", tab, "--ref", controls["Submit"], "--action", "click", expected=4)


def test_link_click(cli, page):
    _, tab = page
    link = next(l["ref"] for l in cli("--observe", "--tab", tab).data["links"] if l["text"] == "Other page")
    cli("--act", "--tab", tab, "--ref", link, "--action", "click")
    assert cli("--observe", "--tab", tab).data["title"] == "Content fixture"


def test_contenteditable_fill(cli, page):
    browser_page, tab = page
    control = refs(cli, tab)["Editor"]
    cli("--act", "--tab", tab, "--ref", control, "--action", "fill", "--value", "NEW_PRIVATE_EDITOR")
    assert browser_page.locator('[contenteditable]').inner_text() == "NEW_PRIVATE_EDITOR"
    assert "NEW_PRIVATE_EDITOR" not in cli("--observe", "--tab", tab).stdout


def test_detach_reattach_invalidates_only_affected_ref(cli, page):
    browser_page, tab = page
    controls = refs(cli, tab)
    browser_page.locator('#query').evaluate('el => { el.remove(); document.querySelector("main").append(el); }')
    result = cli("--act", "--tab", tab, "--ref", controls["Search label"], "--action", "fill", "--value", "private", expected=4)
    assert result.data["error"]["code"] == "STALE_REF"
    cli("--act", "--tab", tab, "--ref", controls["Submit"], "--action", "click")
    refreshed = refs(cli, tab)
    assert refreshed["Search label"] != controls["Search label"]
    cli("--act", "--tab", tab, "--ref", refreshed["Search label"], "--action", "fill", "--value", "private")
