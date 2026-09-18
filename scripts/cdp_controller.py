#!/usr/bin/env python3
"""Command-line controller for legacy and progressive OmniBrowser actions."""

from __future__ import annotations

import argparse
import base64
from dataclasses import asdict, is_dataclass
import json
import os
import socket
import struct
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import urlopen

try:
    from browser_core.contracts import (
        ActionTimeoutError,
        DOMNodeRef,
        ExitCode,
        OmniBrowserError,
        ObserveResult,
        ObservedNode,
        TargetNotFoundError,
    )
except ModuleNotFoundError:  # Allow invoking this file directly from a checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from browser_core.contracts import (  # noqa: E402
        ActionTimeoutError,
        DOMNodeRef,
        ExitCode,
        OmniBrowserError,
        ObserveResult,
        ObservedNode,
        TargetNotFoundError,
    )


DEFAULT_CDP_URL = os.environ.get("CDP_URL", "http://127.0.0.1:17082")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _is_loopback_host(hostname: str | None) -> bool:
    return bool(hostname and hostname.lower() in _LOOPBACK_HOSTS)


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
    from browser_core.page_manager import PageManager

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
    fast_result = _fast_observe(args)
    if fast_result is not None:
        _json(fast_result.to_dict())
        return
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
    from browser_core.engine import act

    payload = _read_json(args.json, args.file, {})
    operation = args.op or payload.get("op") or payload.get("action")
    ref = args.ref or payload.get("ref") or (payload.get("target") or {}).get("ref")
    value = args.value if args.value is not None else payload.get("value")
    expect = _read_json(args.expect, None, payload.get("expect"))
    if not operation or not ref:
        raise ValueError("act requires --op and --ref (or a JSON request)")
    manager, page = _manager_page(args)
    try:
        _json(act(page, operation, ref, value, expect, manager=manager, learn_run=args.learn_run,
                  memory_root=getattr(args, "memory_root", None)).to_dict())
    finally:
        manager.close()


def cmd_inspect(args):
    from browser_core.primitives import inspect

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
    from browser_core.primitives import runBrowserCode

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
    from browser_core.primitives import inspectVisual

    if not args.ref:
        raise ValueError("visual requires --ref/--scope-ref")
    manager, page = _manager_page(args)
    try:
        _json(inspectVisual(manager, page, ref=args.ref, output_path=args.output, padding=args.padding))
    finally:
        manager.close()


def _recipe_store(args):
    """Build the procedural store without connecting to a browser."""
    from browser_core.recipes import RecipeStore

    recipe_dir = Path(getattr(args, "recipes_dir", None) or Path(__file__).resolve().parents[1] / "recipes")
    # RecipeStore's public constructor is intentionally accepted in both the
    # keyword and positional forms used by early v2 recipe implementations.
    try:
        return RecipeStore(recipe_dir=recipe_dir)
    except TypeError:
        try:
            return RecipeStore(recipes_dir=recipe_dir)
        except TypeError:
            return RecipeStore(recipe_dir)


def _learning_memory(args):
    from browser_core.recipes import LearningMemory

    return LearningMemory(getattr(args, "memory_root", None))


def cmd_learn_begin(args):
    _json(_learning_memory(args).begin(args.goal, policy=args.policy, agent_id=args.agent_id))


def cmd_learn_complete(args):
    validation = _read_json(args.validation, None, {}) or {}
    if not isinstance(validation, dict):
        raise ValueError("learn complete --validation must be a JSON object")
    _json(_learning_memory(args).complete(args.run_id, success=args.success, validation=validation))


def _recipe_value(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): _recipe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_recipe_value(item) for item in value]
    if hasattr(value, "__dict__"):
        return {key: _recipe_value(item) for key, item in vars(value).items() if not key.startswith("_")}
    return value


def _store_recipes(store, url: str | None = None) -> list[Any]:
    """Use the store's public listing/matching API and normalize its result."""
    if url:
        for name in ("matching", "match", "list_matching", "list_recipes"):
            method = getattr(store, name, None)
            if method is None:
                continue
            for kwargs in ({"url": url}, {"active_url": url}, {}):
                try:
                    result = method(**kwargs)
                except TypeError:
                    continue
                if result is not None:
                    values = list(result) if not isinstance(result, dict) else list(result.values())
                    if name in {"list_recipes", "list_matching"} and not kwargs:
                        continue
                    return values
    for name in ("list_recipes", "list", "all"):
        method = getattr(store, name, None)
        if method is None:
            continue
        try:
            result = method()
        except TypeError:
            continue
        if result is not None:
            return list(result) if not isinstance(result, dict) else list(result.values())
    values = getattr(store, "recipes", getattr(store, "_recipes", []))
    if isinstance(values, dict):
        values = values.values()
    values = list(values)
    if url:
        matcher = getattr(store, "matches", None)
        if matcher:
            values = [item for item in values if matcher(item, url)]
    return values


def _store_get(store, recipe_id: str) -> Any:
    for name in ("get", "get_recipe", "find"):
        method = getattr(store, name, None)
        if method is None:
            continue
        try:
            result = method(recipe_id)
        except (KeyError, LookupError):
            result = None
        if result is not None:
            return result
    for recipe in _store_recipes(store):
        data = _recipe_value(recipe)
        if data.get("id") == recipe_id:
            return recipe
    raise ValueError(f"Recipe not found: {recipe_id}")


def cmd_recipe_list(args):
    store = _recipe_store(args)
    recipes = _store_recipes(store, args.url)
    _json([_recipe_value(recipe) for recipe in recipes])


def cmd_recipe_show(args):
    store = _recipe_store(args)
    _json(_recipe_value(_store_get(store, args.recipe_id)))


def _recipe_engine(manager, store=None):
    from browser_core.recipes import RecipeEngine

    for kwargs in ({"store": store}, {"manager": manager, "store": store}, {"page_manager": manager, "store": store}, {}):
        try:
            return RecipeEngine(**kwargs)
        except TypeError:
            continue
    return RecipeEngine(manager)


def _execute_recipe(engine, recipe, page, params):
    execute = getattr(engine, "execute")
    for kwargs in (
        {"recipe": recipe, "page": page, "params": params},
        {"recipe": recipe, "page": page, "parameters": params},
    ):
        try:
            return execute(**kwargs)
        except TypeError:
            continue
    for arguments in ((recipe, page, params), (recipe, page), (recipe, params)):
        try:
            return execute(*arguments)
        except TypeError:
            continue
    raise TypeError("RecipeEngine.execute has an unsupported public signature")


def cmd_recipe_run(args):
    params = _read_json(args.params, None, {}) or {}
    if not isinstance(params, dict):
        raise ValueError("recipe run --params must be a JSON object")
    store = _recipe_store(args)
    recipe = _store_get(store, args.recipe_id)
    manager, page = _manager_page(args)
    try:
        result = _execute_recipe(_recipe_engine(manager, store), recipe, page, params)
        _json(_recipe_value(result))
    finally:
        manager.close()


def cmd_recipe_candidates(args):
    _json(_learning_memory(args).candidates(args.url))


def cmd_recipe_promote(args):
    _json(_learning_memory(args).promote(args.candidate_id, approve=args.approve))


def cmd_recipe_suggest(args):
    _json(_learning_memory(args).suggest(args.url))


class _RawCDP:
    """Tiny synchronous CDP transport used to avoid a Node driver for observe."""

    def __init__(self, websocket_url: str):
        parsed = urlsplit(websocket_url)
        if parsed.scheme != "ws" or not _is_loopback_host(parsed.hostname):
            raise ValueError("Only local ws CDP endpoints are supported")
        try:
            port = parsed.port or 80
        except ValueError as error:
            raise ValueError("Invalid local ws CDP endpoint") from error
        self._socket = socket.create_connection((parsed.hostname, port), timeout=10)
        self._socket.settimeout(10)
        self._recv_buffer = bytearray()
        self._closed = False
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        host_header = parsed.hostname
        if ":" in host_header:
            host_header = f"[{host_header}]"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host_header}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self._socket.sendall(request)
        headers = self._read_until(b"\r\n\r\n")
        if not headers.startswith(b"HTTP/1.1 101"):
            self._socket.close()
            raise RuntimeError("CDP WebSocket handshake failed")
        self._next_id = 0

    def _read_until(self, marker: bytes) -> bytes:
        while True:
            marker_index = self._recv_buffer.find(marker)
            if marker_index >= 0:
                end = marker_index + len(marker)
                data = bytes(self._recv_buffer[:end])
                del self._recv_buffer[:end]
                return data
            chunk = self._socket.recv(4096)
            if not chunk:
                raise ConnectionError("CDP connection closed")
            self._recv_buffer.extend(chunk)

    def _recv_exact(self, size: int) -> bytes:
        while len(self._recv_buffer) < size:
            chunk = self._socket.recv(size - len(self._recv_buffer))
            if not chunk:
                raise ConnectionError("CDP connection closed")
            self._recv_buffer.extend(chunk)
        data = bytes(self._recv_buffer[:size])
        del self._recv_buffer[:size]
        return data

    def _send_frame(self, payload: bytes, opcode: int = 1) -> None:
        length = len(payload)
        if length < 126:
            header = bytes((0x80 | opcode, 0x80 | length))
        elif length < 65536:
            header = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", length)
        else:
            header = bytes((0x80 | opcode, 0x80 | 127)) + struct.pack("!Q", length)
        mask = os.urandom(4)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self._socket.sendall(header + mask + masked)

    def _read_frame(self) -> tuple[int, bytes]:
        first, second = self._recv_exact(2)
        self._last_frame_fin = bool(first & 0x80)
        if first & 0x70:
            raise RuntimeError("Unsupported WebSocket extension")
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
            if length & (1 << 63):
                raise RuntimeError("Invalid WebSocket frame length")
        opcode = first & 0x0F
        if opcode not in (0, 1, 2, 8, 9, 10):
            raise RuntimeError("Unsupported WebSocket opcode")
        if opcode >= 8 and (not self._last_frame_fin or length > 125):
            raise RuntimeError("Invalid WebSocket control frame")
        masked = second & 0x80
        mask = self._recv_exact(4) if masked else b""
        payload = bytearray(self._recv_exact(length))
        if mask:
            payload = bytearray(value ^ mask[index % 4] for index, value in enumerate(payload))
        return opcode, bytes(payload)

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        self._send_frame(json.dumps({"id": request_id, "method": method, "params": params or {}}).encode())
        fragmented_opcode = None
        fragmented_payload = bytearray()
        while True:
            opcode, payload = self._read_frame()
            frame_fin = self._last_frame_fin
            if opcode == 9:
                self._send_frame(payload, opcode=10)
                continue
            if opcode == 10:
                continue
            if opcode == 8:
                if not self._closed:
                    self._send_frame(payload, opcode=8)
                    self._closed = True
                raise ConnectionError("CDP connection closed")
            if opcode == 0:
                if fragmented_opcode is None:
                    raise RuntimeError("Unexpected WebSocket continuation frame")
                fragmented_payload.extend(payload)
                if not frame_fin:
                    continue
                opcode = fragmented_opcode
                payload = bytes(fragmented_payload)
                fragmented_opcode = None
                fragmented_payload.clear()
            elif opcode in (1, 2):
                if fragmented_opcode is not None:
                    raise RuntimeError("Unexpected WebSocket data frame")
                if not frame_fin:
                    fragmented_opcode = opcode
                    fragmented_payload = bytearray(payload)
                    continue
            else:
                continue
            if opcode != 1:
                continue
            response = json.loads(payload)
            if response.get("id") == request_id:
                if "error" in response:
                    raise RuntimeError(str(response["error"]))
                return response

    def close(self) -> None:
        if not self._closed:
            try:
                self._send_frame(b"", opcode=8)
            except OSError:
                pass
            finally:
                self._closed = True
        try:
            self._socket.close()
        except OSError:
            pass


def _fast_observe(args) -> ObserveResult | None:
    """Use direct CDP for the common main-frame observe path.

    The Playwright implementation remains the fallback for matched tabs and
    frame-scoped observations, preserving the full feature path.
    """
    if getattr(args, "match", None) or getattr(args, "frame", None):
        return None
    try:
        parsed = urlsplit(args.cdp_url)
        if parsed.scheme not in {"http", "https"} or not _is_loopback_host(parsed.hostname):
            return None
        endpoint = f"{parsed.scheme}://{parsed.netloc}/json/list"
        with urlopen(endpoint, timeout=10) as response:
            tabs = json.load(response)
        target = next(tab for tab in tabs if tab.get("type") == "page" and tab.get("webSocketDebuggerUrl"))
        agent_source = Path(__file__).resolve().parents[1].joinpath("scripts", "dom_agent.js").read_text()
        cdp = _RawCDP(target["webSocketDebuggerUrl"])
        try:
            cdp.request("Runtime.evaluate", {"expression": agent_source, "returnByValue": True})
            cdp.request(
                "Runtime.evaluate",
                {
                    "expression": "window[Symbol.for('__OMNI_DOM_AGENT__')].setContext('0', '1')",
                    "returnByValue": True,
                },
            )
            response = cdp.request(
                "Runtime.evaluate",
                {
                    "expression": f"window[Symbol.for('__OMNI_DOM_AGENT__')].scan({max(1, int(args.max_elements))})",
                    "returnByValue": True,
                    "userGesture": False,
                },
            )
        finally:
            cdp.close()
        raw = response["result"]["result"].get("value")
        nodes = [
            ObservedNode(
                ref=DOMNodeRef.parse(node["ref"]),
                role=node["role"],
                name=node["name"],
                bounds=tuple(node["bounds"]),
                visible=node["visible"],
                interactive=node["interactive"],
                disabled=node.get("disabled", False),
                value=node.get("value"),
            )
            for node in raw["nodes"]
        ]
        return ObserveResult(
            page_url=target.get("url", ""),
            frame_id="f0",
            document_epoch="d1",
            revision=raw["revision"],
            tree=nodes[: max(1, int(args.max_elements))],
        )
    except Exception:
        return None


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
    p.add_argument("--learn-run", help="Append this successful action to a learning run")
    p.add_argument("--memory-root", default=None, help="Local learned-memory root (defaults to ~/.omnibrowser/memory/v1)")
    p = sub.add_parser("inspect", help="Inspect targeted DOM, accessibility, or frame tree")
    p.add_argument("mode", choices=["dom", "ax", "frame-tree"]); p.add_argument("--ref"); p.add_argument("--depth", type=int, default=3); p.add_argument("--format", choices=["text", "json"], default="text")
    p = sub.add_parser("run-code", aliases=["runBrowserCode"], help="Run bounded JavaScript in the page")
    p.add_argument("--script"); p.add_argument("--script-file"); p.add_argument("--mode", choices=["read", "write"], default="read"); p.add_argument("--timeout-ms", type=int)
    p = sub.add_parser("visual", aliases=["inspectVisual"], help="Capture a targeted visual crop")
    p.add_argument("--ref", "--scope-ref", dest="ref"); p.add_argument("--output"); p.add_argument("--padding", type=int, default=20)
    p = sub.add_parser("learn", help="Record and distill a privacy-safe semantic learning run")
    learn_sub = p.add_subparsers(dest="learn_command", required=True)
    p_begin = learn_sub.add_parser("begin", help="Start an isolated learning run")
    p_begin.add_argument("--goal", required=True); p_begin.add_argument("--policy", default="suggest", choices=["suggest", "manual"])
    p_begin.add_argument("--agent-id", default=None); p_begin.add_argument("--memory-root", default=None)
    p_complete = learn_sub.add_parser("complete", help="Finalize a learning run and create a draft")
    p_complete.add_argument("run_id"); p_complete.add_argument("--success", action="store_true", required=True)
    p_complete.add_argument("--validation", default="{}"); p_complete.add_argument("--memory-root", default=None)
    p = sub.add_parser("recipe", help="Use a stored procedural browser recipe")
    recipe_sub = p.add_subparsers(dest="recipe_command", required=True)
    p_list = recipe_sub.add_parser("list", help="List available recipes")
    p_list.add_argument("--url", default=None, help="Only list recipes matching this URL")
    p_list.add_argument("--recipes-dir", default=None, help="Recipe directory (defaults to ./recipes)")
    p_show = recipe_sub.add_parser("show", help="Show recipe steps and health metadata")
    p_show.add_argument("recipe_id")
    p_show.add_argument("--recipes-dir", default=None, help="Recipe directory (defaults to ./recipes)")
    p_run = recipe_sub.add_parser("run", help="Execute a stored recipe against the active tab")
    p_run.add_argument("recipe_id")
    p_run.add_argument("--params", nargs="?", const="{}", default="{}", help="JSON object of recipe parameters")
    p_run.add_argument("--recipes-dir", default=None, help="Recipe directory (defaults to ./recipes)")
    p_candidates = recipe_sub.add_parser("candidates", help="List locally learned draft recipes")
    p_candidates.add_argument("--url", default=None); p_candidates.add_argument("--memory-root", default=None)
    p_promote = recipe_sub.add_parser("promote", help="Promote a local learned draft after approval")
    p_promote.add_argument("candidate_id"); p_promote.add_argument("--approve", action="store_true", default=False)
    p_promote.add_argument("--memory-root", default=None)
    p_suggest = recipe_sub.add_parser("suggest", help="Suggest learned recipes for a URL")
    p_suggest.add_argument("--url", default=None); p_suggest.add_argument("--memory-root", default=None)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        command = {"runBrowserCode": "run-code", "inspectVisual": "visual"}.get(args.command, args.command)
        if command == "learn":
            return globals()[f"cmd_learn_{args.learn_command}"](args)
        if command == "recipe":
            return globals()[f"cmd_recipe_{args.recipe_command}"](args)
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
