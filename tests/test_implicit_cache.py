"""Tests for Task-008: Implicit Flight Recorder & Domain-Scoped Procedural Cache."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from browser_core.contracts import ObserveResult
from browser_core.engine import act
from browser_core.page_manager import PageManager
from browser_core.recipes import (
    FlightJournal,
    FlightRecorder,
    InvalidExecutableArtifact,
    LearningMemory,
    Recipe,
    RecipeStep,
    RecipeStore,
    _domain_from_url,
    _normalize_path,
    _safe_url,
    suggest_for_url,
)


def _session(ephemeral_cdp_url, fixture_server):
    manager = PageManager(ephemeral_cdp_url, test_mode=True)
    manager.connect()
    page = manager.primary_page()
    manager.install_scanner(page)
    page.goto(f"{fixture_server}/interactive_page.html", wait_until="domcontentloaded")
    return manager, page


def _ref(manager, page, name):
    return next(node.ref for node in manager.observe(page).tree if node.name == name)


def _make_dummy_recipe(recipe_id: str, domain: str = "example.com") -> Recipe:
    return Recipe(
        id=recipe_id,
        name=f"Recipe {recipe_id}",
        description=f"Description for {recipe_id}",
        domain_pattern=domain,
        steps=[
            RecipeStep(
                action="fill",
                target={"name": "username", "tag": "input"},
                value={"$ref": "username"},
            ),
            RecipeStep(
                action="click",
                target={"name": "submit", "tag": "button"},
            ),
        ],
        validation={"expect_text": {"target": {"tag": "div", "id": "status"}, "value": "Success"}},
    )


def test_domain_helpers():
    assert _domain_from_url("https://portal.unitemps.com/login?redirect=1") == "portal.unitemps.com"
    assert _domain_from_url("http://127.0.0.1:8080/app") == "127.0.0.1"
    assert _domain_from_url("about:blank") == "about:blank"
    assert _domain_from_url("invalid_url") == "invalid_url"
    assert _domain_from_url("") == "generic"

    # Gate C: Userinfo, custom port, IPv6
    assert _domain_from_url("https://user:pass@example.com:8443/path") == "example.com"
    assert _domain_from_url("https://[::1]:9222/json") == "::1"

    # Safe URL: strips userinfo, query params, and fragments
    assert _safe_url("https://user:pass@example.com:8443/reset-password?token=secret123#frag") == "https://example.com:8443/reset-password"
    assert _safe_url("https://example.com/oauth/callback?code=supersecret") == "https://example.com/oauth/callback"

    assert _normalize_path("https://portal.unitemps.com/login?redirect=1") == "login"
    assert _normalize_path("https://portal.unitemps.com/members/candidate/profile") == "members_candidate_profile"
    assert _normalize_path("https://portal.unitemps.com/") == "_root"
    assert _normalize_path("https://portal.unitemps.com") == "_root"


def test_domain_scoped_recipe_store_and_sitemap(tmp_path):
    store = RecipeStore(root=tmp_path)
    recipe = _make_dummy_recipe("login-flow", domain="portal.unitemps.com")

    # 1. Save recipe under domain
    saved_path = store.save(recipe, domain="portal.unitemps.com")
    assert saved_path == tmp_path / "portal.unitemps.com" / "login-flow.json"
    assert saved_path.exists()

    # 2. Save and retrieve sitemap under domain
    sample_nodes = [
        {"role": "textbox", "name": "Email", "ref": "e1", "interactive": True, "value": "test@example.com"},
        {"role": "button", "name": "Log In", "ref": "e2", "interactive": True},
        {"role": "heading", "name": "Welcome", "ref": "e3", "interactive": False},
    ]
    sitemap_path = store.save_sitemap("https://portal.unitemps.com/Account/SignOn", sample_nodes)
    assert sitemap_path == tmp_path / "portal.unitemps.com" / "sitemaps" / "Account_SignOn.json"
    assert sitemap_path.exists()

    sitemap_data = store.get_sitemap("https://portal.unitemps.com/Account/SignOn")
    assert sitemap_data is not None
    assert sitemap_data["domain"] == "portal.unitemps.com"
    assert sitemap_data["normalized_path"] == "Account_SignOn"
    # Heading shouldn't be included because interactive=False
    assert len(sitemap_data["interactive_elements"]) == 2
    # Sensitive value should be sanitized
    assert sitemap_data["interactive_elements"][0]["name"] == "Email"
    assert sitemap_data["interactive_elements"][0]["value"] == "{{email}}"
    assert "test@example.com" not in json.dumps(sitemap_data)

    # 3. RecipeStore.load() must load recipes from domain folders and IGNORE sitemaps
    recipes = store.load()
    assert len(recipes) == 1
    assert recipes[0].id == "login-flow"
    assert store.get("login-flow") is not None


def test_domain_scoped_learning_memory(tmp_path):
    mem = LearningMemory(tmp_path)
    run = mem.begin("login to portal", agent_id="agent-007")
    run_id = run["run_id"]

    # Append journal steps with a URL
    url = "https://portal.unitemps.com/login"
    mem.append_action(
        run_id,
        action="fill",
        anchor={"name": "username", "context": {"name": "username", "tag": "input"}},
        value="user@unitemps.com",
        url=url,
    )
    mem.append_action(
        run_id,
        action="fill",
        anchor={"name": "password", "context": {"name": "password", "type": "password", "tag": "input"}},
        value="SuperSecretPass123!",
        url=url,
    )

    # Complete the run
    completed = mem.complete(run_id, success=True)
    candidate_id = completed["candidate_id"]
    assert candidate_id

    # Candidate should be stored under portal.unitemps.com/candidates/
    domain_cand = tmp_path / "portal.unitemps.com" / "candidates" / f"{candidate_id}.json"
    assert domain_cand.exists()

    # Verify candidates() retrieves it
    cands = mem.candidates(url="https://portal.unitemps.com/login")
    assert any(c["id"] == candidate_id for c in cands)

    # Verify password sanitization in stored candidate
    cand_data = json.loads(domain_cand.read_text(encoding="utf-8"))
    pass_step = next(s for s in cand_data["steps"] if s["target"]["name"] == "password")
    assert pass_step["value"]["$ref"] == "password"
    assert pass_step["value"]["sensitivity"] == "secret"
    assert "SuperSecretPass123!" not in domain_cand.read_text(encoding="utf-8")

    # Promote candidate to curated recipe
    promoted = mem.promote(candidate_id, approve=True)
    assert promoted["status"] == "promoted"


def test_implicit_flight_recorder_and_auto_distillation(tmp_path, ephemeral_cdp_url, fixture_server):
    FlightRecorder.clear()
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        # 1. First action: Fill Name (without learn_run)
        name_ref = _ref(manager, page, "Name")
        act_res = act(page, "fill", name_ref, "Bob", manager=manager, memory_root=str(tmp_path))
        assert act_res.ok

        # Verify action recorded in session journal
        journal = FlightRecorder.get_journal(page)
        assert len(journal.events) >= 1
        assert journal.events[-1]["action"] == "fill"

        # 2. Second action: Click Save (triggers form submission boundary)
        save_ref = _ref(manager, page, "Save details")
        act(page, "click", save_ref, manager=manager, memory_root=str(tmp_path))

        # Check that auto-distillation occurred
        server_domain = _domain_from_url(fixture_server)
        candidates_dir = tmp_path / server_domain / "candidates"
        candidates = list(candidates_dir.glob("*.json")) if candidates_dir.exists() else []
        assert len(candidates) >= 1

        cand_data = json.loads(candidates[0].read_text(encoding="utf-8"))
        assert cand_data["metadata"]["source"] == "flight_recorder"
        assert cand_data["metadata"]["status"] == "draft"
        assert len(cand_data["steps"]) >= 1
    finally:
        manager.close()


def test_suggest_for_url_and_observe_integration(tmp_path, ephemeral_cdp_url, fixture_server):
    # 1. Create a curated recipe matching fixture_server
    server_domain = _domain_from_url(fixture_server)
    store = RecipeStore(root=tmp_path / "recipes")
    recipe = _make_dummy_recipe("quick-submit", domain=server_domain)
    store.save(recipe, domain=server_domain)

    # 2. Query suggestions directly
    target_url = f"{fixture_server}/interactive_page.html"
    suggestions = suggest_for_url(target_url, recipe_store=store, memory_root=tmp_path / "memory")
    assert len(suggestions) >= 1
    match = suggestions[0]
    assert match["recipe_id"] == "quick-submit"
    assert match["confidence"] >= 0.8
    assert "python3 scripts/cdp_controller.py recipe run quick-submit" in match["command"]

    # 3. Observe page and verify suggested_recipes in ObserveResult
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        res = manager.observe(page, recipe_store=store, memory_root=str(tmp_path / "memory"))
        assert isinstance(res, ObserveResult)
        assert len(res.suggested_recipes) >= 1
        assert res.suggested_recipes[0]["recipe_id"] == "quick-submit"
        assert res.suggested_recipes[0]["confidence"] >= 0.8
    finally:
        manager.close()


def test_cli_recipe_domain_filter_and_run(tmp_path, ephemeral_cdp_url, fixture_server):
    controller = Path(__file__).parents[1] / "scripts" / "cdp_controller.py"
    recipes_dir = tmp_path / "recipes"
    store = RecipeStore(root=recipes_dir)

    r1 = _make_dummy_recipe("r-domain1", domain="portal.unitemps.com")
    r2 = _make_dummy_recipe("r-domain2", domain="example.org")
    store.save(r1, domain="portal.unitemps.com")
    store.save(r2, domain="example.org")

    # Test recipe list --domain portal.unitemps.com
    listed = subprocess.run(
        [
            sys.executable, str(controller),
            "recipe", "list",
            "--domain", "portal.unitemps.com",
            "--recipes-dir", str(recipes_dir),
        ],
        cwd=controller.parents[1], text=True, capture_output=True, check=False,
    )
    assert listed.returncode == 0, listed.stderr
    items = json.loads(listed.stdout)
    assert len(items) == 1
    assert items[0]["id"] == "r-domain1"

    # Test recipe show by path
    r1_path = recipes_dir / "portal.unitemps.com" / "r-domain1.json"
    shown = subprocess.run(
        [
            sys.executable, str(controller),
            "recipe", "show", str(r1_path),
            "--recipes-dir", str(recipes_dir),
        ],
        cwd=controller.parents[1], text=True, capture_output=True, check=False,
    )
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout)["id"] == "r-domain1"

    # Test recipe run by path against active page
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        # Create a runnable recipe for interactive page
        server_domain = _domain_from_url(fixture_server)
        run_recipe = Recipe(
            id="run-by-path",
            name="Run By Path",
            domain_pattern=server_domain,
            steps=[
                RecipeStep(
                    action="fill",
                    target={"name": "Name", "tag": "input", "selector": "#name"},
                    value={"$ref": "username"},
                ),
            ],
        )
        run_path = recipes_dir / server_domain / "run-by-path.json"
        store.save(run_recipe, domain=server_domain)

        run_res = subprocess.run(
            [
                sys.executable, str(controller),
                "--cdp-url", ephemeral_cdp_url,
                "recipe", "run", str(run_path),
                "--params", json.dumps({"username": "Charlie"}),
                "--recipes-dir", str(recipes_dir),
            ],
            cwd=controller.parents[1], text=True, capture_output=True, check=False,
        )
        assert run_res.returncode == 0, run_res.stderr
        payload = json.loads(run_res.stdout)
        assert payload["ok"] is True
        assert page.locator("#name").input_value() == "Charlie"
    finally:
        manager.close()


def test_recipe_run_rejects_sitemap_and_invalid_artifacts_direct_path(tmp_path):
    controller = Path(__file__).parents[1] / "scripts" / "cdp_controller.py"
    store = RecipeStore(root=tmp_path)

    # 1. Create a sitemap file
    sample_nodes = [{"role": "textbox", "name": "Email", "ref": "e1", "interactive": True}]
    sitemap_path = store.save_sitemap("https://example.com/test", sample_nodes)
    assert sitemap_path.exists()

    # Direct execution of sitemap must fail closed
    res_sitemap = subprocess.run(
        [
            sys.executable, str(controller),
            "recipe", "run", str(sitemap_path),
            "--recipes-dir", str(tmp_path),
        ],
        cwd=controller.parents[1], text=True, capture_output=True, check=False,
    )
    assert res_sitemap.returncode != 0
    assert "Sitemaps cannot be executed" in res_sitemap.stderr or "Cannot execute sitemap" in res_sitemap.stderr

    # Recipe.from_dict directly rejecting sitemap
    sitemap_data = json.loads(sitemap_path.read_text(encoding="utf-8"))
    with pytest.raises(InvalidExecutableArtifact, match="Sitemaps cannot be executed"):
        Recipe.from_dict(sitemap_data)

    # 2. Reject unknown artifact kind
    unknown_file = tmp_path / "unknown_artifact.json"
    unknown_file.write_text(json.dumps({"kind": "omnibrowser.unknown_kind", "id": "u1"}), encoding="utf-8")
    res_unknown = subprocess.run(
        [
            sys.executable, str(controller),
            "recipe", "run", str(unknown_file),
            "--recipes-dir", str(tmp_path),
        ],
        cwd=controller.parents[1], text=True, capture_output=True, check=False,
    )
    assert res_unknown.returncode != 0
    assert "Cannot execute artifact with kind" in res_unknown.stderr or "Invalid executable artifact" in res_unknown.stderr


def test_flight_recorder_privacy_deny_by_default(tmp_path):
    # Gate A: verify deny-by-default parameterization and secret detection
    # 1. Password input
    v_pass = LearningMemory._value_ref("fill", "SuperSecret123!", {"name": "user_password", "type": "password"})
    assert v_pass == {"$ref": "password", "kind": "runtime_param", "sensitivity": "secret", "persist_value": False}

    # 2. Autocomplete one-time-code
    v_otp = LearningMemory._value_ref("fill", "987654", {"autocomplete": "one-time-code", "name": "verification_code"})
    assert v_otp == {"$ref": "verification_code", "kind": "runtime_param", "sensitivity": "secret", "persist_value": False}

    # 3. API key
    v_api = LearningMemory._value_ref("fill", "sk-1234567890abcdef", {"name": "apiKey"})
    assert v_api == {"$ref": "password", "kind": "runtime_param", "sensitivity": "secret", "persist_value": False}

    # 4. Bearer token in text
    v_bearer = LearningMemory._value_ref("fill", "Bearer eyJhbGciOi...", {"name": "authHeader"})
    assert v_bearer == {"$ref": "token", "kind": "runtime_param", "sensitivity": "secret", "persist_value": False}

    # 5. Free-form user input (e.g. textarea, chat composer, support ticket)
    free_form_text = "Here is my internal company notes and customer secret info"
    v_free = LearningMemory._value_ref("fill", free_form_text, {"tag": "textarea", "name": "comments"})
    assert v_free == {"$ref": "input_value", "kind": "runtime_param", "sensitivity": "runtime", "persist_value": False}
    assert free_form_text not in json.dumps(v_free)


def test_suggest_for_url_argv_and_quoting_safety(tmp_path):
    # Gate D: structured argv and shell quote safety
    # 1. Recipe.validate() strictly rejects shell injection characters in recipe IDs:
    with pytest.raises(ValueError, match="Recipe id must contain only letters"):
        Recipe(
            id="unsafe;rm -rf /;recipe",
            name="Quoting Test",
            domain_pattern="example.com",
            steps=[RecipeStep(action="click", target={"name": "btn", "tag": "button"})],
        )

    # 2. For valid recipe with parameters, verify structured argv and proper shell quoting:
    recipe = Recipe(
        id="quoting-test-recipe",
        name="Quoting Test",
        domain_pattern="example.com",
        steps=[
            RecipeStep(
                action="fill",
                target={"name": "field", "tag": "input"},
                value="{{query}}",
            ),
        ],
    )
    store = RecipeStore(root=tmp_path)
    store.save(recipe, domain="example.com")
    suggestions = suggest_for_url("https://example.com/search", recipe_store=store)

    target_sugg = next((s for s in suggestions if s["recipe_id"] == "quoting-test-recipe"), None)
    assert target_sugg is not None
    assert "argv" in target_sugg
    # argv contains structured command arguments
    assert target_sugg["argv"][:4] == ["python3", "scripts/cdp_controller.py", "recipe", "run"]
    assert target_sugg["argv"][4] == "quoting-test-recipe"
    assert target_sugg["argv"][5] == "--params"
    # command string has proper shell quoting
    assert target_sugg["command"].startswith("python3 scripts/cdp_controller.py recipe run quoting-test-recipe --params '")


def test_flight_recorder_failure_safe_distillation_and_filtering(tmp_path):
    # Gate E: only successful actions distilled, journal truncation only upon persistence
    journal = FlightJournal(session_id="test_session_failure_filter", domain="example.com", initial_url="https://example.com/app")
    journal.events = [
        {"seq": 1, "action": "click", "status": "success", "target": {"tag": "button", "name": "Start"}, "value": None, "expect": {}, "url": "https://example.com/app", "after_url": "https://example.com/app", "duration_ms": 10, "timestamp": 1.0},
        {"seq": 2, "action": "click", "status": "failed", "target": {"tag": "button", "name": "Broken"}, "value": None, "expect": {}, "url": "https://example.com/app", "after_url": "https://example.com/app", "duration_ms": 10, "timestamp": 2.0},
        {"seq": 3, "action": "fill", "status": "success", "target": {"tag": "input", "name": "email"}, "value": {"$ref": "email"}, "expect": {}, "url": "https://example.com/app", "after_url": "https://example.com/app", "duration_ms": 15, "timestamp": 3.0},
    ]

    distilled = FlightRecorder.distill_journal(journal, memory_root=tmp_path)
    assert distilled is not None
    candidate_steps = distilled["recipe"]["steps"]
    # Only the 2 successful steps should be in the candidate! Step 2 ('Broken') must be filtered out!
    assert len(candidate_steps) == 2
    assert candidate_steps[0]["target"]["name"] == "Start"
    assert candidate_steps[1]["target"]["name"] == "email"
