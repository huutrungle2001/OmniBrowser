"""Task 005 procedural-memory tests using only isolated local browser sessions."""

from __future__ import annotations

import inspect
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any

import pytest

from browser_core.page_manager import PageManager
from browser_core.primitives import capture_network_traffic
from browser_core.recipes import Recipe, RecipeEngine, RecipeStore


def _value(result: Any, name: str, default: Any = None) -> Any:
    if isinstance(result, dict):
        return result.get(name, default)
    return getattr(result, name, default)


def _make_step(action: str, target: str | None = None, value: Any = None, **extra: Any) -> Any:
    """Construct a step across the small schema spelling variations used by workers."""
    payload = {"action": action, "target": target, "value": value, **extra}
    factory = getattr(RecipeStep, "from_dict", None)
    if factory:
        return factory({key: val for key, val in payload.items() if val is not None})
    parameters = inspect.signature(RecipeStep).parameters
    kwargs = {}
    for key, val in payload.items():
        if key in parameters and val is not None:
            kwargs[key] = val
    if "op" in parameters and "action" not in parameters:
        kwargs["op"] = action
    if "selector" in parameters and "target" not in parameters:
        kwargs["selector"] = target
    return RecipeStep(**kwargs)


def _make_recipe(
    recipe_id: str,
    *,
    domain_pattern: str = "127.0.0.1",
    steps: list[Any] | None = None,
    validation: dict[str, Any] | None = None,
) -> Any:
    payload = {
        "id": recipe_id,
        "name": "Interactive enrolment",
        "description": "Fast-path fixture recipe",
        "domain_pattern": domain_pattern,
        "steps": [
            {"action": "fill", "target": "#name", "value": "{{username}}"},
            {"action": "click", "target": "#save"},
        ] if steps is None else steps,
        "validation": validation or {"text_present": "Saved: {{username}}"},
        "metadata": {"version": 1, "success_count": 0, "failure_count": 0},
    }
    factory = getattr(Recipe, "from_dict", None)
    if factory:
        return factory(payload)
    parameters = inspect.signature(Recipe).parameters
    kwargs = {key: value for key, value in payload.items() if key in parameters}
    if "steps" in kwargs:
        kwargs["steps"] = [
            step if isinstance(step, RecipeStep) else _make_step(**step) for step in kwargs["steps"]
        ]
    return Recipe(**kwargs)


def _session(ephemeral_cdp_url, fixture_server):
    manager = PageManager(ephemeral_cdp_url, test_mode=True)
    manager.connect()
    page = manager.primary_page()
    page.goto(f"{fixture_server}/interactive_page.html", wait_until="domcontentloaded")
    return manager, page


def _execute(engine, recipe, page, params):
    signature = inspect.signature(engine.execute)
    names = list(signature.parameters)
    if names and names[0] in {"page", "context"}:
        return engine.execute(page, recipe, params)
    if len(names) > 1 and names[1] in {"page", "context"}:
        return engine.execute(recipe, page, params)
    try:
        return engine.execute(recipe, page, params)
    except TypeError:
        return engine.execute(recipe, params, page=page)


def test_recipe_store_matches_url_and_ignores_other_domains(tmp_path):
    recipe_path = Path(tmp_path) / "enrolment.json"
    recipe_path.write_text(json.dumps(_make_recipe("enrolment").to_dict()), encoding="utf-8")
    store = RecipeStore(tmp_path)
    loader = getattr(store, "load", None) or getattr(store, "scan", None) or getattr(store, "load_all", None)
    if loader:
        loader()
    matcher = getattr(store, "match_url", None) or getattr(store, "match", None) or getattr(store, "find_for_url", None)
    assert matcher is not None, "RecipeStore must expose a URL matching method"
    matched = matcher("http://127.0.0.1:43127/interactive_page.html")
    assert matched is not None
    if isinstance(matched, list):
        assert any(getattr(recipe, "id", None) == "enrolment" for recipe in matched)
    else:
        assert getattr(matched, "id", None) == "enrolment"
    assert matcher("https://example.invalid/interactive_page.html") in (None, [], False)


def test_recipe_engine_executes_parameterized_form_fast_path(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        recipe = _make_recipe("enrolment")
        engine = RecipeEngine(RecipeStore())
        result = _execute(engine, recipe, page, {"username": "Ada"})
        assert _value(result, "fallback_required", False) is False
        assert _value(result, "ok", _value(result, "success", True)) is True
        assert page.locator("#name").input_value() == "Ada"
        assert page.locator("#status").inner_text() == "Saved: Ada"
        metadata = getattr(recipe, "metadata", {})
        assert int(metadata.get("success_count", metadata.get("successes", 1))) >= 1
    finally:
        manager.close()


def test_recipe_engine_fails_closed_and_records_ui_drift(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        recipe = _make_recipe(
            "drifted",
            steps=[
                {"action": "fill", "target": "#selector-removed", "value": "{{username}}"},
                {"action": "click", "target": "#save"},
            ],
        )
        engine = RecipeEngine(RecipeStore())
        result = _execute(engine, recipe, page, {"username": "Ada"})
        assert _value(result, "fallback_required", False) is True
        diagnostic = str(_value(result, "diagnostic", _value(result, "message", result))).lower()
        assert "observe" in diagnostic or "fallback" in diagnostic
        metadata = getattr(recipe, "metadata", {})
        ledger = getattr(engine, "ledger", None) or metadata.get("ledger") or metadata.get("failure_ledger")
        failure_count = metadata.get("failure_count", metadata.get("failures", 0))
        assert ledger or int(failure_count) >= 1
        if ledger:
            assert "selector-removed" in json.dumps(ledger)
    finally:
        manager.close()


def test_recipe_distillation_masks_pii_values():
    recipes_module = sys.modules[Recipe.__module__]
    distill = next(
        (getattr(recipes_module, name, None) for name in ("distill_recipe", "distill_interaction", "recipe_from_trace")),
        None,
    )
    assert distill is not None, "Task 005 must expose a recipe distillation helper"
    trace = {
        "id": "signup",
        "name": "Signup",
        "url": "https://accounts.example.test/register",
        "steps": [
            {"action": "fill", "target": "#email", "value": "alice@example.com"},
            {"action": "fill", "target": "#password", "value": "Correct-Horse-Battery-Staple"},
            {"action": "eval", "value": "Bearer eyJhbGciOiJIUzI1NiJ9.secret"},
        ],
    }
    parameters = inspect.signature(distill).parameters
    if len(parameters) == 1:
        distilled = distill(trace)
    else:
        kwargs = {}
        for name in parameters:
            if name in {"interaction", "trace", "recording"}:
                kwargs[name] = trace
            elif name == "steps":
                kwargs[name] = trace["steps"]
            elif name in {"recipe_id", "id"}:
                kwargs[name] = "signup"
            elif name == "name":
                kwargs[name] = "Signup"
        distilled = distill(**kwargs)
    encoded = json.dumps(distilled.to_dict() if hasattr(distilled, "to_dict") else distilled)
    assert "alice@example.com" not in encoded
    assert "Correct-Horse-Battery-Staple" not in encoded
    assert "Bearer eyJhbGciOiJIUzI1NiJ9.secret" not in encoded
    assert "{{" in encoded and "}}" in encoded


class _NetworkHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib handler API
        if self.path == "/network.html":
            body = b"""<!doctype html><script>
              fetch('/api/data').then(r => r.json()).then(data => {
                document.body.textContent = data.message;
              });
            </script>"""
            content_type = "text/html"
        elif self.path == "/api/data":
            body = b'{"message":"network captured","ok":true}'
            content_type = "application/json"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


@pytest.fixture
def network_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _NetworkHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_capture_network_traffic_returns_json_response(ephemeral_cdp_url, network_server):
    manager = PageManager(ephemeral_cdp_url, test_mode=True)
    manager.connect()
    page = manager.primary_page()
    try:
        traffic = capture_network_traffic(
            manager,
            page,
            action=lambda: page.goto(f"{network_server}/network.html", wait_until="networkidle"),
            timeout_ms=250,
        )
        encoded = json.dumps(traffic, default=str)
        assert "/api/data" in encoded
        assert "application/json" in encoded or '"message"' in encoded
    finally:
        manager.close()


def test_recipe_cli_help_exposes_recipe_commands():
    controller = Path(__file__).parents[1] / "scripts" / "cdp_controller.py"
    result = subprocess.run(
        [sys.executable, str(controller), "recipe", "--help"],
        cwd=controller.parents[1],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "list" in result.stdout
    assert "run" in result.stdout
    assert "show" in result.stdout


def test_recipe_cli_list_and_show_read_recipe_store(tmp_path):
    controller = Path(__file__).parents[1] / "scripts" / "cdp_controller.py"
    recipe = _make_recipe("cli-enrolment")
    (Path(tmp_path) / "cli-enrolment.json").write_text(recipe.to_json(), encoding="utf-8")
    listed = subprocess.run(
        [sys.executable, str(controller), "recipe", "list", "--recipes-dir", str(tmp_path)],
        cwd=controller.parents[1], text=True, capture_output=True, check=False,
    )
    assert listed.returncode == 0, listed.stderr
    assert any(item["id"] == "cli-enrolment" for item in json.loads(listed.stdout))
    shown = subprocess.run(
        [sys.executable, str(controller), "recipe", "show", "cli-enrolment", "--recipes-dir", str(tmp_path)],
        cwd=controller.parents[1], text=True, capture_output=True, check=False,
    )
    assert shown.returncode == 0, shown.stderr
    payload = json.loads(shown.stdout)
    assert payload["id"] == "cli-enrolment"
    assert payload["steps"][0]["action"] == "fill"


def test_recipe_cli_run_executes_against_active_ephemeral_tab(
    ephemeral_cdp_url, fixture_server, tmp_path
):
    controller = Path(__file__).parents[1] / "scripts" / "cdp_controller.py"
    recipe = _make_recipe("cli-run")
    (Path(tmp_path) / "cli-run.json").write_text(recipe.to_json(), encoding="utf-8")
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        result = subprocess.run(
            [
                sys.executable, str(controller),
                "--cdp-url", ephemeral_cdp_url,
                "recipe", "run", "cli-run",
                "--params", json.dumps({"username": "Lin"}),
                "--recipes-dir", str(tmp_path),
            ],
            cwd=controller.parents[1], text=True, capture_output=True, check=False,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert page.locator("#status").inner_text() == "Saved: Lin"
    finally:
        manager.close()
