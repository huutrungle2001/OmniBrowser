"""Exact tab resolution helpers; operations live under commands/."""
from ..errors import OmniError


def target_id(page):
    session = page.context.new_cdp_session(page)
    try:
        return session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
    finally:
        session.detach()


def resolve(browser, tab_id):
    from playwright.sync_api import Error
    for context in browser.contexts:
        for page in context.pages:
            try:
                if target_id(page) == tab_id:
                    return page
            except Error:
                continue  # A closing tab must not select a different one.
    raise OmniError("TAB_NOT_FOUND", "The selected tab does not exist.", 3)
