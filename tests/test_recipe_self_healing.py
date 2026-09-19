"""
Tests for Milestone v2.6: Self-Healing Anchor Bundles, Deterministic Local Repair & Alternate Transition Discovery.
Verifies multi-candidate anchor bundles, ambiguity disqualification, zero online canonical mutation,
offline repair candidate logging and CAS application, and STG alternate transition detours.
"""

import json
from pathlib import Path
import tempfile
import pytest

from browser_core.contracts import (
    AnchorAmbiguous,
    AnchorBundle,
    AnchorCandidate,
    AnchorNotFound,
    ExecutionOutcome,
    RecipeLifecycleState,
    RepairCandidate,
    RiskClass,
    SemanticStateNode,
    TransitionEdge,
    WorkflowPlan,
)
from browser_core.page_manager import PageManager
from browser_core.recipes import (
    AnchorResolver,
    Recipe,
    RecipeEngine,
    RecipeStep,
    RecipeStore,
)
from browser_core.workflows import (
    StateTransitionGraph,
    WorkflowEngine,
)


def _session(ephemeral_cdp_url, fixture_server):
    manager = PageManager(ephemeral_cdp_url, test_mode=True)
    manager.connect()
    page = manager.primary_page()
    manager.install_scanner(page)
    page.goto(f"{fixture_server}/interactive_page.html", wait_until="domcontentloaded")
    return manager, page


# 1. Primary Candidate Success
def test_anchor_bundle_primary_success(ephemeral_cdp_url, fixture_server, tmp_path):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        page.set_content("""
            <div id="container">
                <button data-testid="save-btn" class="btn">Save Changes</button>
                <div id="output">idle</div>
            </div>
            <script>
                document.querySelector('[data-testid="save-btn"]').onclick = () => {
                    document.getElementById('output').textContent = 'Saved!';
                };
            </script>
        """)

        bundle = AnchorBundle(
            candidates=[
                AnchorCandidate(kind="test_attr", selector='[data-testid="save-btn"]', score=0.95),
                AnchorCandidate(kind="role_name", role="button", name="Save Changes", score=0.90),
            ]
        )

        store = RecipeStore(root=tmp_path / "recipes")
        engine = RecipeEngine(store=store)

        recipe = Recipe(
            id="test-primary-success",
            name="Primary Success Recipe",
            domain_pattern="*",
            steps=[
                RecipeStep(action="click", target=bundle),
            ],
        )

        res = engine.execute(recipe, page, manager=manager)
        assert res.ok is True
        assert page.locator("#output").inner_text() == "Saved!"

        # No repair candidate should be recorded because candidate[0] succeeded
        repairs = store.list_repair_candidates(recipe.id)
        assert len(repairs) == 0
    finally:
        manager.close()


# 2. Heals Drift via Secondary Candidate & Records RepairCandidate
def test_anchor_bundle_heals_drift_via_secondary_candidate(ephemeral_cdp_url, fixture_server, tmp_path):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        # UI Drift: data-testid is gone, but role+name button exists
        page.set_content("""
            <div id="container">
                <button class="styled-btn">Submit Order</button>
                <div id="status">waiting</div>
            </div>
            <script>
                document.querySelector('button').onclick = () => {
                    document.getElementById('status').textContent = 'Order Submitted!';
                };
            </script>
        """)

        bundle = AnchorBundle(
            candidates=[
                AnchorCandidate(kind="test_attr", selector='[data-testid="broken-submit-id"]', score=0.96),
                AnchorCandidate(kind="role_name", role="button", name="Submit Order", score=0.91),
                AnchorCandidate(kind="scoped_css", selector="button.styled-btn", score=0.80),
            ]
        )

        store = RecipeStore(root=tmp_path / "recipes")
        engine = RecipeEngine(store=store)

        recipe = Recipe(
            id="test-heals-drift",
            name="Heals Drift Recipe",
            domain_pattern="*",
            steps=[
                RecipeStep(action="click", target=bundle),
            ],
        )

        res = engine.execute(recipe, page, manager=manager)
        assert res.ok is True
        assert page.locator("#status").inner_text() == "Order Submitted!"

        # Exactly one RepairCandidate should be recorded into .repairs.jsonl
        repairs = store.list_repair_candidates(recipe.id)
        assert len(repairs) == 1
        rep = repairs[0]
        assert rep.recipe_id == recipe.id
        assert rep.step_index == 0
        assert rep.broken_candidate["selector"] == '[data-testid="broken-submit-id"]'
        assert rep.healed_candidate["kind"] == "role_name"
        assert rep.healed_candidate["name"] == "Submit Order"
        assert rep.confidence == 0.91
    finally:
        manager.close()


# 3. Candidate Bundle Ambiguity Safety (Never click .first() on ambiguous candidates)
def test_anchor_ambiguity_disqualified(ephemeral_cdp_url, fixture_server, tmp_path):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        # data-testid has multiple elements (ambiguous), but scoped_css #unique-btn is unique
        page.set_content("""
            <div id="container">
                <button data-testid="duplicate-action" class="btn">Duplicate 1</button>
                <button data-testid="duplicate-action" class="btn">Duplicate 2</button>
                <button id="unique-btn" class="btn">Unique Button</button>
                <div id="target-output">idle</div>
            </div>
            <script>
                document.getElementById('unique-btn').onclick = () => {
                    document.getElementById('target-output').textContent = 'Unique Clicked!';
                };
            </script>
        """)

        bundle = AnchorBundle(
            candidates=[
                AnchorCandidate(kind="test_attr", selector='[data-testid="duplicate-action"]', score=0.95),
                AnchorCandidate(kind="scoped_css", selector='#unique-btn', score=0.85),
            ]
        )

        store = RecipeStore(root=tmp_path / "recipes")
        engine = RecipeEngine(store=store)

        recipe = Recipe(
            id="test-ambiguity-disqualified",
            name="Ambiguity Disqualified Recipe",
            domain_pattern="*",
            steps=[
                RecipeStep(action="click", target=bundle),
            ],
        )

        res = engine.execute(recipe, page, manager=manager)
        assert res.ok is True
        assert page.locator("#target-output").inner_text() == "Unique Clicked!"

        # RepairCandidate logged from ambiguous candidate[0] to unambiguous candidate[1]
        repairs = store.list_repair_candidates(recipe.id)
        assert len(repairs) == 1
        assert repairs[0].broken_candidate["selector"] == '[data-testid="duplicate-action"]'
        assert repairs[0].healed_candidate["selector"] == '#unique-btn'
    finally:
        manager.close()


# 4. Zero Online Canonical Mutation Invariant
def test_zero_online_canonical_mutation(ephemeral_cdp_url, fixture_server, tmp_path):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        page.set_content("""
            <div id="container">
                <button class="active-button">Confirm</button>
                <div id="log">init</div>
            </div>
            <script>
                document.querySelector('.active-button').onclick = () => {
                    document.getElementById('log').textContent = 'Done';
                };
            </script>
        """)

        store = RecipeStore(root=tmp_path / "recipes")
        bundle = AnchorBundle(
            candidates=[
                AnchorCandidate(kind="test_attr", selector='[data-testid="missing-id"]', score=0.95),
                AnchorCandidate(kind="role_name", role="button", name="Confirm", score=0.91),
            ]
        )

        recipe = Recipe(
            id="test-zero-mutation",
            name="Zero Mutation Recipe",
            domain_pattern="*",
            steps=[
                RecipeStep(action="click", target=bundle),
            ],
        )

        saved_path = store.save(recipe)
        initial_file_content = saved_path.read_text(encoding="utf-8")
        initial_rev = store.get(recipe.id).lifecycle.revision
        initial_gen = store.get(recipe.id).lifecycle.generation
        initial_digest = store.get(recipe.id).lifecycle.content_digest

        engine = RecipeEngine(store=store)
        res = engine.execute(recipe.id, page, manager=manager)
        assert res.ok is True
        assert page.locator("#log").inner_text() == "Done"

        # Verification of Zero Online Canonical Mutation:
        after_file_content = saved_path.read_text(encoding="utf-8")
        assert after_file_content == initial_file_content, "Canonical recipe file on disk was modified online!"

        disk_recipe = store.get(recipe.id)
        assert disk_recipe.lifecycle.revision == initial_rev
        assert disk_recipe.lifecycle.generation == initial_gen
        assert disk_recipe.lifecycle.content_digest == initial_digest

        # The primary candidate in the canonical recipe remains the original candidate
        assert disk_recipe.steps[0].target.candidates[0].selector == '[data-testid="missing-id"]'

        # Repair candidate exists in .repairs.jsonl
        repairs = store.list_repair_candidates(recipe.id)
        assert len(repairs) == 1
    finally:
        manager.close()


# 5. Offline Repair Application & Generation Bound Evidence Reset
def test_offline_repair_application(tmp_path):
    store = RecipeStore(root=tmp_path / "recipes")

    bundle = AnchorBundle(
        candidates=[
            AnchorCandidate(kind="test_attr", selector='[data-testid="stale-selector"]', score=0.95),
            AnchorCandidate(kind="role_name", role="button", name="New Button", score=0.91),
        ]
    )

    recipe = Recipe(
        id="test-offline-repair",
        name="Offline Repair Recipe",
        domain_pattern="*",
        steps=[
            RecipeStep(action="click", target=bundle),
        ],
    )
    store.save(recipe)

    # Simulate recorded historical evidence in production
    recipe.lifecycle.state = RecipeLifecycleState.VERIFIED_SHARED
    recipe.lifecycle.sessions_seen = ["sess_alpha", "sess_beta", "sess_gamma"]
    recipe.lifecycle.agents_seen = ["agent_1", "agent_2"]
    recipe.lifecycle.consecutive_failures = 1
    store.save(recipe)

    initial_gen = recipe.lifecycle.generation

    # Record a repair candidate
    repair = RepairCandidate(
        recipe_id=recipe.id,
        step_index=0,
        broken_candidate={"kind": "test_attr", "selector": '[data-testid="stale-selector"]', "score": 0.95},
        healed_candidate={"kind": "role_name", "role": "button", "name": "New Button", "score": 0.91},
        confidence=0.91,
    )
    store.record_repair_candidate(repair)

    # Verify list_repair_candidates
    candidates = store.list_repair_candidates(recipe.id)
    assert len(candidates) == 1
    assert candidates[0].id == repair.id

    # Apply repair candidate
    updated = store.apply_repair_candidate(repair.id, actor="offline_learner", reason="self_healing_promotion")

    # Invariants on repair promotion:
    # 1. Generation incremented
    assert updated.lifecycle.generation == initial_gen + 1
    # 2. Healed candidate is now candidates[0]
    assert updated.steps[0].target.candidates[0].name == "New Button"
    assert updated.steps[0].target.candidates[0].role == "button"
    # 3. Evidence reset to VERIFIED_LOCAL
    assert updated.lifecycle.state == RecipeLifecycleState.VERIFIED_LOCAL
    assert updated.lifecycle.sessions_seen == []
    assert updated.lifecycle.agents_seen == []
    assert updated.lifecycle.consecutive_failures == 0
    # 4. Audit log entry recorded
    assert len(updated.lifecycle.audit_log) > 0
    assert updated.lifecycle.audit_log[-1]["action"] == "apply_repair"
    assert updated.lifecycle.audit_log[-1]["repair_id"] == repair.id


# 6. STG Alternate Transition Detour Discovery & Execution
def test_workflow_stg_alternate_detour(ephemeral_cdp_url, fixture_server, tmp_path):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        # Page has standard checkout path available, but express checkout button is missing
        page.set_content("""
            <div id="app">
                <button id="cart-to-standard">Go To Standard Checkout</button>
                <div id="step-output">Cart</div>
            </div>
            <script>
                document.getElementById('cart-to-standard').onclick = () => {
                    document.getElementById('step-output').textContent = 'Standard Checkout';
                    document.getElementById('app').innerHTML = `
                        <button id="complete-order">Complete Order</button>
                        <div id="step-output">Standard Checkout</div>
                    `;
                    document.getElementById('complete-order').onclick = () => {
                        document.getElementById('step-output').textContent = 'Order Confirmed';
                    };
                };
            </script>
        """)

        store = RecipeStore(root=tmp_path / "recipes")

        # Recipe 1: Direct path (broken on page: #direct-route-btn is missing)
        r_express = Recipe(
            id="route-direct",
            name="Route Direct",
            domain_pattern="*",
            steps=[RecipeStep(action="click", target="#direct-route-btn", risk_class=RiskClass.R2_LOCAL_MUTABLE)],
        )
        store.save(r_express)

        # Recipe 2: Detour Step 1 (Cart -> Standard)
        r_detour1 = Recipe(
            id="detour-step-1",
            name="Detour Step 1",
            domain_pattern="*",
            steps=[RecipeStep(action="click", target="#cart-to-standard", risk_class=RiskClass.R2_LOCAL_MUTABLE)],
        )
        store.save(r_detour1)

        # Recipe 3: Detour Step 2 (Standard -> Confirmation)
        r_detour2 = Recipe(
            id="detour-step-2",
            name="Detour Step 2",
            domain_pattern="*",
            steps=[RecipeStep(action="click", target="#complete-order", risk_class=RiskClass.R2_LOCAL_MUTABLE)],
        )
        store.save(r_detour2)

        graph = StateTransitionGraph()
        graph.add_state(SemanticStateNode(state_id="shop:cart", domain="example.com"))
        graph.add_state(SemanticStateNode(state_id="shop:standard", domain="example.com"))
        graph.add_state(SemanticStateNode(state_id="shop:confirmation", domain="example.com"))

        # Direct edge that will fail
        graph.add_edge(TransitionEdge(
            edge_id="edge-direct-route",
            from_state="shop:cart",
            to_state="shop:confirmation",
            recipe_id="route-direct",
            risk_class=RiskClass.R2_LOCAL_MUTABLE,
            cost=1.0,
        ))

        # Alternate detour route
        graph.add_edge(TransitionEdge(
            edge_id="edge-detour-1",
            from_state="shop:cart",
            to_state="shop:standard",
            recipe_id="detour-step-1",
            risk_class=RiskClass.R2_LOCAL_MUTABLE,
            cost=1.5,
        ))
        graph.add_edge(TransitionEdge(
            edge_id="edge-detour-2",
            from_state="shop:standard",
            to_state="shop:confirmation",
            recipe_id="detour-step-2",
            risk_class=RiskClass.R2_LOCAL_MUTABLE,
            cost=1.5,
        ))

        # Initial workflow plan calls the direct edge
        plan = WorkflowPlan(
            plan_id="plan-checkout-route",
            start_state="shop:cart",
            goal_state="shop:confirmation",
            edges=[graph.get_outgoing_edges("shop:cart")[0]],
            cumulative_risk=RiskClass.R2_LOCAL_MUTABLE,
            estimated_steps=1,
        )

        engine = WorkflowEngine(store=store, graph=graph)
        result = engine.execute(plan, page, manager=manager, allow_detour=True)

        assert result.ok is True
        assert result.detour_taken is True
        assert result.current_state == "shop:confirmation"
        assert page.locator("#step-output").inner_text() == "Order Confirmed"

        # Edge results show both the failed attempt and the successful detour edges
        assert len(result.edge_results) >= 2
        detour_edges = [e for e in result.edge_results if e.get("detour")]
        assert len(detour_edges) == 2
        assert detour_edges[0]["edge_id"] == "edge-detour-1"
        assert detour_edges[1]["edge_id"] == "edge-detour-2"
    finally:
        manager.close()


# 7. AnchorResolver Ambiguity Fall-Through & Failure Edge Cases
def test_anchor_resolver_edge_cases(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        page.set_content("""
            <div id="panel">
                <input aria-label="Username" id="user-in" />
                <span class="label-text">Exact Label</span>
            </div>
        """)

        # Test label_input resolution
        bundle_label = AnchorBundle(
            candidates=[
                AnchorCandidate(kind="label_input", name="Username", score=0.91),
            ]
        )
        loc, cand, score = AnchorResolver.resolve(page, bundle_label)
        assert loc.count() == 1
        assert cand.name == "Username"

        # Test text resolution
        bundle_text = AnchorBundle(
            candidates=[
                AnchorCandidate(kind="text", name="Exact Label", score=0.80),
            ]
        )
        loc, cand, score = AnchorResolver.resolve(page, bundle_text)
        assert loc.count() == 1
        assert cand.kind == "text"

        # Test all candidates fail raises AnchorNotFound
        bundle_empty = AnchorBundle(
            candidates=[
                AnchorCandidate(kind="test_attr", selector="#non-existent-1", score=0.95),
                AnchorCandidate(kind="role_name", role="button", name="Non-Existent", score=0.90),
            ]
        )
        with pytest.raises(AnchorNotFound):
            AnchorResolver.resolve(page, bundle_empty)
    finally:
        manager.close()
