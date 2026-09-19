"""Tests for Task-009: Guarded State Transitions, Composite Semantic Matcher & Risk Classes (Milestone v2.4)."""

import json
from pathlib import Path
import pytest

from browser_core.contracts import (
    ExecutionOutcome,
    ForbiddenAnchorError,
    HealthStats,
    MatcherSpec,
    PreconditionFailedError,
    RiskClass,
    RiskGateError,
    SafetySpec,
)
from browser_core.page_manager import PageManager
from browser_core.recipes import (
    Recipe,
    RecipeEngine,
    RecipeStep,
    RecipeStore,
    calculate_semantic_similarity,
    classify_action_risk,
    extract_semantic_fingerprint,
    get_recipe_risk,
    match_page_state,
    suggest_for_url,
)


def _session(ephemeral_cdp_url, fixture_server):
    manager = PageManager(ephemeral_cdp_url, test_mode=True)
    manager.connect()
    page = manager.primary_page()
    manager.install_scanner(page)
    page.goto(f"{fixture_server}/interactive_page.html", wait_until="domcontentloaded")
    return manager, page


# 1. Semantic Anchor Verification & Veto
def test_semantic_anchor_verification_and_veto():
    nodes = [
        {"role": "heading", "name": "User Settings", "value": ""},
        {"role": "textbox", "name": "Email", "value": "user@example.com"},
        {"role": "button", "name": "Save details", "value": ""},
    ]

    # Required anchors match
    recipe = Recipe(
        id="save-settings",
        name="Save Settings",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target={"name": "Save details"})],
        matcher=MatcherSpec(
            required_anchors=[{"role": "heading", "name": "User Settings"}],
            min_similarity=0.60,
        ),
    )
    res = match_page_state(recipe, "https://example.com/settings", nodes)
    assert res["matched"] is True
    assert res["score"] >= 0.60
    assert res["anchor_coverage"] == 1.0

    # Required anchor missing -> match fails with score 0.0
    recipe_missing = Recipe(
        id="save-billing",
        name="Save Billing",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target={"name": "Save details"})],
        matcher=MatcherSpec(
            required_anchors=[{"role": "heading", "name": "Billing Overview"}],
            min_similarity=0.60,
        ),
    )
    res_missing = match_page_state(recipe_missing, "https://example.com/settings", nodes)
    assert res_missing["matched"] is False
    assert res_missing["score"] == 0.0
    assert "missing" in res_missing["reason"]

    # Forbidden anchor detected -> strictly vetos match (score 0.0)
    nodes_with_error = nodes + [{"role": "dialog", "name": "Session expired", "value": ""}]
    recipe_with_veto = Recipe(
        id="save-settings-veto",
        name="Save Settings Veto",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target={"name": "Save details"})],
        matcher=MatcherSpec(
            required_anchors=[{"role": "heading", "name": "User Settings"}],
            forbidden_anchors=[{"role": "dialog", "name": "Session expired"}],
            min_similarity=0.60,
        ),
    )
    res_veto = match_page_state(recipe_with_veto, "https://example.com/settings", nodes_with_error)
    assert res_veto["matched"] is False
    assert res_veto["score"] == 0.0
    assert "forbidden" in res_veto["reason"]


# 2. Semantic Fingerprint Jaccard Similarity
def test_semantic_fingerprint_jaccard_similarity():
    fp_a = {("heading", "dashboard"), ("button", "save"), ("link", "profile")}
    fp_b = {("heading", "dashboard"), ("button", "save"), ("link", "profile")}
    # Exact match -> 1.0
    assert calculate_semantic_similarity(fp_a, fp_b) == 1.0

    # 2 common out of 4 total -> 0.5
    fp_c = {("heading", "dashboard"), ("button", "save"), ("link", "settings")}
    sim = calculate_semantic_similarity(fp_a, fp_c)
    assert sim == 0.5

    # Completely disjoint -> 0.0
    fp_d = {("textbox", "search"), ("button", "search")}
    assert calculate_semantic_similarity(fp_a, fp_d) == 0.0

    # Both empty -> 1.0
    assert calculate_semantic_similarity(set(), set()) == 1.0


# 3. Risk Class Classification
def test_risk_class_classification():
    # R0 Read-only
    assert classify_action_risk("observe") == RiskClass.R0_READONLY
    assert classify_action_risk("inspect", target="main") == RiskClass.R0_READONLY
    assert classify_action_risk("wait_for", target="heading") == RiskClass.R0_READONLY

    # R1 Reversible Nav
    assert classify_action_risk("click", target={"name": "Dashboard Tab"}) == RiskClass.R1_REVERSIBLE_NAV
    assert classify_action_risk("click", target="nav a.profile") == RiskClass.R1_REVERSIBLE_NAV
    assert classify_action_risk("click", target="filter-dropdown") == RiskClass.R1_REVERSIBLE_NAV

    # R2 Local Mutable UI
    assert classify_action_risk("fill", target="username", value="trung") == RiskClass.R2_LOCAL_MUTABLE
    assert classify_action_risk("select", target="country", value="VN") == RiskClass.R2_LOCAL_MUTABLE

    # R3 Persistent Mutation
    assert classify_action_risk("click", target="Submit Application") == RiskClass.R3_PERSISTENT_MUTATION
    assert classify_action_risk("click", target="Save changes") == RiskClass.R3_PERSISTENT_MUTATION
    assert classify_action_risk("click", target="Create project") == RiskClass.R3_PERSISTENT_MUTATION

    # R4 Irreversible / High-Risk
    assert classify_action_risk("click", target="Delete account") == RiskClass.R4_IRREVERSIBLE
    assert classify_action_risk("click", target="Confirm Payment") == RiskClass.R4_IRREVERSIBLE
    assert classify_action_risk("click", target="Checkout Now") == RiskClass.R4_IRREVERSIBLE
    assert classify_action_risk("click", target="Purge database") == RiskClass.R4_IRREVERSIBLE


# 4. Risk Class R4 Gating (Execution fails closed without explicit permission)
def test_risk_class_r4_gating(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        r4_recipe = Recipe(
            id="delete-user-record",
            name="Delete Record",
            domain_pattern="127.0.0.1",
            steps=[
                RecipeStep(action="click", target={"name": "Delete database record"}),
            ],
            safety=SafetySpec(max_risk=RiskClass.R4_IRREVERSIBLE, allow_r4=False),
        )

        engine = RecipeEngine()
        # Should raise RiskGateError when allow_r4 is False
        with pytest.raises(RiskGateError) as exc_info:
            engine.execute(r4_recipe, page, allow_r4=False, manager=manager)
        assert "R4" in str(exc_info.value)
        assert "allow_r4" in str(exc_info.value)
    finally:
        manager.close()


# 5. Outcome Classification: SAFE_FAILURE vs UNKNOWN_SIDE_EFFECT
def test_outcome_classification_safe_failure(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        # Pre-mutation failure: target not found on fill step (R2)
        safe_recipe = Recipe(
            id="safe-fail",
            name="Safe Failure Test",
            domain_pattern="127.0.0.1",
            steps=[
                RecipeStep(action="fill", target={"name": "non_existent_field"}, value="test"),
                RecipeStep(action="click", target={"name": "Save details"}),
            ],
        )
        engine = RecipeEngine()
        result = engine.execute(safe_recipe, page, manager=manager)
        assert result.ok is False
        assert result.outcome == ExecutionOutcome.SAFE_FAILURE
        assert result.reconciled is False
    finally:
        manager.close()


def test_outcome_classification_unknown_side_effect(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        # Persistent mutation click fails and no postcondition to reconcile
        mutation_recipe = Recipe(
            id="mutation-fail",
            name="Mutation Failure Test",
            domain_pattern="127.0.0.1",
            steps=[
                RecipeStep(action="click", target={"name": "Save details"}),
                # Following step fails after persistent mutation
                RecipeStep(action="click", target={"name": "non_existent_followup"}),
            ],
            safety=SafetySpec(unknown_effect_policy="reconcile"),
        )
        engine = RecipeEngine()
        result = engine.execute(mutation_recipe, page, manager=manager)
        assert result.ok is False
        assert result.outcome == ExecutionOutcome.UNKNOWN_SIDE_EFFECT
        assert result.reconciled is False
    finally:
        manager.close()


# 6. Postcondition Reconciliation Recovers Success
def test_postcondition_reconciliation_recovers_success(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        # Interactive page has a button '#save' that persists details
        # We specify postcondition that requires the text present after mutation
        reconcile_recipe = Recipe(
            id="reconcile-test",
            name="Reconciliation Test",
            domain_pattern="127.0.0.1",
            steps=[
                # Click Save details (R3 persistent mutation)
                RecipeStep(action="click", target="#save"),
                # Then deliberate failure step after mutation
                RecipeStep(action="click", target="#missing_button_trigger_failure"),
            ],
            postconditions=[
                # Postcondition check: Save details button is verified on the page
                {"role": "button", "name": "Save details"},
            ],
            safety=SafetySpec(unknown_effect_policy="reconcile"),
        )
        engine = RecipeEngine()
        result = engine.execute(reconcile_recipe, page, manager=manager)
        # Because the first click executed successfully and the postcondition is satisfied,
        # the engine reconciles the outcome as CONFIRMED_SUCCESS!
        assert result.ok is True
        assert result.outcome == ExecutionOutcome.CONFIRMED_SUCCESS
        assert result.reconciled is True
    finally:
        manager.close()


# 7. Precondition & Forbidden Anchor Live Page Guard Enforcement
def test_live_page_guard_enforcement(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        engine = RecipeEngine()

        # 1. Missing required anchor raises PreconditionFailedError
        recipe_missing_req = Recipe(
            id="missing-req",
            name="Missing Required Anchor",
            domain_pattern="127.0.0.1",
            steps=[RecipeStep(action="click", target={"name": "Save details"})],
            matcher=MatcherSpec(
                required_anchors=[{"role": "heading", "name": "Nonexistent Section"}],
            ),
        )
        with pytest.raises(PreconditionFailedError):
            engine.execute(recipe_missing_req, page, manager=manager)

        # 2. Forbidden anchor present raises ForbiddenAnchorError
        recipe_forbidden = Recipe(
            id="forbidden-present",
            name="Forbidden Anchor Present",
            domain_pattern="127.0.0.1",
            steps=[RecipeStep(action="click", target={"name": "Save details"})],
            matcher=MatcherSpec(
                forbidden_anchors=[{"role": "button", "name": "Save details"}],
            ),
        )
        with pytest.raises(ForbiddenAnchorError):
            engine.execute(recipe_forbidden, page, manager=manager)
    finally:
        manager.close()


# 8. Health Telemetry Tracking
def test_health_telemetry_tracking(tmp_path):
    store = RecipeStore(root=tmp_path / "recipes")
    recipe = Recipe(
        id="health-tracking",
        name="Health Tracking Recipe",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target={"name": "Submit"})],
        health=HealthStats(executions=0, successes=0, failures=0, health_score=1.0),
    )
    store.save(recipe, domain="example.com")

    # Record 3 successes
    for _ in range(3):
        store.record_success(recipe)
    assert recipe.health.executions == 3
    assert recipe.health.successes == 3
    assert recipe.health.health_score == 1.0

    # Record 1 failure
    store.record_failure(recipe, step_index=0, reason="timeout", target="Submit")
    assert recipe.health.executions == 4
    assert recipe.health.failures == 1
    assert recipe.health.health_score == 0.75


# 9. Backward Compatibility for v2.3 Recipes
def test_backward_compatibility_v2_3_recipe():
    # v2.3 recipe JSON with no matcher, safety, or health specs
    v2_3_dict = {
        "kind": "omnibrowser.recipe",
        "schema_version": 1,
        "id": "legacy-recipe",
        "name": "Legacy Recipe v2.3",
        "domain_pattern": "legacy.example.com",
        "steps": [
            {"action": "fill", "target": "input#name", "value": "Test"},
            {"action": "click", "target": "button#submit"},
        ],
        "metadata": {"source": "flight_recorder"},
    }
    recipe = Recipe.from_dict(v2_3_dict)
    assert recipe.id == "legacy-recipe"
    assert recipe.matcher is not None
    assert recipe.safety is not None
    assert recipe.health is not None
    assert recipe.safety.max_risk == "R2"
    assert recipe.health.health_score == 1.0

    # Serializing back maintains kind and adds v2.4 fields
    dumped = recipe.to_dict()
    assert dumped["schema_version"] == 2
    assert "matcher" in dumped
    assert "safety" in dumped
    assert "health" in dumped


# 10. CLI Recipe Run with --allow-irreversible & observe integration
def test_cli_recipe_run_and_observe_integration(tmp_path, ephemeral_cdp_url, fixture_server):
    recipes_dir = tmp_path / "recipes"
    store = RecipeStore(root=recipes_dir)

    server_domain = "127.0.0.1"
    # Create R4 recipe
    r4_recipe = Recipe(
        id="r4-delete-cli",
        name="Delete Database CLI",
        domain_pattern=server_domain,
        steps=[RecipeStep(action="click", target={"name": "Delete database record"})],
        safety=SafetySpec(max_risk=RiskClass.R4_IRREVERSIBLE),
    )
    store.save(r4_recipe, domain=server_domain)

    # Observe integration
    suggestions = suggest_for_url(
        f"{fixture_server}/interactive_page.html",
        recipe_store=store,
        memory_root=tmp_path / "memory",
    )
    assert len(suggestions) >= 1
    match = next(s for s in suggestions if s["recipe_id"] == "r4-delete-cli")
    assert match["risk_class"] == RiskClass.R4_IRREVERSIBLE
    assert "--allow-irreversible" in match["command"]
    assert "--allow-irreversible" in match["argv"]
