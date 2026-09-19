"""
tests/test_recipe_governance.py
Tests for Task-011: Cross-Agent Promotion, Quarantine & Shared Cache Governance (Milestone v2.5.x).
Verifies evidence-based promotion lifecycle, automatic quarantine triggers,
CAS optimistic concurrency, U-score eviction, and CLI governance commands.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import MagicMock
import pytest

from browser_core.contracts import (
    CASConflictError,
    ExecutionOutcome,
    HealthStats,
    LifecycleRecord,
    PromotionPolicy,
    QuarantinedRecipeError,
    RecipeLifecycleState,
    RiskClass,
    SemanticStateNode,
    TransitionEdge,
    WorkflowPlan,
)
from browser_core.page_manager import PageManager
from browser_core.recipes import (
    Recipe,
    RecipeEngine,
    RecipeStep,
    RecipeStore,
    calculate_utility_score,
    check_quarantine_triggers,
    compute_recipe_content_digest,
    evaluate_promotion,
    suggest_for_url,
)
from browser_core.workflows import StateTransitionGraph, WorkflowComposer, WorkflowEngine


def _session(ephemeral_cdp_url, fixture_server):
    manager = PageManager(ephemeral_cdp_url, test_mode=True)
    manager.connect()
    page = manager.primary_page()
    manager.install_scanner(page)
    page.goto(f"{fixture_server}/interactive_page.html", wait_until="domcontentloaded")
    return manager, page


# 1. LifecycleRecord and PromotionPolicy Serialization
def test_lifecycle_record_and_policy_serialization():
    # Test LifecycleRecord defaults and round-trip
    lc = LifecycleRecord(
        state=RecipeLifecycleState.VERIFIED_LOCAL,
        revision=3,
        generation=1,
        sessions_seen=["sess_1", "sess_2"],
        agents_seen=["agent_a"],
        consecutive_failures=0,
        quarantine_reason=None,
        quarantined_at=None,
        utility_score=2.45,
        content_digest="sha256_dummy_digest",
        audit_log=[{"actor": "admin", "reason": "initial", "from_state": "DRAFT", "to_state": "VERIFIED_LOCAL"}],
    )
    d_lc = lc.to_dict()
    assert d_lc["state"] == "VERIFIED_LOCAL"
    assert d_lc["revision"] == 3
    assert d_lc["sessions_seen"] == ["sess_1", "sess_2"]
    assert d_lc["utility_score"] == 2.45
    assert d_lc["content_digest"] == "sha256_dummy_digest"
    assert len(d_lc["audit_log"]) == 1

    reconstructed_lc = LifecycleRecord.from_dict(d_lc)
    assert reconstructed_lc.state == RecipeLifecycleState.VERIFIED_LOCAL
    assert reconstructed_lc.revision == 3
    assert reconstructed_lc.sessions_seen == ["sess_1", "sess_2"]
    assert reconstructed_lc.content_digest == "sha256_dummy_digest"
    assert len(reconstructed_lc.audit_log) == 1
    assert reconstructed_lc.audit_log[0]["actor"] == "admin"

    # Test PromotionPolicy defaults and round-trip
    pol = PromotionPolicy()
    assert pol.min_successes_local == 2
    assert pol.min_successes_shared == 5
    assert pol.min_independent_sessions == 3
    assert pol.min_executions_curated == 20
    assert pol.min_independent_agents == 3
    assert pol.curated_success_rate == 0.95

    d_pol = pol.to_dict()
    reconstructed_pol = PromotionPolicy.from_dict(d_pol)
    assert reconstructed_pol.min_successes_local == 2
    assert reconstructed_pol.curated_success_rate == 0.95

    # Test Recipe serialization with lifecycle
    recipe = Recipe(
        id="test-lifecycle-recipe",
        name="Test Lifecycle Serialization",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target="#btn")],
        lifecycle=lc,
    )
    d_recipe = recipe.to_dict()
    assert "lifecycle" in d_recipe
    assert d_recipe["lifecycle"]["state"] == "VERIFIED_LOCAL"
    assert d_recipe["lifecycle"]["revision"] == 3

    from_dict_recipe = Recipe.from_dict(d_recipe)
    assert from_dict_recipe.lifecycle.state == RecipeLifecycleState.VERIFIED_LOCAL
    assert from_dict_recipe.lifecycle.revision == 3

    json_str = recipe.to_json()
    from_json_recipe = Recipe.from_json(json_str)
    assert from_json_recipe.lifecycle.state == RecipeLifecycleState.VERIFIED_LOCAL
    assert from_json_recipe.lifecycle.sessions_seen == ["sess_1", "sess_2"]


# 2. Evidence-Based Promotion: DRAFT -> VERIFIED_LOCAL -> VERIFIED_SHARED -> CURATED
def test_evidence_based_promotion_draft_to_local_to_shared_to_curated():
    recipe = Recipe(
        id="promotion-flow",
        name="Promotion Flow",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target="#submit")],
        lifecycle=LifecycleRecord(state=RecipeLifecycleState.DRAFT),
        health=HealthStats(executions=0, successes=0, failures=0, health_score=1.0),
    )

    # 1. DRAFT with 1 success -> does not promote
    recipe.health.executions = 1
    recipe.health.successes = 1
    recipe.health.health_score = 1.0
    assert evaluate_promotion(recipe) is None

    # 2. DRAFT with 2 successes -> promotes to VERIFIED_LOCAL
    recipe.health.executions = 2
    recipe.health.successes = 2
    assert evaluate_promotion(recipe) == RecipeLifecycleState.VERIFIED_LOCAL

    recipe.lifecycle.state = RecipeLifecycleState.VERIFIED_LOCAL

    # 3. VERIFIED_LOCAL with 5 successes in 1 session -> does NOT promote to VERIFIED_SHARED
    recipe.health.executions = 5
    recipe.health.successes = 5
    recipe.lifecycle.sessions_seen = ["sess-1"]
    assert evaluate_promotion(recipe) is None

    # 4. VERIFIED_LOCAL with 5 successes across 2 sessions -> does NOT promote
    recipe.lifecycle.sessions_seen = ["sess-1", "sess-2"]
    assert evaluate_promotion(recipe) is None

    # 5. VERIFIED_LOCAL with 5 successes across 3 sessions, but 1 structural failure -> does NOT promote
    recipe.lifecycle.sessions_seen = ["sess-1", "sess-2", "sess-3"]
    recipe.health.failures = 1
    assert evaluate_promotion(recipe) is None

    # 6. VERIFIED_LOCAL with 5 successes across 3 sessions with 0 failures -> promotes to VERIFIED_SHARED
    recipe.health.failures = 0
    assert evaluate_promotion(recipe) == RecipeLifecycleState.VERIFIED_SHARED

    recipe.lifecycle.state = RecipeLifecycleState.VERIFIED_SHARED

    # 7. VERIFIED_SHARED with 20 executions across 2 agents -> does NOT promote to CURATED
    recipe.health.executions = 20
    recipe.health.successes = 20
    recipe.lifecycle.agents_seen = ["agent-1", "agent-2"]
    assert evaluate_promotion(recipe) is None

    # 8. VERIFIED_SHARED with 20 executions across 3 agents, but success rate < 0.95 -> does NOT promote
    recipe.lifecycle.agents_seen = ["agent-1", "agent-2", "agent-3"]
    recipe.health.successes = 18
    recipe.health.failures = 2
    recipe.health.health_score = 0.90
    assert evaluate_promotion(recipe) is None

    # 9. VERIFIED_SHARED with 20 executions across 3 agents and success rate >= 0.95 -> promotes to CURATED
    recipe.health.successes = 20
    recipe.health.failures = 0
    recipe.health.health_score = 1.0
    assert evaluate_promotion(recipe) == RecipeLifecycleState.CURATED


# 3. Quarantine Trigger: >= 3 Consecutive Failures
def test_quarantine_trigger_on_three_consecutive_failures(tmp_path):
    store = RecipeStore(root=tmp_path / "recipes")
    recipe = Recipe(
        id="consecutive-failures-recipe",
        name="Consecutive Failures Recipe",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target="#action")],
        lifecycle=LifecycleRecord(state=RecipeLifecycleState.VERIFIED_LOCAL),
        health=HealthStats(executions=10, successes=10, failures=0, health_score=1.0),
    )
    store.save(recipe, domain="example.com")

    # Failure 1
    store.record_failure(recipe, step_index=0, reason="timeout", target="#action")
    assert recipe.lifecycle.consecutive_failures == 1
    assert recipe.lifecycle.state == RecipeLifecycleState.VERIFIED_LOCAL

    # Success resets consecutive failures
    store.record_success(recipe)
    assert recipe.lifecycle.consecutive_failures == 0
    assert recipe.lifecycle.state == RecipeLifecycleState.VERIFIED_LOCAL

    # 3 consecutive failures
    store.record_failure(recipe, step_index=0, reason="timeout", target="#action")
    assert recipe.lifecycle.consecutive_failures == 1
    assert recipe.lifecycle.state == RecipeLifecycleState.VERIFIED_LOCAL

    store.record_failure(recipe, step_index=0, reason="timeout", target="#action")
    assert recipe.lifecycle.consecutive_failures == 2
    assert recipe.lifecycle.state == RecipeLifecycleState.VERIFIED_LOCAL

    store.record_failure(recipe, step_index=0, reason="timeout", target="#action")
    assert recipe.lifecycle.consecutive_failures == 3
    # Automatically quarantined!
    assert recipe.lifecycle.state == RecipeLifecycleState.QUARANTINED
    assert "consecutive_failures_exceeded" in str(recipe.lifecycle.quarantine_reason)
    assert recipe.lifecycle.quarantined_at is not None


# 4. Quarantine Trigger: UNKNOWN_SIDE_EFFECT on Persistent Mutation (R3/R4)
def test_quarantine_trigger_on_unknown_side_effect_persistent_mutation(tmp_path):
    store = RecipeStore(root=tmp_path / "recipes")
    recipe = Recipe(
        id="mutation-side-effect-recipe",
        name="Mutation Side Effect Recipe",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target="#submit-payment", risk_class=RiskClass.R3_PERSISTENT_MUTATION)],
        lifecycle=LifecycleRecord(state=RecipeLifecycleState.VERIFIED_LOCAL),
        health=HealthStats(executions=0, successes=0, failures=0, health_score=1.0),
    )
    store.save(recipe, domain="example.com")

    # A single UNKNOWN_SIDE_EFFECT on R3 triggers immediate quarantine
    store.record_failure(
        recipe,
        step_index=0,
        reason="network dropped during write",
        target="#submit-payment",
        outcome=ExecutionOutcome.UNKNOWN_SIDE_EFFECT,
    )
    assert recipe.lifecycle.state == RecipeLifecycleState.QUARANTINED
    assert "unknown_side_effect_on_persistent_mutation" in str(recipe.lifecycle.quarantine_reason)


# 5. Quarantine Trigger: Health Score Degradation (< 0.60)
def test_quarantine_trigger_on_health_score_degradation(tmp_path):
    store = RecipeStore(root=tmp_path / "recipes")
    recipe = Recipe(
        id="health-degraded-recipe",
        name="Health Degraded Recipe",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target="#nav", risk_class=RiskClass.R1_REVERSIBLE_NAV)],
        lifecycle=LifecycleRecord(state=RecipeLifecycleState.VERIFIED_LOCAL),
        health=HealthStats(executions=0, successes=0, failures=0, health_score=1.0),
    )
    store.save(recipe, domain="example.com")

    # Record 1 success, 1 failure, 1 success (executions=3, successes=2, failures=1 -> health=0.667)
    store.record_success(recipe)
    store.record_failure(recipe, step_index=0, reason="nav error", target="#nav")
    store.record_success(recipe)
    assert recipe.lifecycle.state == RecipeLifecycleState.VERIFIED_LOCAL

    # Record 2 more failures (executions=5, successes=2, failures=3 -> health=0.400 < 0.60)
    store.record_failure(recipe, step_index=0, reason="nav error", target="#nav")
    # consecutive_failures is now 1. Not >= 3 yet.
    assert recipe.lifecycle.consecutive_failures == 1
    # But health_score dropped to 2/4 = 0.50 < 0.60 over 4 executions!
    assert recipe.lifecycle.state == RecipeLifecycleState.QUARANTINED
    assert "health_score_degraded" in str(recipe.lifecycle.quarantine_reason)


# 6. Quarantined Recipe Excluded from Suggestions
def test_quarantined_recipe_excluded_from_suggestions(tmp_path):
    store = RecipeStore(root=tmp_path / "recipes")
    active_recipe = Recipe(
        id="active-recipe",
        name="Active Recipe",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target="#btn")],
        lifecycle=LifecycleRecord(state=RecipeLifecycleState.VERIFIED_LOCAL),
    )
    quarantined_recipe = Recipe(
        id="quarantined-recipe",
        name="Quarantined Recipe",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target="#broken")],
        lifecycle=LifecycleRecord(state=RecipeLifecycleState.QUARANTINED, quarantine_reason="broken"),
    )
    archived_recipe = Recipe(
        id="archived-recipe",
        name="Archived Recipe",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target="#old")],
        lifecycle=LifecycleRecord(state=RecipeLifecycleState.ARCHIVED),
    )
    store.save(active_recipe, domain="example.com")
    store.save(quarantined_recipe, domain="example.com")
    store.save(archived_recipe, domain="example.com")

    suggestions = suggest_for_url("https://example.com/page", recipe_store=store)
    suggested_ids = [s["recipe_id"] for s in suggestions]

    assert "active-recipe" in suggested_ids
    assert "quarantined-recipe" not in suggested_ids
    assert "archived-recipe" not in suggested_ids

    active_sug = next(s for s in suggestions if s["recipe_id"] == "active-recipe")
    assert active_sug["lifecycle_state"] == "VERIFIED_LOCAL"


# 7. Quarantined Recipe Excluded from StateTransitionGraph
def test_quarantined_recipe_excluded_from_state_transition_graph():
    graph = StateTransitionGraph()

    active_recipe = Recipe(
        id="r-active",
        name="Active Step",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target="#btn")],
        lifecycle=LifecycleRecord(state=RecipeLifecycleState.VERIFIED_LOCAL),
    )
    quarantined_recipe = Recipe(
        id="r-quarantined",
        name="Quarantined Step",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target="#btn2")],
        lifecycle=LifecycleRecord(state=RecipeLifecycleState.QUARANTINED),
    )
    suspect_recipe = Recipe(
        id="r-suspect",
        name="Suspect Step",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target="#btn3")],
        lifecycle=LifecycleRecord(state=RecipeLifecycleState.SUSPECT),
    )
    archived_recipe = Recipe(
        id="r-archived",
        name="Archived Step",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target="#btn4")],
        lifecycle=LifecycleRecord(state=RecipeLifecycleState.ARCHIVED),
    )

    graph.ingest_recipe(active_recipe)
    graph.ingest_recipe(quarantined_recipe)
    graph.ingest_recipe(suspect_recipe)
    graph.ingest_recipe(archived_recipe)

    # Collect all ingested edge recipe IDs
    ingested_recipes = {edge.recipe_id for elist in graph.edges.values() for edge in elist}

    assert "r-active" in ingested_recipes
    assert "r-quarantined" not in ingested_recipes
    assert "r-suspect" not in ingested_recipes
    assert "r-archived" not in ingested_recipes


# 8. Quarantined Recipe Execution Raises QuarantinedRecipeError
def test_quarantined_recipe_execution_raises_quarantined_error(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        engine = RecipeEngine()
        quarantined = Recipe(
            id="quarantined-exec-test",
            name="Quarantined Execution Test",
            domain_pattern="127.0.0.1",
            steps=[RecipeStep(action="click", target="#save")],
            lifecycle=LifecycleRecord(
                state=RecipeLifecycleState.QUARANTINED,
                quarantine_reason="structural failure on mutation",
            ),
        )

        with pytest.raises(QuarantinedRecipeError) as exc_info:
            engine.execute(quarantined, page, manager=manager)

        assert "QUARANTINED" in str(exc_info.value)
        assert "structural failure on mutation" in str(exc_info.value)
    finally:
        manager.close()


# 9. Recipe Restore Re-Enables Execution
def test_recipe_restore_re_enables_execution(tmp_path, ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        store = RecipeStore(root=tmp_path / "recipes")
        recipe = Recipe(
            id="restore-exec-test",
            name="Restore Execution Test",
            domain_pattern="127.0.0.1",
            steps=[RecipeStep(action="click", target="#save")],
            lifecycle=LifecycleRecord(state=RecipeLifecycleState.VERIFIED_LOCAL),
        )
        store.save(recipe, domain="127.0.0.1")

        # Quarantine it
        store.quarantine(recipe.id, reason="testing quarantine")
        assert store.get(recipe.id).lifecycle.state == RecipeLifecycleState.QUARANTINED

        engine = RecipeEngine(store=store)
        with pytest.raises(QuarantinedRecipeError):
            engine.execute(recipe.id, page, manager=manager)

        # Restore it
        restored = store.restore(recipe.id)
        assert restored.lifecycle.state == RecipeLifecycleState.VERIFIED_LOCAL
        assert restored.lifecycle.consecutive_failures == 0
        assert restored.lifecycle.quarantine_reason is None

        # Execution now succeeds!
        result = engine.execute(recipe.id, page, manager=manager)
        assert result.ok is True
        assert result.completed_steps == 1
    finally:
        manager.close()


# 10. CAS Optimistic Concurrency Conflict Prevention
def test_cas_optimistic_concurrency_conflict_prevention(tmp_path):
    store = RecipeStore(root=tmp_path / "recipes")
    recipe = Recipe(
        id="cas-recipe",
        name="CAS Recipe",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target="#btn")],
        lifecycle=LifecycleRecord(state=RecipeLifecycleState.VERIFIED_LOCAL, revision=1),
    )
    # Initial save: revision becomes 2
    store.save(recipe, domain="example.com")
    assert recipe.lifecycle.revision == 2

    # Client A and Client B both hold revision 2
    client_a_recipe = Recipe.from_dict(recipe.to_dict())
    client_b_recipe = Recipe.from_dict(recipe.to_dict())

    # Client A updates with expected_revision=2 -> succeeds! Revision becomes 3
    client_a_recipe.description = "Updated by Client A"
    store.save(client_a_recipe, domain="example.com", expected_revision=2)
    assert client_a_recipe.lifecycle.revision == 3

    # Client B tries to save with expected_revision=2 -> CAS conflict!
    client_b_recipe.description = "Updated by Client B"
    with pytest.raises(CASConflictError) as exc_info:
        store.save(client_b_recipe, domain="example.com", expected_revision=2)
    assert "CAS conflict" in str(exc_info.value)

    # Client B fetches latest (revision 3) and retries with expected_revision=3 -> succeeds!
    fresh_recipe = store.get("cas-recipe")
    assert fresh_recipe.lifecycle.revision == 3
    fresh_recipe.description = "Updated by Client B after re-fetch"
    store.save(fresh_recipe, domain="example.com", expected_revision=3)
    assert fresh_recipe.lifecycle.revision == 4


# 11. Utility-Score Calculation and Eviction
def test_utility_score_calculation_and_eviction(tmp_path):
    store = RecipeStore(root=tmp_path / "recipes")

    # High-utility recipe
    active_recipe = Recipe(
        id="high-utility",
        name="High Utility Recipe",
        domain_pattern="example.com",
        steps=[
            RecipeStep(action="click", target="#btn1"),
            RecipeStep(action="click", target="#btn2"),
        ],
        health=HealthStats(executions=50, successes=48, failures=2, health_score=0.96),
        metadata={"created_at": time.time(), "last_executed_at": time.time()},
    )
    u_high = calculate_utility_score(active_recipe)
    assert u_high > 1.0

    # Degraded/stale low-utility recipe
    low_utility_recipe = Recipe(
        id="low-utility-stale",
        name="Low Utility Stale Recipe",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target="#old")],
        health=HealthStats(executions=10, successes=1, failures=9, health_score=0.10),
        metadata={"created_at": time.time() - 90 * 86400, "last_executed_at": time.time() - 90 * 86400},
    )
    u_low = calculate_utility_score(low_utility_recipe)
    assert u_low < 0.50

    store.save(active_recipe, domain="example.com")
    store.save(low_utility_recipe, domain="example.com")

    # Evict with archive=True -> low utility recipe marked ARCHIVED
    evicted = store.evict_low_utility(threshold=0.50, archive=True)
    assert "low-utility-stale" in evicted
    assert "high-utility" not in evicted

    stale_loaded = store.get("low-utility-stale")
    assert stale_loaded.lifecycle.state == RecipeLifecycleState.ARCHIVED

    # Evict with archive=False -> low utility recipe deleted permanently
    evicted_del = store.evict_low_utility(threshold=0.50, archive=False)
    assert "low-utility-stale" in evicted_del
    assert store.get("low-utility-stale") is None


# 12. CLI Lifecycle, Quarantine, and Promote Integration
def test_cli_lifecycle_quarantine_and_promote(tmp_path):
    controller = Path(__file__).resolve().parents[1] / "scripts" / "cdp_controller.py"
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir(parents=True, exist_ok=True)

    recipe = Recipe(
        id="cli-gov-recipe",
        name="CLI Governance Recipe",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target="#btn")],
        lifecycle=LifecycleRecord(state=RecipeLifecycleState.DRAFT, revision=1),
    )
    recipe_file = recipes_dir / "cli-gov-recipe.json"
    recipe_file.write_text(recipe.to_json(), encoding="utf-8")

    # 1. recipe lifecycle
    cmd_res = subprocess.run(
        [sys.executable, str(controller), "recipe", "lifecycle", "cli-gov-recipe", "--recipes-dir", str(recipes_dir)],
        capture_output=True, text=True, check=False,
    )
    assert cmd_res.returncode == 0, cmd_res.stderr
    data = json.loads(cmd_res.stdout)
    assert data["recipe_id"] == "cli-gov-recipe"
    assert data["lifecycle"]["state"] == "DRAFT"

    # 2. recipe quarantine
    q_res = subprocess.run(
        [
            sys.executable, str(controller), "recipe", "quarantine", "cli-gov-recipe",
            "--reason", "flaky in CI", "--recipes-dir", str(recipes_dir),
        ],
        capture_output=True, text=True, check=False,
    )
    assert q_res.returncode == 0, q_res.stderr
    q_data = json.loads(q_res.stdout)
    assert q_data["lifecycle"]["state"] == "QUARANTINED"
    assert q_data["lifecycle"]["quarantine_reason"] == "flaky in CI"

    # 3. recipe restore
    r_res = subprocess.run(
        [sys.executable, str(controller), "recipe", "restore", "cli-gov-recipe", "--recipes-dir", str(recipes_dir)],
        capture_output=True, text=True, check=False,
    )
    assert r_res.returncode == 0, r_res.stderr
    r_data = json.loads(r_res.stdout)
    assert r_data["lifecycle"]["state"] == "VERIFIED_LOCAL"

    # 4. recipe promote without privileged override fails
    p_fail = subprocess.run(
        [
            sys.executable, str(controller), "recipe", "promote", "cli-gov-recipe",
            "--target-state", "VERIFIED_SHARED", "--recipes-dir", str(recipes_dir),
        ],
        capture_output=True, text=True, check=False,
    )
    assert p_fail.returncode != 0
    assert "requires policy evidence or explicit privileged=True override" in p_fail.stderr

    # 5. recipe promote with privileged override succeeds
    p_res = subprocess.run(
        [
            sys.executable, str(controller), "recipe", "promote", "cli-gov-recipe",
            "--target-state", "VERIFIED_SHARED", "--privileged", "--actor", "admin", "--reason", "manual verification",
            "--recipes-dir", str(recipes_dir),
        ],
        capture_output=True, text=True, check=False,
    )
    assert p_res.returncode == 0, p_res.stderr
    p_data = json.loads(p_res.stdout)
    assert p_data["lifecycle"]["state"] == "VERIFIED_SHARED"


# 13. Hardening Test: True Concurrent CAS Atomicity
def test_cas_true_multiprocess_atomicity(tmp_path):
    """
    Verify true concurrent compare-and-swap atomicity under lock serialization.
    Two concurrent writers attempt expected_revision=N on the same recipe file.
    Exactly one succeeds, exactly one gets CASConflictError, final revision is N+1.
    """
    recipes_dir = tmp_path / "recipes"
    store = RecipeStore(root=recipes_dir)
    recipe = Recipe(
        id="concurrent-cas-recipe",
        name="Concurrent CAS Recipe",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target="#btn")],
        lifecycle=LifecycleRecord(state=RecipeLifecycleState.VERIFIED_LOCAL, revision=1),
    )
    # Initial save sets revision = 2
    store.save(recipe, domain="example.com")
    assert recipe.lifecycle.revision == 2

    barrier = threading.Barrier(2)
    results = []
    errors = []

    def writer_task(worker_id: int):
        worker_store = RecipeStore(root=recipes_dir)
        rec = worker_store.get("concurrent-cas-recipe")
        rec.description = f"Updated by worker {worker_id}"
        # Synchronize both workers to attempt saving expected_revision=2 simultaneously
        barrier.wait()
        try:
            worker_store.save(rec, domain="example.com", expected_revision=2)
            results.append((worker_id, "SUCCESS"))
        except CASConflictError as err:
            results.append((worker_id, "CONFLICT"))
            errors.append(err)

    t1 = threading.Thread(target=writer_task, args=(1,))
    t2 = threading.Thread(target=writer_task, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    statuses = [r[1] for r in results]
    assert statuses.count("SUCCESS") == 1, f"Expected exactly 1 SUCCESS, got: {statuses}"
    assert statuses.count("CONFLICT") == 1, f"Expected exactly 1 CONFLICT, got: {statuses}"
    assert len(errors) == 1
    assert "CAS conflict" in str(errors[0])

    # Authoritative disk revision must be exactly N + 1 = 2 + 1 = 3
    final_store = RecipeStore(root=recipes_dir)
    final_rec = final_store.get("concurrent-cas-recipe")
    assert final_rec.lifecycle.revision == 3


# 14. Hardening Test: Stale Plan Execution Blocked After Recipe Quarantine
def test_stale_plan_execution_blocked_after_recipe_quarantine(tmp_path):
    """
    Verify that a workflow plan composed while a recipe was healthy is immediately
    blocked from execution if that recipe is subsequently quarantined.
    Execution must raise QuarantinedRecipeError and recipe X dispatch count must be 0.
    """
    recipes_dir = tmp_path / "recipes"
    store = RecipeStore(root=recipes_dir)
    recipe_x = Recipe(
        id="recipe-x",
        name="Recipe X",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target="#btn-x")],
        lifecycle=LifecycleRecord(state=RecipeLifecycleState.VERIFIED_LOCAL),
    )
    store.save(recipe_x, domain="example.com")

    # 1. StateTransitionGraph & WorkflowPlan composition
    graph = StateTransitionGraph()
    node_a = SemanticStateNode(state_id="state_a", domain="example.com")
    node_b = SemanticStateNode(state_id="state_b", domain="example.com")
    graph.add_state(node_a)
    graph.add_state(node_b)
    edge_ab = TransitionEdge(
        edge_id="e_ab",
        from_state="state_a",
        to_state="state_b",
        recipe_id="recipe-x",
        risk_class=RiskClass.R2_LOCAL_MUTABLE,
    )
    graph.add_edge(edge_ab)

    # 2. Compose plan containing recipe X while recipe X is healthy
    composer = WorkflowComposer(graph)
    plan = composer.plan("state_a", "state_b")
    assert plan is not None
    assert len(plan.edges) == 1
    assert plan.edges[0].recipe_id == "recipe-x"

    # 3. Quarantine recipe X in store AFTER plan composition (stale plan scenario)
    store.quarantine("recipe-x", reason="security anomaly detected on recipe-x")
    quarantined = store.get("recipe-x")
    assert quarantined.lifecycle.state == RecipeLifecycleState.QUARANTINED

    # 4. Mock recipe engine to verify dispatch count
    mock_recipe_engine = MagicMock(spec=RecipeEngine)
    engine = WorkflowEngine(store=store, recipe_engine=mock_recipe_engine, graph=graph)

    # 5. Execute old plan -> must be rejected with QuarantinedRecipeError
    with pytest.raises(QuarantinedRecipeError) as exc_info:
        engine.execute(plan, MagicMock(), manager=MagicMock())
    assert "QUARANTINED recipe 'recipe-x'" in str(exc_info.value)

    # 6. Verify recipe X dispatch count is strictly 0
    assert mock_recipe_engine.execute.call_count == 0

    # 7. Also verify direct RecipeEngine execution with stale Recipe snapshot
    direct_engine = RecipeEngine(store=store)
    stale_recipe_snapshot = recipe_x  # In-memory snapshot where state was VERIFIED_LOCAL
    with pytest.raises(QuarantinedRecipeError):
        direct_engine.execute(stale_recipe_snapshot, MagicMock())


# 15. Hardening Test: Generation-Bound Evidence Resets on Mutation
def test_generation_bound_evidence_resets_on_mutation(tmp_path):
    """
    Verify that mutating recipe steps increments generation, clears sessions_seen/agents_seen,
    resets failure counters, and demotes state from CURATED/VERIFIED_SHARED to VERIFIED_LOCAL.
    """
    recipes_dir = tmp_path / "recipes"
    store = RecipeStore(root=recipes_dir)
    recipe = Recipe(
        id="gen-bound-recipe",
        name="Generation Bound Recipe",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target="#step1")],
        lifecycle=LifecycleRecord(
            state=RecipeLifecycleState.CURATED,
            revision=1,
            generation=1,
            sessions_seen=["session_1", "session_2", "session_3"],
            agents_seen=["agent_a", "agent_b", "agent_c"],
            consecutive_failures=0,
        ),
    )
    # Initial save: records content_digest and persists generation=1
    store.save(recipe, domain="example.com")
    assert recipe.lifecycle.generation == 1
    assert recipe.lifecycle.content_digest is not None
    assert recipe.lifecycle.sessions_seen == ["session_1", "session_2", "session_3"]
    assert recipe.lifecycle.agents_seen == ["agent_a", "agent_b", "agent_c"]
    assert recipe.lifecycle.state == RecipeLifecycleState.CURATED
    initial_digest = recipe.lifecycle.content_digest

    # Non-executable metadata mutation -> does not increment generation or reset evidence
    recipe.description = "Updated description only"
    store.save(recipe, domain="example.com")
    assert recipe.lifecycle.generation == 1
    assert recipe.lifecycle.content_digest == initial_digest
    assert len(recipe.lifecycle.sessions_seen) == 3

    # Executable step mutation: append new step
    recipe.steps.append(RecipeStep(action="click", target="#step2"))
    store.save(recipe, domain="example.com")

    # Invariants must be satisfied:
    assert recipe.lifecycle.generation == 2
    assert recipe.lifecycle.content_digest != initial_digest
    assert recipe.lifecycle.sessions_seen == []
    assert recipe.lifecycle.agents_seen == []
    assert recipe.lifecycle.consecutive_failures == 0
    assert recipe.lifecycle.state == RecipeLifecycleState.VERIFIED_LOCAL

    # Fresh load from disk must match
    fresh = store.get("gen-bound-recipe")
    assert fresh.lifecycle.generation == 2
    assert fresh.lifecycle.sessions_seen == []
    assert fresh.lifecycle.agents_seen == []
    assert fresh.lifecycle.state == RecipeLifecycleState.VERIFIED_LOCAL


# 16. Hardening Test: Safe Restore & Privileged Promotion Override
def test_safe_restore_and_privileged_promotion_override(tmp_path):
    """
    Verify:
    1. Unprivileged target_state promotion raises PermissionError without evidence.
    2. Privileged override requires non-empty actor and reason, recording audit log.
    3. Restore resets state strictly to VERIFIED_LOCAL, clearing quarantine fields and failure counts.
    """
    recipes_dir = tmp_path / "recipes"
    store = RecipeStore(root=recipes_dir)
    recipe = Recipe(
        id="safe-gov-recipe",
        name="Safe Governance Recipe",
        domain_pattern="example.com",
        steps=[RecipeStep(action="click", target="#btn")],
        lifecycle=LifecycleRecord(state=RecipeLifecycleState.DRAFT),
    )
    store.save(recipe, domain="example.com")

    # 1. Unprivileged target_state promotion without meeting policy criteria -> raises PermissionError
    with pytest.raises(PermissionError) as exc_perm:
        store.promote("safe-gov-recipe", target_state=RecipeLifecycleState.CURATED, privileged=False)
    assert "requires policy evidence or explicit privileged=True override" in str(exc_perm.value)

    # 2. Privileged promotion without actor or reason -> raises ValueError
    with pytest.raises(ValueError) as exc_no_actor:
        store.promote("safe-gov-recipe", target_state=RecipeLifecycleState.CURATED, privileged=True, actor=None, reason="override")
    assert "Privileged promotion requires non-empty actor and reason" in str(exc_no_actor.value)

    with pytest.raises(ValueError) as exc_no_reason:
        store.promote("safe-gov-recipe", target_state=RecipeLifecycleState.CURATED, privileged=True, actor="admin", reason="   ")
    assert "Privileged promotion requires non-empty actor and reason" in str(exc_no_reason.value)

    # 3. Privileged promotion with actor and reason -> succeeds, records audit log
    promoted = store.promote(
        "safe-gov-recipe",
        target_state=RecipeLifecycleState.CURATED,
        privileged=True,
        actor="sec-architect",
        reason="Manual bypass following security board review",
    )
    assert promoted.lifecycle.state == RecipeLifecycleState.CURATED
    assert len(promoted.lifecycle.audit_log) == 1
    audit_entry = promoted.lifecycle.audit_log[0]
    assert audit_entry["actor"] == "sec-architect"
    assert audit_entry["reason"] == "Manual bypass following security board review"
    assert audit_entry["from_state"] == RecipeLifecycleState.DRAFT
    assert audit_entry["to_state"] == RecipeLifecycleState.CURATED
    assert "timestamp" in audit_entry
    assert "revision" in audit_entry

    # 4. Quarantine the recipe
    quarantined = store.quarantine("safe-gov-recipe", reason="runtime violation")
    assert quarantined.lifecycle.state == RecipeLifecycleState.QUARANTINED
    assert quarantined.lifecycle.quarantine_reason == "runtime violation"

    # 5. Restore resets strictly to VERIFIED_LOCAL (not CURATED!) and clears quarantine fields
    restored = store.restore("safe-gov-recipe")
    assert restored.lifecycle.state == RecipeLifecycleState.VERIFIED_LOCAL
    assert restored.lifecycle.quarantine_reason is None
    assert restored.lifecycle.quarantined_at is None
    assert restored.lifecycle.consecutive_failures == 0

    # Verify on disk
    on_disk = store.get("safe-gov-recipe")
    assert on_disk.lifecycle.state == RecipeLifecycleState.VERIFIED_LOCAL
    assert len(on_disk.lifecycle.audit_log) == 1
