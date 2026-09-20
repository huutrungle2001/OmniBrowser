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


def _reset_color_scheme(page: Any) -> None:
    """Reset emulated media query so Chromium falls back to native prefers-color-scheme.

    Prevents Playwright connect_over_cdp() from overriding and strobing dark/light themes.
    """
    if not page:
        return
    try:
        page.emulate_media(color_scheme="null")
    except Exception:
        try:
            page.emulate_media(color_scheme=None)
        except Exception:
            pass


def _legacy_context_and_page(browser, url_substring=None):
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    if url_substring:
        # First priority: substring or exact match in page.url (fast and non-blocking)
        for page in context.pages:
            if url_substring in page.url:
                page.bring_to_front()
                _reset_color_scheme(page)
                return context, page
        # Second priority: match in page title with timeout guard
        for page in context.pages:
            try:
                t = page.evaluate("() => document.title")
                if t and url_substring.lower() in t.lower():
                    page.bring_to_front()
                    _reset_color_scheme(page)
                    return context, page
            except Exception:
                pass
    page = context.pages[0] if context.pages else context.new_page()
    page.bring_to_front()
    _reset_color_scheme(page)
    return context, page


def _resolve_lease(args):
    """If --lease / --session is provided, validates lease and returns (router, lease, cdp_url, context_id)."""
    lease_id = getattr(args, "lease", None)
    if not lease_id:
        return None, None, args.cdp_url, None
    from browser_core.broker.session_router import SessionRouter
    router = SessionRouter()
    fencing_token = getattr(args, "fencing_token", None)
    lease = router.lease_manager.validate_lease(lease_id, fencing_token)
    return router, lease, lease.cdp_url, lease.browser_context_id


def cmd_list_tabs(args):
    router, lease, cdp_url, context_id = _resolve_lease(args)
    if lease:
        print(f"\nConnected to Chrome CDP ({cdp_url})")
        print(f"Active Lease: {lease.lease_id} (Class {lease.execution_class}, Token #{lease.fencing_token})")
        targets = router.target_registry.get_targets_for_lease(lease.lease_id)
        print(f"Total open tabs: {len(targets)}\n")
        print(f"{'INDEX':<6} | {'TITLE':<40} | {'URL'}")
        print("-" * 90)
        for i, target in enumerate(targets):
            title = target.title or "about:blank"
            url = target.url or "about:blank"
            print(f"{i:<6} | {title[:38]:<40} | {url[:50]}")
        return

    try:
        import urllib.request
        req = urllib.request.Request(f"{cdp_url}/json/list", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            pages = [t for t in data if isinstance(t, dict) and t.get("type") == "page"]
            print(f"\nConnected to Chrome CDP ({cdp_url})")
            print(f"Total open tabs: {len(pages)}\n")
            print(f"{'INDEX':<6} | {'TITLE':<40} | {'URL'}")
            print("-" * 90)
            for i, page in enumerate(pages):
                title = page.get("title", "") or "about:blank"
                url = page.get("url", "") or "about:blank"
                print(f"{i:<6} | {title[:38]:<40} | {url[:50]}")
            return
    except Exception:
        pass

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()

        pages = context.pages
        print(f"\nConnected to Chrome CDP ({cdp_url})")
        print(f"Total open tabs: {len(pages)}\n")
        print(f"{'INDEX':<6} | {'TITLE':<40} | {'URL'}")
        print("-" * 90)
        for i, page in enumerate(pages):
            _reset_color_scheme(page)
            print(f"{i:<6} | {page.title()[:38]:<40} | {page.url[:50]}")


def _legacy_page(args):
    from playwright.sync_api import sync_playwright

    router, lease, cdp_url, context_id = _resolve_lease(args)
    playwright = sync_playwright().start()
    browser = playwright.chromium.connect_over_cdp(cdp_url)
    context = None
    if context_id:
        for ctx in browser.contexts:
            if getattr(ctx, "_browser_context_id", None) == context_id:
                context = ctx
                break
    if not context:
        context = browser.contexts[0] if browser.contexts else browser.new_context()

    if getattr(args, "match", None):
        # Priority 1: substring in URL
        for page in context.pages:
            if args.match in page.url:
                _reset_color_scheme(page)
                return playwright, browser, context, page
        # Priority 2: title match
        for page in context.pages:
            try:
                if page.title() and args.match.lower() in page.title().lower():
                    _reset_color_scheme(page)
                    return playwright, browser, context, page
            except Exception:
                pass

    page = context.pages[0] if context.pages else context.new_page()
    _reset_color_scheme(page)
    return playwright, browser, context, page


def cmd_goto(args):
    playwright, browser, _context, page = _legacy_page(args)
    try:
        print(f"Navigating to {args.url}...")
        page.goto(args.url, wait_until="domcontentloaded")
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


def _raw_cdp_screenshot(
    cdp_url: str,
    output_path: str,
    match: str | None = None,
    context_id: str | None = None,
) -> bool:
    """Capture a screenshot directly via raw CDP WebSocket without initializing Playwright.

    Bypasses Playwright startup, drops latency to ~30ms, consumes near-zero CPU,
    and avoids theme strobing entirely.
    """
    import base64
    import json
    import time
    from pathlib import Path
    import urllib.request

    try:
        req = urllib.request.Request(f"{cdp_url}/json/list")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return False

    pages = [p for p in data if p.get("type") == "page" and p.get("webSocketDebuggerUrl")]
    if not pages:
        return False

    if context_id:
        context_pages = [p for p in pages if p.get("browserContextId") == context_id]
        if context_pages:
            pages = context_pages

    target = None
    if match:
        for p in pages:
            if match in p.get("url", ""):
                target = p
                break
        if not target:
            for p in pages:
                if match.lower() in p.get("title", "").lower():
                    target = p
                    break
    else:
        target = pages[0]

    if not target or not target.get("webSocketDebuggerUrl"):
        return False

    ws_url = target["webSocketDebuggerUrl"]
    fmt = "png"
    low = output_path.lower()
    if low.endswith(".jpg") or low.endswith(".jpeg"):
        fmt = "jpeg"
    elif low.endswith(".webp"):
        fmt = "webp"

    cmd = {
        "id": 1,
        "method": "Page.captureScreenshot",
        "params": {"format": fmt},
    }

    try:
        from websockets.sync.client import connect

        with connect(ws_url, open_timeout=2.0, close_timeout=2.0) as ws:
            ws.send(json.dumps(cmd))
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                resp_raw = ws.recv(timeout=2.0)
                msg = json.loads(resp_raw)
                if msg.get("id") == 1:
                    if "error" in msg:
                        return False
                    data_b64 = msg.get("result", {}).get("data")
                    if not data_b64:
                        return False
                    img_bytes = base64.b64decode(data_b64)
                    out = Path(output_path)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(img_bytes)
                    return True
    except Exception:
        return False

    return False


def cmd_screenshot(args):
    router, lease, cdp_url, context_id = _resolve_lease(args)
    # Fast path: raw CDP WebSocket capture (bypasses Playwright, ~30ms, no theme strobing)
    try:
        if _raw_cdp_screenshot(cdp_url, args.output, match=getattr(args, "match", None), context_id=context_id):
            print(f"Screenshot saved to: {args.output}")
            return
    except Exception:
        pass

    # Fallback to Playwright
    playwright, browser, _context, page = _legacy_page(args)
    try:
        page.screenshot(path=args.output)
        print(f"Screenshot saved to: {args.output}")
    finally:
        browser.close()
        playwright.stop()


def cmd_upload(args):
    playwright, browser, _context, page = _legacy_page(args)
    try:
        selector = getattr(args, "selector", None) or "#upload-files"
        print(f"Uploading {len(args.files)} file(s) via selector '{selector}'...")
        page.set_input_files(selector, args.files)
        print("Upload command executed successfully.")
    finally:
        browser.close()
        playwright.stop()


def _manager_page(args):
    from browser_core.page_manager import PageManager

    router, lease, cdp_url, context_id = _resolve_lease(args)
    manager = PageManager(cdp_url, browser_context_id=context_id)
    manager.connect()
    page = manager.primary_page()
    if getattr(args, "match", None) and manager.context:
        for p in manager.context.pages:
            if args.match in p.url:
                page = p
                break
        else:
            for p in manager.context.pages:
                try:
                    if args.match in p.title():
                        page = p
                        break
                except Exception:
                    pass
    _reset_color_scheme(page)
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
        dict_res = fast_result.to_dict()
        try:
            from browser_core.recipes import RecipeStore, suggest_for_url
            store = _recipe_store(args)
            store.save_sitemap(fast_result.page_url, fast_result.tree)
            suggested = suggest_for_url(fast_result.page_url, recipe_store=store, memory_root=getattr(args, "memory_root", None), tree=fast_result.tree)
            dict_res["suggested_recipes"] = suggested
        except Exception:
            pass
        _json(dict_res)
        return
    manager, page = _manager_page(args)
    try:
        result = manager.observe(page, max_elements=args.max_elements, frame=_frame(manager, page, args.frame))
        dict_res = result.to_dict()
        try:
            from browser_core.recipes import RecipeStore, suggest_for_url
            store = _recipe_store(args)
            store.save_sitemap(result.page_url, result.tree)
            suggested = suggest_for_url(result.page_url, recipe_store=store, memory_root=getattr(args, "memory_root", None), tree=result.tree)
            dict_res["suggested_recipes"] = suggested
        except Exception:
            pass
        _json(dict_res)
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


def _store_get(store, recipe_id: str, memory: Any = None) -> Any:
    from browser_core.recipes import Recipe, InvalidExecutableArtifact

    # 1. Path directly
    p = Path(recipe_id)
    if p.exists() and p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as err:
            raise ValueError(f"Failed to parse JSON from {p}: {err}") from err
        if not isinstance(data, dict):
            raise InvalidExecutableArtifact(f"Artifact {p} must be a JSON object")
        if data.get("kind") == "omnibrowser.semantic_sitemap" or "interactive_elements" in data:
            raise InvalidExecutableArtifact(f"Cannot execute sitemap {p} as a recipe")
        if data.get("kind") not in (None, "omnibrowser.recipe", "omnibrowser.recipe_candidate"):
            raise InvalidExecutableArtifact(f"Cannot execute artifact with kind: {data.get('kind')}")
        return Recipe.from_dict(data)

    # 2. Store root / recipe_id (or with .json)
    if hasattr(store, "root"):
        cand_p = store.root / recipe_id
        if cand_p.is_file():
            data = json.loads(cand_p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and (data.get("kind") == "omnibrowser.semantic_sitemap" or "interactive_elements" in data):
                raise InvalidExecutableArtifact(f"Cannot execute sitemap {cand_p} as a recipe")
            return Recipe.from_dict(data)
        cand_json = store.root / f"{recipe_id}.json"
        if cand_json.is_file():
            data = json.loads(cand_json.read_text(encoding="utf-8"))
            if isinstance(data, dict) and (data.get("kind") == "omnibrowser.semantic_sitemap" or "interactive_elements" in data):
                raise InvalidExecutableArtifact(f"Cannot execute sitemap {cand_json} as a recipe")
            return Recipe.from_dict(data)

    for name in ("get", "get_recipe", "find"):
        method = getattr(store, name, None)
        if callable(method):
            try:
                result = method(recipe_id)
            except TypeError:
                continue
            if result is not None:
                return result
    for recipe in _store_recipes(store):
        data = _recipe_value(recipe)
        if data.get("id") == recipe_id:
            return recipe

    # 3. Learning memory candidates
    if memory is not None:
        try:
            candidates = memory.candidates()
            for cand in candidates:
                if cand.get("id") == recipe_id:
                    from browser_core.recipes import Recipe
                    return Recipe.from_dict(cand)
        except Exception:
            pass

    raise ValueError(f"Recipe not found: {recipe_id}")


def cmd_recipe_list(args):
    store = _recipe_store(args)
    recipes = _store_recipes(store, args.url)
    domain_filter = getattr(args, "domain", None)
    if domain_filter:
        d = domain_filter.lower().strip()
        filtered = []
        for recipe in recipes:
            val = _recipe_value(recipe)
            dp = val.get("domain_pattern", "")
            matches = False
            if isinstance(dp, list):
                if any(d in str(p).lower() for p in dp):
                    matches = True
            elif d in str(dp).lower():
                matches = True
            if not matches and getattr(recipe, "matches_url", None):
                if recipe.matches_url(f"https://{d}/") or recipe.matches_url(f"http://{d}/"):
                    matches = True
            if matches:
                filtered.append(recipe)
        recipes = filtered
    _json([_recipe_value(recipe) for recipe in recipes])


def cmd_recipe_show(args):
    store = _recipe_store(args)
    mem = _learning_memory(args)
    _json(_recipe_value(_store_get(store, args.recipe_id, mem)))


def _recipe_engine(manager, store=None):
    from browser_core.recipes import RecipeEngine

    for kwargs in ({"store": store}, {"manager": manager, "store": store}, {"page_manager": manager, "store": store}, {}):
        try:
            return RecipeEngine(**kwargs)
        except TypeError:
            continue
    return RecipeEngine(manager)


def _execute_recipe(engine, recipe, page, params, allow_r4: bool = False):
    execute = getattr(engine, "execute")
    for kwargs in (
        {"recipe": recipe, "page": page, "params": params, "allow_r4": allow_r4},
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
    mem = _learning_memory(args)
    recipe = _store_get(store, args.recipe_id, mem)
    manager, page = _manager_page(args)
    allow_r4 = bool(getattr(args, "allow_irreversible", False))
    try:
        result = _execute_recipe(_recipe_engine(manager, store), recipe, page, params, allow_r4=allow_r4)
        _json(_recipe_value(result))
    finally:
        manager.close()


def cmd_recipe_candidates(args):
    _json(_learning_memory(args).candidates(args.url))


def cmd_recipe_promote(args):
    recipe_id = getattr(args, "recipe_id", None) or getattr(args, "candidate_id", None)
    store = _recipe_store(args)
    recipe = store.get(recipe_id)
    if recipe is not None:
        target_state = getattr(args, "target_state", None)
        privileged = getattr(args, "privileged", False)
        actor = getattr(args, "actor", None)
        reason = getattr(args, "reason", None)
        promoted = store.promote(
            recipe_id,
            target_state=target_state,
            privileged=privileged,
            actor=actor,
            reason=reason,
        )
        _json(_recipe_value(promoted))
        return 0

    mem = _learning_memory(args)
    try:
        _json(mem.promote(recipe_id, approve=getattr(args, "approve", False)))
        return 0
    except Exception as err:
        if recipe is None:
            raise ValueError(f"Recipe not found: {recipe_id}") from err
        raise


def cmd_recipe_quarantine(args):
    store = _recipe_store(args)
    recipe = store.quarantine(args.recipe_id, reason=args.reason)
    _json(_recipe_value(recipe))
    return 0


def cmd_recipe_restore(args):
    store = _recipe_store(args)
    recipe = store.restore(args.recipe_id)
    _json(_recipe_value(recipe))
    return 0


def cmd_recipe_lifecycle(args):
    store = _recipe_store(args)
    recipe = store.get(args.recipe_id)
    if recipe is None:
        raise ValueError(f"Recipe not found: {args.recipe_id}")
    lifecycle = getattr(recipe, "lifecycle", None)
    lifecycle_dict = lifecycle.to_dict() if lifecycle and hasattr(lifecycle, "to_dict") else {}
    health = getattr(recipe, "health", None)
    health_dict = health.to_dict() if health and hasattr(health, "to_dict") else {}
    _json({
        "recipe_id": recipe.id,
        "name": recipe.name,
        "lifecycle": lifecycle_dict,
        "health": health_dict,
    })
    return 0


def cmd_recipe_suggest(args):
    _json(_learning_memory(args).suggest(args.url))


def cmd_recipe_repair(args):
    action = args.repair_command
    store = _recipe_store(args)
    if action == "list":
        candidates = store.list_repair_candidates(args.recipe_id)
        _json([c.to_dict() for c in candidates])
        return 0
    elif action == "inspect":
        candidates = store.list_repair_candidates()
        matched = [c for c in candidates if c.id == args.candidate_id]
        if not matched:
            raise ValueError(f"Repair candidate not found: {args.candidate_id}")
        _json(matched[-1].to_dict())
        return 0
    elif action == "apply":
        recipe = store.apply_repair_candidate(args.candidate_id, actor=args.actor, reason=args.reason)
        _json({
            "status": "applied",
            "recipe_id": recipe.id,
            "revision": recipe.lifecycle.revision,
            "generation": recipe.lifecycle.generation,
            "content_digest": recipe.lifecycle.content_digest,
        })
        return 0
    raise ValueError(f"Unknown repair action: {action}")


def cmd_workflow_plan(args):
    from browser_core.workflows import StateTransitionGraph, WorkflowComposer
    store = _recipe_store(args)
    graph = StateTransitionGraph()
    graph.ingest_store(store)

    start = args.from_state
    if start not in graph.states:
        for sid, s in graph.states.items():
            if s.route_pattern and (s.route_pattern in start or start in s.route_pattern):
                start = sid
                break

    composer = WorkflowComposer(graph)
    plan = composer.plan(start, args.to_state, max_allowed_risk=args.max_risk)
    if plan is None:
        raise ValueError(f"No path found from '{args.from_state}' to '{args.to_state}' within risk limit {args.max_risk}")
    _json(plan.to_dict())
    return 0


def cmd_workflow_run(args):
    from browser_core.contracts import WorkflowPlan, TransitionEdge
    from browser_core.workflows import WorkflowEngine

    raw = args.plan
    if Path(raw).is_file():
        with open(raw, "r", encoding="utf-8") as f:
            plan_data = json.load(f)
    else:
        plan_data = json.loads(raw)

    edges = [
        TransitionEdge(**e) if isinstance(e, dict) else e
        for e in plan_data.get("edges", [])
    ]
    plan = WorkflowPlan(
        plan_id=plan_data.get("plan_id", f"plan_{uuid4().hex[:8]}"),
        start_state=plan_data.get("start_state", ""),
        goal_state=plan_data.get("goal_state", ""),
        edges=edges,
        cumulative_risk=plan_data.get("cumulative_risk", "R0"),
        estimated_steps=plan_data.get("estimated_steps", len(edges)),
    )

    params = _read_json(args.params, None, {}) or {}
    if not isinstance(params, dict):
        raise ValueError("workflow run --params must be a JSON object")

    store = _recipe_store(args)
    manager, page = _manager_page(args)
    allow_r4 = bool(getattr(args, "allow_irreversible", False))
    try:
        engine = WorkflowEngine(store=store)
        result = engine.execute(plan, page, params, manager=manager, allow_r4=allow_r4)
        _json(result.to_dict())
        return 0 if result.ok else 1
    finally:
        manager.close()


def cmd_workflow_graph(args):
    from browser_core.workflows import StateTransitionGraph
    store = _recipe_store(args)
    graph = StateTransitionGraph()
    graph.ingest_store(store)
    data = graph.to_dict()
    if getattr(args, "domain", None):
        domain = args.domain
        data["states"] = {k: v for k, v in data["states"].items() if v.get("domain") == domain}
        data["edges"] = {k: [e for e in elist if k in data["states"]] for k, elist in data["edges"].items() if k in data["states"]}
    _json(data)
    return 0


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
    if getattr(args, "match", None) or getattr(args, "frame", None) or getattr(args, "lease", None):
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


def cmd_broker_lease_request(args):
    from browser_core.broker.session_router import SessionRouter
    from browser_core.contracts import BrowserRequirements

    router = SessionRouter()
    req = BrowserRequirements(
        execution_class=args.exec_class,
        auth_identity=args.auth,
        requires_visual=args.visual,
        timeout_seconds=args.timeout,
    )
    lease = router.request_lease(req, agent_id=args.agent_id, project_id=args.project_id)
    _json(lease.to_dict())


def cmd_broker_lease_release(args):
    from browser_core.broker.session_router import SessionRouter

    router = SessionRouter()
    router.release_lease(args.lease_id)
    _json({"released": args.lease_id, "ok": True})


def cmd_broker_status(args):
    from browser_core.broker.session_router import SessionRouter

    router = SessionRouter()
    _json(router.status())


def _parser():
    parser = argparse.ArgumentParser(description="CDP Browser Automation Controller")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL, help=f"CDP URL (default: {DEFAULT_CDP_URL})")
    parser.add_argument("--match", default=None, help="Target tab by URL substring")
    parser.add_argument("--lease", "--session", dest="lease", default=None, help="Scope operations to an active broker lease ID")
    parser.add_argument("--fencing-token", type=int, default=None, help="Fencing token for lease validation")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-tabs", help="List all open browser tabs")
    p = sub.add_parser("goto", help="Navigate tab to a specific URL"); p.add_argument("url")
    p = sub.add_parser("eval", help="Evaluate JavaScript expression on active page"); p.add_argument("expression")
    p = sub.add_parser("screenshot", help="Capture a screenshot"); p.add_argument("--output", default="screenshot.png")
    p = sub.add_parser("upload", help="Upload files to input[type=file]")
    p.add_argument("files", nargs="+", help="File paths to upload")
    p.add_argument("--selector", default="#upload-files", help="Selector for file input (default: #upload-files)")

    p = sub.add_parser("observe", help="Return a compact semantic DOM projection")
    p.add_argument("--scope", default="main", choices=["main", "viewport"])
    p.add_argument("--max-elements", type=int, default=80)
    p.add_argument("--frame", default=None)
    p.add_argument("--json", action="store_true", default=False, help="Output compact JSON")
    p.add_argument("--recipes-dir", default=None, help="Recipe directory (defaults to ./recipes)")
    p.add_argument("--memory-root", default=None, help="Local learned-memory root (defaults to ~/.omnibrowser/memory/v1)")
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
    p_list.add_argument("--domain", default=None, help="Only list recipes matching this domain")
    p_list.add_argument("--recipes-dir", default=None, help="Recipe directory (defaults to ./recipes)")
    p_show = recipe_sub.add_parser("show", help="Show recipe steps and health metadata")
    p_show.add_argument("recipe_id")
    p_show.add_argument("--recipes-dir", default=None, help="Recipe directory (defaults to ./recipes)")
    p_show.add_argument("--memory-root", default=None, help="Learning memory root")
    p_run = recipe_sub.add_parser("run", help="Execute a stored recipe against the active tab")
    p_run.add_argument("recipe_id")
    p_run.add_argument("--params", nargs="?", const="{}", default="{}", help="JSON object of recipe parameters")
    p_run.add_argument("--allow-irreversible", "--allow-r4", dest="allow_irreversible", action="store_true", default=False, help="Allow execution of R4 (irreversible/destructive) actions")
    p_run.add_argument("--recipes-dir", default=None, help="Recipe directory (defaults to ./recipes)")
    p_run.add_argument("--memory-root", default=None, help="Learning memory root")
    p_candidates = recipe_sub.add_parser("candidates", help="List locally learned draft recipes")
    p_candidates.add_argument("--url", default=None); p_candidates.add_argument("--memory-root", default=None)
    p_promote = recipe_sub.add_parser("promote", help="Promote a recipe or local learned draft")
    p_promote.add_argument("recipe_id", help="Recipe ID or candidate ID to promote")
    p_promote.add_argument("--target-state", default=None, help="Target lifecycle state (e.g. VERIFIED_SHARED, CURATED)")
    p_promote.add_argument("--privileged", action="store_true", default=False, help="Explicit privileged administrative override")
    p_promote.add_argument("--actor", default=None, help="Actor identity performing privileged promotion")
    p_promote.add_argument("--reason", default=None, help="Reason for privileged promotion")
    p_promote.add_argument("--approve", action="store_true", default=False, help="Approve learned draft promotion")
    p_promote.add_argument("--recipes-dir", default=None, help="Recipe directory (defaults to ./recipes)")
    p_promote.add_argument("--memory-root", default=None, help="Learning memory root")
    p_quarantine = recipe_sub.add_parser("quarantine", help="Quarantine a recipe")
    p_quarantine.add_argument("recipe_id", help="Recipe ID to quarantine")
    p_quarantine.add_argument("--reason", default="administrative_quarantine", help="Reason for quarantine")
    p_quarantine.add_argument("--recipes-dir", default=None, help="Recipe directory (defaults to ./recipes)")
    p_restore = recipe_sub.add_parser("restore", help="Restore a quarantined recipe")
    p_restore.add_argument("recipe_id", help="Recipe ID to restore")
    p_restore.add_argument("--recipes-dir", default=None, help="Recipe directory (defaults to ./recipes)")
    p_lifecycle = recipe_sub.add_parser("lifecycle", help="Show lifecycle and health status for a recipe")
    p_lifecycle.add_argument("recipe_id", help="Recipe ID to inspect")
    p_lifecycle.add_argument("--recipes-dir", default=None, help="Recipe directory (defaults to ./recipes)")
    p_suggest = recipe_sub.add_parser("suggest", help="Suggest learned recipes for a URL")
    p_suggest.add_argument("--url", default=None); p_suggest.add_argument("--memory-root", default=None)
    p_repair = recipe_sub.add_parser("repair", help="Manage self-healing repair candidates")
    repair_sub = p_repair.add_subparsers(dest="repair_command", required=True)
    p_rep_list = repair_sub.add_parser("list", help="List recorded repair candidates")
    p_rep_list.add_argument("--recipe-id", default=None, help="Filter by recipe ID")
    p_rep_list.add_argument("--recipes-dir", default=None, help="Recipe directory (defaults to ./recipes)")
    p_rep_inspect = repair_sub.add_parser("inspect", help="Inspect details of a repair candidate")
    p_rep_inspect.add_argument("candidate_id", help="Repair candidate ID to inspect")
    p_rep_inspect.add_argument("--recipes-dir", default=None, help="Recipe directory (defaults to ./recipes)")
    p_rep_apply = repair_sub.add_parser("apply", help="Apply a repair candidate to its recipe")
    p_rep_apply.add_argument("candidate_id", help="Repair candidate ID to apply")
    p_rep_apply.add_argument("--actor", default="offline_learner", help="Actor applying the repair")
    p_rep_apply.add_argument("--reason", default="self_healing_promotion", help="Reason for applying the repair")
    p_rep_apply.add_argument("--recipes-dir", default=None, help="Recipe directory (defaults to ./recipes)")

    # Workflow commands
    p_wf = sub.add_parser("workflow", help="Compose and execute multi-step state transition workflows")
    wf_sub = p_wf.add_subparsers(dest="workflow_command", required=True)
    p_wf_plan = wf_sub.add_parser("plan", help="Compute lowest-cost workflow plan between states")
    p_wf_plan.add_argument("--from", dest="from_state", required=True, help="Starting state ID or URL")
    p_wf_plan.add_argument("--to", dest="to_state", required=True, help="Target state ID")
    p_wf_plan.add_argument("--max-risk", default="R4", choices=["R0", "R1", "R2", "R3", "R4"], help="Maximum allowable edge risk")
    p_wf_plan.add_argument("--recipes-dir", default=None, help="Recipe directory (defaults to ./recipes)")
    p_wf_plan.add_argument("--memory-root", default=None, help="Learning memory root")
    p_wf_run = wf_sub.add_parser("run", help="Execute a planned workflow across state transitions")
    p_wf_run.add_argument("plan", help="Workflow plan JSON string or path to plan JSON file")
    p_wf_run.add_argument("--params", nargs="?", const="{}", default="{}", help="JSON object of parameters")
    p_wf_run.add_argument("--allow-irreversible", "--allow-r4", dest="allow_irreversible", action="store_true", default=False, help="Allow execution of R4 actions")
    p_wf_run.add_argument("--recipes-dir", default=None, help="Recipe directory (defaults to ./recipes)")
    p_wf_run.add_argument("--memory-root", default=None, help="Learning memory root")
    p_wf_graph = wf_sub.add_parser("graph", help="Dump state transition graph topology")
    p_wf_graph.add_argument("--domain", default=None, help="Filter states/edges by domain")
    p_wf_graph.add_argument("--recipes-dir", default=None, help="Recipe directory (defaults to ./recipes)")
    p_wf_graph.add_argument("--memory-root", default=None, help="Learning memory root")

    # Broker commands
    p_broker = sub.add_parser("broker", help="Concurrency and Resource Broker commands")
    broker_sub = p_broker.add_subparsers(dest="broker_command", required=True)
    p_lease = broker_sub.add_parser("lease", help="Manage broker leases")
    lease_sub = p_lease.add_subparsers(dest="lease_action", required=True)
    p_req = lease_sub.add_parser("request", help="Request a new browser lease")
    p_req.add_argument("--class", dest="exec_class", choices=["S", "I", "A"], default="S", help="Execution class (S=Shared, I=Isolated, A=Auth)")
    p_req.add_argument("--auth", default=None, help="Auth identity (e.g. google, github)")
    p_req.add_argument("--timeout", type=float, default=300.0, help="Lease timeout in seconds (default: 300)")
    p_req.add_argument("--agent-id", default="agent-default", help="Calling agent ID")
    p_req.add_argument("--project-id", default="workbench", help="Calling project ID")
    p_req.add_argument("--visual", action="store_true", default=False, help="Require visible/headful display")
    p_rel = lease_sub.add_parser("release", help="Release an active browser lease")
    p_rel.add_argument("lease_id", help="Lease ID to release")
    broker_sub.add_parser("status", help="Get broker and system telemetry status")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        command = {"runBrowserCode": "run-code", "inspectVisual": "visual"}.get(args.command, args.command)
        if command == "broker":
            if args.broker_command == "status":
                return cmd_broker_status(args)
            if args.broker_command == "lease":
                if args.lease_action == "request":
                    return cmd_broker_lease_request(args)
                if args.lease_action == "release":
                    return cmd_broker_lease_release(args)
        if command == "learn":
            return globals()[f"cmd_learn_{args.learn_command}"](args)
        if command == "recipe":
            return globals()[f"cmd_recipe_{args.recipe_command}"](args)
        if command == "workflow":
            return globals()[f"cmd_workflow_{args.workflow_command}"](args)
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
