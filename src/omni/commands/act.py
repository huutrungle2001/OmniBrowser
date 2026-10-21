import time

from ..browser.targets import resolve
from ..errors import OmniError, invalid, stale


def act(page, tab_id, ref, action, value=None, timeout=10000, key=None, label=None, x=None, y=None):
    from playwright.sync_api import Error, TimeoutError
    handle = None
    deadline = time.monotonic() + timeout / 1000
    def remaining():
        ms = int((deadline - time.monotonic()) * 1000)
        if ms <= 0:
            raise OmniError('TIMEOUT', 'Action timed out; inspect before retrying. It was not replayed.', 5)
        return ms
    try:
        if action == 'click_at':
            session = page.context.new_cdp_session(page)
            try:
                layout = session.send('Page.getLayoutMetrics')
                view = layout['cssVisualViewport']
                # Pinch/visual-viewport offsets require a different input mapping.
                if view.get('scale', 1) != 1 or view.get('offsetX', 0) or view.get('offsetY', 0):
                    raise OmniError('UNSUPPORTED_ACTION', 'Non-default visual viewport scaling/offset is unsupported.', 2)
                if not (0 <= x < view['clientWidth'] and 0 <= y < view['clientHeight']):
                    raise invalid('click_at point is outside the current viewport CSS bounds.')
            finally:
                session.detach()
            remaining()
            page.mouse.click(x, y)
            return {'ok': True, 'tab_id': tab_id, 'action': action, 'x': x, 'y': y,
                    'coordinate_unit': 'CSS pixels', 'coordinate_space': 'top-level viewport', 'url': page.url}
        handle = resolve(page, tab_id, ref, remaining)
        element = handle.as_element()
        if element is None:
            raise stale()
        if not element.is_visible() or not element.is_enabled():
            raise OmniError("INVALID_ACTION", "Target is hidden or disabled; inspect the page.", 4)
        if action == "click":
            element.click(timeout=remaining(), no_wait_after=True)
        elif action == "fill":
            allowed = element.evaluate("""el => el.isContentEditable || el.matches('textarea,input:not([type]),input[type=text],input[type=search],input[type=email],input[type=url],input[type=tel],input[type=password],input[type=number]')""")
            if not allowed:
                raise OmniError("INVALID_ACTION", "Target is not a text-like editable field.", 2)
            element.fill(value, timeout=remaining())
        elif action == "press":
            element.press(key, timeout=remaining())
        elif action == "select":
            if not element.evaluate("el => el instanceof HTMLSelectElement"):
                raise OmniError("UNSUPPORTED_ACTION", "Select requires a native HTML select element.", 2)
            if value is not None:
                element.select_option(value=value, timeout=remaining())
            else:
                element.select_option(label=label, timeout=remaining())
        else:
            raise OmniError("INVALID_ACTION", "Unsupported action.", 2)
        return {"ok": True, "tab_id": tab_id, "ref": ref, "action": action, "url": page.url}
    except TimeoutError:
        raise OmniError("TIMEOUT", "Action timed out; inspect before retrying. It was not replayed.", 5) from None
    except Error:
        # Never include Playwright messages: fill call logs can contain the secret value.
        try:
            valid = bool(handle and handle.evaluate('el => el.isConnected && el.ownerDocument === document'))
        except Error:
            valid = False
        if page.is_closed() or not page.context.browser.is_connected():
            raise OmniError('UNAVAILABLE_TARGET', 'Browser transport or tab unavailable.', 4) from None
        if not valid and action != 'click_at':
            raise stale() from None
        raise OmniError("INVALID_ACTION", "Action failed; inspect before retrying. It was not replayed.", 4) from None
    finally:
        if handle:
            try:
                handle.dispose()
            except Error:
                pass
