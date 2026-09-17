#!/usr/bin/env python3
"""Command-line controller for legacy and progressive OmniBrowser actions."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from browser_core.contracts import (
        ActionTimeoutError,
        ExitCode,
        OmniBrowserError,
        TargetNotFoundError,
    )
    from browser_core.engine import act
    from browser_core.page_manager import PageManager
    from browser_core.primitives import inspect, inspectVisual, runBrowserCode
except ModuleNotFoundError:  # Allow invoking this file directly from a checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from browser_core.contracts import (  # noqa: E402
        ActionTimeoutError,
        ExitCode,
        OmniBrowserError,
        TargetNotFoundError,
    )
    from browser_core.engine import act  # noqa: E402
    from browser_core.page_manager import PageManager  # noqa: E402
    from browser_core.primitives import inspect, inspectVisual, runBrowserCode  # noqa: E402


DEFAULT_CDP_URL = os.environ.get("CDP_URL", "http://127.0.0.1:17082")


def _legacy_context_and_page(browser, url_substring=None):
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    if url_substring:
        for page in context.pages:
            if url_substring in page.url:
                page.bring_to_front()
                return context, page
    page = context.pages[0] if context.pages else context.new_page()
    page.bring_to_front()
    return context, page


def cmd_list_tabs(args):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(args.cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        print(f"\nConnected to Chrome CDP ({args.cdp_url})")
        print(f"Total open tabs: {len(context.pages)}\n")
        print(f"{'INDEX':<6} | {'TITLE':<40} | {'URL'}")
        print("-" * 90)
        for i, page in enumerate(context.pages):
            print(f"{i:<6} | {page.title()[:38]:<40} | {page.url[:50]}")


def _legacy_page(args):
    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    browser = playwright.chromium.connect_over_cdp(args.cdp_url)
    context, page = _legacy_context_and_page(browser, args.match)
    return playwright, browser, context, page


def cmd_goto(args):
    playwright, browser, _context, page = _legacy_page(args)
    try:
        print(f"Navigating to {args.url}...")
        page.goto(args.url)
        page.wait_for_load_state("domcontentloaded")
        print(f"Page title: {page.title()}")
        print(f"Current URL: {page.url}")
    finally:
        browser.close()
        playwright.stop()


def cmd_eval(args):
    playwright, browser, _context, page = _legacy_page(args)
    try:
        print("Result:", page.evaluate(args.expression))
    finally:
        browser.close()
        playwright.stop()


def cmd_screenshot(args):
    playwright, browser, _context, page = _legacy_page(args)
    try:
        page.screenshot(path=args.output)
        print(f"Screenshot saved to: {args.output}")
    finally:
        browser.close()
        playwright.stop()


def _manager_page(args):
    manager = PageManager(args.cdp_url)
    manager.connect()
    page = manager.primary_page()
    if getattr(args, "match", None) and manager.context:
        for p in manager.context.pages:
            if args.match in p.url or args.match in p.title():
                page = p
                break
    manager.install_scanner(page)
    return manager, page


def _frame(manager, page, token):
    if not token:
        return None
    return manager._frame_for_token(page, token.removeprefix("f"))


def _json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))


def cmd_observe(args):
    manager, page = _manager_page(args)
    try:
        result = manager.observe(page, max_elements=args.max_elements, frame=_frame(manager, page, args.frame))
        _json(result.to_dict())
    finally:
        manager.close()


def _read_json(value: Any, filename: str | None, default: Any = None) -> Any:
    if filename:
        return json.loads(Path(filename).read_text(encoding="utf-8"))
    if value is None or value is True:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def cmd_act(args):
    payload = _read_json(args.json, args.file, {})
    operation = args.op or payload.get("op") or payload.get("action")
    ref = args.ref or payload.get("ref") or (payload.get("target") or {}).get("ref")
    value = args.value if args.value is not None else payload.get("value")
    expect = _read_json(args.expect, None, payload.get("expect"))
    if not operation or not ref:
        raise ValueError("act requires --op and --ref (or a JSON request)")
    manager, page = _manager_page(args)
    try:
        _json(act(page, operation, ref, value, expect, manager=manager).to_dict())
    finally:
        manager.close()


def cmd_inspect(args):
    manager, page = _manager_page(args)
    try:
        result = inspect(manager, page, mode=args.mode, ref=args.ref, depth=args.depth)
        if args.format == "json":
            _json(result)
        else:
            print(result)
    finally:
        manager.close()


def cmd_run_code(args):
    script = args.script
    if args.script_file:
        script = Path(args.script_file).read_text(encoding="utf-8")
    if not script:
        raise ValueError("run-code requires --script or --script-file")
    manager, page = _manager_page(args)
    try:
        _json(runBrowserCode(manager, page, script=script, mode=args.mode, timeout_ms=args.timeout_ms))
    finally:
        manager.close()


def cmd_visual(args):
    if not args.ref:
        raise ValueError("visual requires --ref/--scope-ref")
    manager, page = _manager_page(args)
    try:
        _json(inspectVisual(manager, page, ref=args.ref, output_path=args.output, padding=args.padding))
    finally:
        manager.close()


def _parser():
    parser = argparse.ArgumentParser(description="CDP Browser Automation Controller")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL, help=f"CDP URL (default: {DEFAULT_CDP_URL})")
    parser.add_argument("--match", default=None, help="Target tab by URL substring")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-tabs", help="List all open browser tabs")
    p = sub.add_parser("goto", help="Navigate tab to a specific URL"); p.add_argument("url")
    p = sub.add_parser("eval", help="Evaluate JavaScript expression on active page"); p.add_argument("expression")
    p = sub.add_parser("screenshot", help="Capture a screenshot"); p.add_argument("--output", default="screenshot.png")
    p = sub.add_parser("observe", help="Return a compact semantic DOM projection")
    p.add_argument("--scope", default="main", choices=["main", "viewport"])
    p.add_argument("--max-elements", type=int, default=80)
    p.add_argument("--frame", default=None)
    p.add_argument("--json", action="store_true", default=False, help="Output compact JSON")
    p = sub.add_parser("act", help="Execute an action against an opaque DOM ref")
    p.add_argument("--op", "--action", dest="op"); p.add_argument("--ref"); p.add_argument("--value")
    p.add_argument("--expect"); p.add_argument("--json", nargs="?", const=True, default=None); p.add_argument("--file")
    p = sub.add_parser("inspect", help="Inspect targeted DOM, accessibility, or frame tree")
    p.add_argument("mode", choices=["dom", "ax", "frame-tree"]); p.add_argument("--ref"); p.add_argument("--depth", type=int, default=3); p.add_argument("--format", choices=["text", "json"], default="text")
    p = sub.add_parser("run-code", aliases=["runBrowserCode"], help="Run bounded JavaScript in the page")
    p.add_argument("--script"); p.add_argument("--script-file"); p.add_argument("--mode", choices=["read", "write"], default="read"); p.add_argument("--timeout-ms", type=int)
    p = sub.add_parser("visual", aliases=["inspectVisual"], help="Capture a targeted visual crop")
    p.add_argument("--ref", "--scope-ref", dest="ref"); p.add_argument("--output"); p.add_argument("--padding", type=int, default=20)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        command = {"runBrowserCode": "run-code", "inspectVisual": "visual"}.get(args.command, args.command)
        return globals()[f"cmd_{command.replace('-', '_')}"](args)
    except (OmniBrowserError, TargetNotFoundError, ActionTimeoutError) as error:
        print(str(error), file=sys.stderr)
        return int(error.exit_code)
    except (ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return int(ExitCode.INVALID_INPUT)
    except Exception as error:
        print(str(error), file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)


if __name__ == "__main__":
    raise SystemExit(main())
