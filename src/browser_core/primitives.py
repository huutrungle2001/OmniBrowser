"""Bounded diagnostic and browser-context escape hatches."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

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
    expression = """
    async ({source, timeout}) => {
      const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
      const fn = new AsyncFunction(source);
      return await Promise.race([
        fn(),
        new Promise((_, reject) => setTimeout(() => reject(new Error('browser code timeout')), timeout))
      ]);
    }
    """
    try:
        value = page.evaluate(expression, {"source": script, "timeout": limit})
    except Exception as error:
        if "timeout" in str(error).lower() or "exceeded" in str(error).lower():
            raise ActionTimeoutError(f"runBrowserCode timeout ({limit}ms)") from error
        raise
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
