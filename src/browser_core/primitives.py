"""Bounded diagnostic and browser-context escape hatches."""

from __future__ import annotations

import json
import fnmatch
from pathlib import Path
import re
import time
from typing import Any, Callable, Iterable

from PIL import Image
from io import BytesIO
from playwright.sync_api import Page

from .contracts import ActionTimeoutError, DOMNodeRef, StaleRefError
from .page_manager import PageManager, manager_for_page


MAX_OUTPUT_BYTES = 32 * 1024


def _args(first: PageManager | Page, second: Page | None) -> tuple[PageManager, Page]:
    """Accept both manager-first and page-first forms for compatibility."""
    if isinstance(first, Page):
        page = first
        manager = manager_for_page(page)
    else:
        manager, page = first, second
    if manager is None or page is None:
        raise RuntimeError("A PageManager and Page are required")
    return manager, page


def _parse_ref(value: DOMNodeRef | str | None) -> DOMNodeRef | None:
    if value is None or isinstance(value, DOMNodeRef):
        return value
    return DOMNodeRef.parse(value)


def _target(manager: PageManager, page: Page, ref: DOMNodeRef | str | None):
    dom_ref = _parse_ref(ref)
    if dom_ref is None:
        return page.main_frame, None
    try:
        frame = manager._frame_for_token(page, dom_ref.frame)
        if str(manager._frame_state(frame).epoch) != dom_ref.epoch:
            raise StaleRefError(f"Stale ref {dom_ref}; call observe() to refresh refs")
        handle = manager.resolve_element(page, dom_ref)
    except StaleRefError:
        raise
    except Exception as error:
        raise StaleRefError(f"Stale ref {dom_ref}; call observe() to refresh refs") from error
    if handle is None:
        raise StaleRefError(f"Stale ref {dom_ref}; call observe() to refresh refs")
    return frame, handle


def _bounded(value: Any, *, limit: int = MAX_OUTPUT_BYTES) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(text.encode("utf-8")) <= limit:
        return text
    raw = text.encode("utf-8")[:limit]
    return raw.decode("utf-8", errors="ignore")


def _network_url_matches(url: str, pattern: str | re.Pattern[str] | None) -> bool:
    """Match either a regular expression or a shell-style URL glob."""
    if pattern is None:
        return True
    if hasattr(pattern, "search"):
        return bool(pattern.search(url))
    value = str(pattern)
    try:
        if re.search(value, url):
            return True
    except re.error:
        # A malformed regex can still be a useful literal/glob filter.
        pass
    return fnmatch.fnmatch(url, value)


def _network_body_value(body: str, *, max_body_bytes: int) -> tuple[str | None, bool, Any]:
    """Return a bounded body, truncation state, and parsed JSON when possible."""
    encoded = body.encode("utf-8")
    if len(encoded) > max_body_bytes:
        bounded = encoded[:max_body_bytes].decode("utf-8", errors="ignore")
        return bounded, True, None
    try:
        parsed = json.loads(body)
    except (TypeError, ValueError):
        parsed = None
    return body, False, parsed


def capture_network_traffic(
    first: PageManager | Page,
    second: Page | None = None,
    *,
    action: Callable[[], Any] | None = None,
    page_action: Callable[[], Any] | None = None,
    url_pattern: str | re.Pattern[str] | None = None,
    resource_types: Iterable[str] | None = None,
    max_entries: int = 100,
    max_body_bytes: int = 64 * 1024,
    timeout_ms: int = 1000,
) -> dict[str, Any]:
    """Capture bounded JSON/API responses while a page action runs.

    The primitive uses a short-lived CDP ``Network`` session. Response events
    are collected first and bodies are fetched after the action, which keeps
    the event callback lightweight and avoids re-entrant CDP calls.
    """
    if second is not None and callable(second) and action is None and page_action is None:
        action = second  # type: ignore[assignment]
        second = None
    _manager, page = _args(first, second)
    if action is not None and page_action is not None:
        raise ValueError("capture_network_traffic accepts action or page_action, not both")
    callback = action or page_action
    max_entries = min(max(int(max_entries), 1), 1000)
    max_body_bytes = min(max(int(max_body_bytes), 0), 1024 * 1024)
    timeout_ms = min(max(int(timeout_ms), 0), 10000)
    wanted_types = {str(value).lower() for value in (resource_types or {"xhr", "fetch"})}
    session = page.context.new_cdp_session(page)
    responses: list[dict[str, Any]] = []
    requests: dict[str, dict[str, Any]] = {}

    def on_request(event: dict[str, Any]) -> None:
        request = event.get("request") or {}
        request_id = str(event.get("requestId", ""))
        if request_id:
            requests[request_id] = {
                "method": request.get("method", "GET"),
                "url": request.get("url", ""),
                "resource_type": event.get("type", ""),
            }

    def on_response(event: dict[str, Any]) -> None:
        response = event.get("response") or {}
        request_id = str(event.get("requestId", ""))
        resource_type = str(event.get("type", "")).lower()
        url = str(response.get("url", ""))
        if resource_type not in wanted_types or not _network_url_matches(url, url_pattern):
            return
        if len(responses) >= max_entries:
            return
        headers = response.get("headers") or {}
        content_type = str(headers.get("content-type", headers.get("Content-Type", "")))
        responses.append(
            {
                "request_id": request_id,
                "url": url,
                "method": requests.get(request_id, {}).get("method", "GET"),
                "status": response.get("status"),
                "status_text": response.get("statusText", ""),
                "mime_type": response.get("mimeType", ""),
                "content_type": content_type,
                "resource_type": resource_type,
                "headers": headers,
                "_body_pending": True,
            }
        )

    action_result: Any = None
    action_error: BaseException | None = None
    try:
        session.on("Network.requestWillBeSent", on_request)
        session.on("Network.responseReceived", on_response)
        session.send("Network.enable")
        if callback is not None:
            try:
                action_result = callback()
            except BaseException as error:  # Preserve the action error in the result.
                action_error = error
        if timeout_ms:
            page.wait_for_timeout(timeout_ms)
        for item in responses:
            item.pop("_body_pending", None)
            try:
                body_result = session.send("Network.getResponseBody", {"requestId": item["request_id"]})
                body = str(body_result.get("body", ""))
                bounded, truncated, parsed = _network_body_value(body, max_body_bytes=max_body_bytes)
                item["body"] = bounded
                item["body_truncated"] = truncated
                if parsed is not None:
                    item["json"] = parsed
                    item["body_json"] = parsed
            except Exception as error:
                item["body"] = None
                item["body_truncated"] = False
                item["body_error"] = str(error)
    finally:
        try:
            session.send("Network.disable")
        except Exception:
            pass
        session.detach()

    result: dict[str, Any] = {
        "ok": action_error is None,
        "requests": responses,
        "responses": responses,
        "count": len(responses),
        "truncated": len(responses) >= max_entries,
    }
    if action_result is not None:
        result["action_result"] = action_result
    if action_error is not None:
        result["action_error"] = {"type": type(action_error).__name__, "message": str(action_error)}
    return result


def inspect(
    first: PageManager | Page,
    second: Page | None = None,
    *,
    mode: str = "dom",
    ref: DOMNodeRef | str | None = None,
    depth: int = 3,
) -> str:
    """Return bounded textual DOM, accessibility, or frame-tree diagnostics."""
    manager, page = _args(first, second)
    if mode not in {"dom", "ax", "frame-tree"}:
        raise ValueError("mode must be dom, ax, or frame-tree")
    depth = min(max(int(depth), 0), 8)
    if mode == "frame-tree":
        rows = []
        for frame in page.frames:
            state = manager._frame_state(frame)
            rows.append({
                "frame": f"f{state.token}",
                "token": f"f{state.token}",
                "url": frame.url,
                "name": frame.name,
                "attached": frame.is_detached() is False,
            })
        return _bounded(json.dumps(rows, ensure_ascii=False, indent=2))

    frame, handle = _target(manager, page, ref)
    if mode == "dom":
        script = """
        (element, depth) => {
          const root = element || document.documentElement;
          const trim = (value, max = 800) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, max);
          const walk = (node, level) => {
            if (!(node instanceof Element) || level > depth) return '';
            const attrs = [...node.attributes].map(a => `${a.name}=\"${trim(a.value, 120)}\"`).join(' ');
            const own = trim(node.childElementCount ? '' : node.textContent, 800);
            let out = `${'  '.repeat(level)}<${node.tagName.toLowerCase()}${attrs ? ' ' + attrs : ''}>${own}`;
            for (const child of node.children) out += '\\n' + walk(child, level + 1);
            return out;
          };
          return walk(root, 0);
        }
        """
        raw = handle.evaluate(script, depth) if handle else frame.evaluate(script, depth)
        return _bounded(raw)

    if mode == "ax":
        script = """
        (element, depth) => {
          const root = element || document.body || document.documentElement;
          const trim = (value, max = 800) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, max);
          const walk = (node, level) => {
            if (!(node instanceof Element) || level > depth) return '';
            const role = node.getAttribute('role') || node.tagName.toLowerCase();
            const name = node.getAttribute('aria-label') || node.getAttribute('name') || node.id || '';
            const value = node.value || '';
            let line = `${'  '.repeat(level)}- role=${role}`;
            if (name) line += ` name="${trim(name, 120)}"`;
            if (value) line += ` value="${trim(value, 120)}"`;
            for (const child of node.children) {
              const sub = walk(child, level + 1);
              if (sub) line += '\\n' + sub;
            }
            return line;
          };
          return walk(root, 0);
        }
        """
        raw = handle.evaluate(script, depth) if handle else frame.evaluate(script, depth)
        return _bounded(raw)

    raise ValueError(f"Unsupported inspect mode: {mode}")


def runBrowserCode(
    first: PageManager | Page,
    second: Page | None = None,
    *,
    script: str,
    mode: str = "read",
    timeout_ms: int | None = None,
) -> Any:
    """Run a bounded async IIFE in the page and return JSON-compatible data."""
    manager, page = _args(first, second)
    del manager
    if mode not in {"read", "write"}:
        raise ValueError("mode must be read or write")
    limit = 2000 if mode == "read" else 10000
    if timeout_ms is not None:
        limit = min(max(int(timeout_ms), 1), 10000)

    expression = f"""
    (async () => {{
      const AsyncFunction = Object.getPrototypeOf(async function(){{}}).constructor;
      const fn = new AsyncFunction({json.dumps(script)});
      return await Promise.race([
        fn(),
        new Promise((_, reject) => setTimeout(() => reject(new Error('browser code timeout')), {limit}))
      ]);
    }})()
    """
    started = time.monotonic()
    session = page.context.new_cdp_session(page)
    try:
        response = session.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
                "timeout": limit,
            },
        )
    except Exception as error:
        elapsed_ms = (time.monotonic() - started) * 1000
        error_text = str(error).lower()
        if (
            "timeout" in error_text
            or "timed out" in error_text
            or "exceeded" in error_text
            or ("internal error" in error_text and elapsed_ms >= limit * 0.75)
        ):
            raise ActionTimeoutError(f"runBrowserCode timeout ({limit}ms)") from error
        raise
    finally:
        session.detach()

    exception_details = response.get("exceptionDetails")
    if exception_details:
        exception = exception_details.get("exception") or {}
        error_text = str(exception.get("description") or exception_details.get("text") or "browser code failed")
        if "browser code timeout" in error_text.lower():
            raise ActionTimeoutError(f"runBrowserCode timeout ({limit}ms)")
        raise RuntimeError(error_text)

    remote_value = response.get("result", {})
    value = remote_value.get("value")
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise ValueError(f"runBrowserCode output exceeds 32 KB limit ({len(encoded)} bytes)")
    return value


def inspectVisual(
    first: PageManager | Page,
    second: Page | None = None,
    *,
    ref: DOMNodeRef | str | None = None,
    output_path: str | None = None,
    padding: int = 20,
) -> dict[str, Any]:
    """Capture a padded element crop and flag CAPTCHA evidence for a human."""
    manager, page = _args(first, second)
    frame, handle = _target(manager, page, ref)
    if handle is None:
        raise ValueError("inspectVisual requires ref")
    metadata = handle.evaluate("""element => ({
      text: (element.innerText || element.textContent || '').slice(0, 1000),
      label: element.getAttribute('aria-label') || '', id: element.id || ''
    })""")
    captcha = bool(re.search(r"captcha", json.dumps(metadata), re.IGNORECASE))
    if captcha:
        result = {"requires_human_verification": True, "reason": "CAPTCHA detected"}
        if output_path:
            _save_crop(page, handle, output_path, padding)
            result["output_path"] = str(output_path)
        return result
    if output_path is None:
        return {"requires_human_verification": False, "bounds": handle.bounding_box()}
    _save_crop(page, handle, output_path, padding)
    return {"requires_human_verification": False, "output_path": str(output_path), "bounds": handle.bounding_box()}


def _save_crop(page: Page, handle: Any, output_path: str, padding: int) -> None:
    handle.scroll_into_view_if_needed()
    box = handle.bounding_box()
    if not box:
        raise StaleRefError("Target has no bounding box; call observe() and retry")
    image = Image.open(BytesIO(page.screenshot(type="png")))
    left = max(0, int(box["x"]) - padding)
    top = max(0, int(box["y"]) - padding)
    right = min(image.width, int(box["x"] + box["width"]) + padding)
    bottom = min(image.height, int(box["y"] + box["height"]) + padding)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    image.crop((left, top, right, bottom)).save(output_path)
