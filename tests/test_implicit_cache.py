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
    FlightRecorder,
    LearningMemory,
    Recipe,
    RecipeStep,
    RecipeStore,
    _domain_from_url,
    _normalize_path,
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
