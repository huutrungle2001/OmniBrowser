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
    RiskGateError,
    SemanticStateNode,
    StaleRepairError,
    TransitionEdge,
    UnknownSideEffectError,
    WorkflowInterruptedError,
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
        recipe_generation=recipe.lifecycle.generation,
        recipe_content_digest=recipe.lifecycle.content_digest,
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


# 8. Negative Regression: UNKNOWN_SIDE_EFFECT is strictly terminal (0 detours, 0 subsequent dispatches)
def test_unknown_side_effect_never_detours(ephemeral_cdp_url, fixture_server, tmp_path):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        page.set_content("""
            <div id="app">
                <button id="danger-step">Dangerous Operation</button>
                <button id="detour-step">Detour Route</button>
                <button id="step-2">Subsequent Step</button>
                <div id="status">initial</div>
            </div>
            <script>
                document.getElementById('danger-step').onclick = () => {
                    document.getElementById('status').textContent = 'partially_mutated';
                };
            </script>
        """)

        store = RecipeStore(root=tmp_path / "recipes")

        # Step 1 Recipe: Clicks dangerous operation (R3 persistent mutation), but postcondition fails.
        # This simulates a mutation started where side effect cannot be verified (UNKNOWN_SIDE_EFFECT).
        r1 = Recipe(
            id="r-danger-unknown",
            name="Dangerous Unknown Effect Recipe",
            domain_pattern="*",
            steps=[
                RecipeStep(
                    action="click",
                    target="#danger-step",
                    risk_class=RiskClass.R3_PERSISTENT_MUTATION,
                )
            ],
            postconditions=[{"role": "heading", "name": "Non-existent Confirmation"}],
        )
        store.save(r1)

        # Detour Recipe
        r_detour = Recipe(
            id="r-detour-route",
            name="Detour Route Recipe",
            domain_pattern="*",
            steps=[RecipeStep(action="click", target="#detour-step", risk_class=RiskClass.R2_LOCAL_MUTABLE)],
        )
        store.save(r_detour)

        # Step 2 Recipe (Subsequent)
        r2 = Recipe(
            id="r-subsequent-step",
            name="Subsequent Step Recipe",
            domain_pattern="*",
            steps=[RecipeStep(action="click", target="#step-2", risk_class=RiskClass.R2_LOCAL_MUTABLE)],
        )
        store.save(r2)

        graph = StateTransitionGraph()
        graph.add_state(SemanticStateNode(state_id="state:init", domain="example.com"))
        graph.add_state(SemanticStateNode(state_id="state:mid", domain="example.com"))
        graph.add_state(SemanticStateNode(state_id="state:final", domain="example.com"))

        # Primary planned edges: init -> mid -> final
        edge1 = TransitionEdge(
            edge_id="edge-1-danger",
            from_state="state:init",
            to_state="state:mid",
            recipe_id=r1.id,
            risk_class=RiskClass.R3_PERSISTENT_MUTATION,
            cost=1.0,
        )
        edge2 = TransitionEdge(
            edge_id="edge-2-subsequent",
            from_state="state:mid",
            to_state="state:final",
            recipe_id=r2.id,
            risk_class=RiskClass.R2_LOCAL_MUTABLE,
            cost=1.0,
        )
        graph.add_edge(edge1)
        graph.add_edge(edge2)

        # Detour edge: init -> final
        edge_detour = TransitionEdge(
            edge_id="edge-detour",
            from_state="state:init",
            to_state="state:final",
            recipe_id=r_detour.id,
            risk_class=RiskClass.R2_LOCAL_MUTABLE,
            cost=2.0,
        )
        graph.add_edge(edge_detour)

        plan = WorkflowPlan(
            plan_id="plan-unknown-safety",
            start_state="state:init",
            goal_state="state:final",
            edges=[edge1, edge2],
            cumulative_risk=RiskClass.R3_PERSISTENT_MUTATION,
            estimated_steps=2,
        )

        engine = WorkflowEngine(store=store, graph=graph)

        # Execute with allow_detour=True
        result = engine.execute(plan, page, manager=manager, allow_detour=True, raise_on_unknown_effect=False)

        # Invariant 1: Terminal halt on UNKNOWN_SIDE_EFFECT
        assert result.ok is False
        assert result.outcome == ExecutionOutcome.UNKNOWN_SIDE_EFFECT
        assert result.failure_edge == "edge-1-danger"
        # Zero detours taken!
        assert result.detour_taken is False
        assert len([e for e in result.edge_results if e.get("detour")]) == 0
        # Zero subsequent edges dispatched! (edge 2 was never executed)
        assert len(result.edge_results) == 1
        assert result.edge_results[0]["edge_id"] == "edge-1-danger"

        # Also verify that when raise_on_unknown_effect=True, UnknownSideEffectError is raised immediately
        with pytest.raises(UnknownSideEffectError):
            engine.execute(plan, page, manager=manager, allow_detour=True, raise_on_unknown_effect=True)
    finally:
        manager.close()


# 9. Negative Regression: R3/R4 Low-Confidence / Text-Only Candidates Disqualified
def test_r3_r4_low_confidence_candidate_disqualified(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        page.set_content("""
            <div id="container">
                <button id="destroy-btn" class="action-btn">Permanently Delete</button>
            </div>
        """)

        # 1. Text-only candidate (count == 1, score 0.95)
        cand_text = AnchorCandidate(kind="text", name="Permanently Delete", score=0.95)
        # 2. Low-confidence scoped_css (count == 1, score 0.75 < 0.80)
        cand_low = AnchorCandidate(kind="scoped_css", selector="#destroy-btn", score=0.75)
        # 3. Mid-confidence scoped_css (count == 1, score 0.85 >= 0.80, but < 0.90)
        cand_mid = AnchorCandidate(kind="scoped_css", selector="#destroy-btn", score=0.85)
        # 4. Neighborhood candidate (count == 1, score 0.92 >= 0.90)
        cand_neigh = AnchorCandidate(kind="neighborhood", selector="#destroy-btn", score=0.92)
        # 5. High-confidence semantic role_name candidate (count == 1, score 0.95)
        cand_high = AnchorCandidate(kind="role_name", role="button", name="Permanently Delete", score=0.95)

        # Test R3 Requirements:
        # - Text-only is strictly disqualified even if count == 1 and score == 0.95
        with pytest.raises(AnchorNotFound):
            AnchorResolver.resolve(
                page,
                AnchorBundle(candidates=[cand_text]),
                mutating=True,
                risk_class=RiskClass.R3_PERSISTENT_MUTATION,
            )

        # - Score < 0.80 is strictly disqualified for R3 even if count == 1
        with pytest.raises(AnchorNotFound):
            AnchorResolver.resolve(
                page,
                AnchorBundle(candidates=[cand_low]),
                mutating=True,
                risk_class=RiskClass.R3_PERSISTENT_MUTATION,
            )

        # - Score >= 0.80 non-text is accepted for R3
        loc, cand, score = AnchorResolver.resolve(
            page,
            AnchorBundle(candidates=[cand_mid]),
            mutating=True,
            risk_class=RiskClass.R3_PERSISTENT_MUTATION,
        )
        assert loc.count() == 1
        assert cand.selector == "#destroy-btn"

        # - Neighborhood with score >= 0.80 is accepted for R3
        loc, cand, score = AnchorResolver.resolve(
            page,
            AnchorBundle(candidates=[cand_neigh]),
            mutating=True,
            risk_class=RiskClass.R3_PERSISTENT_MUTATION,
        )
        assert loc.count() == 1
        assert cand.kind == "neighborhood"

        # Test R4 Requirements:
        # - Text-only disqualified for R4
        with pytest.raises(AnchorNotFound):
            AnchorResolver.resolve(
                page,
                AnchorBundle(candidates=[cand_text]),
                mutating=True,
                risk_class=RiskClass.R4_IRREVERSIBLE,
            )

        # - Score < 0.90 disqualified for R4 (cand_mid has 0.85)
        with pytest.raises(AnchorNotFound):
            AnchorResolver.resolve(
                page,
                AnchorBundle(candidates=[cand_mid]),
                mutating=True,
                risk_class=RiskClass.R4_IRREVERSIBLE,
            )

        # - Neighborhood is strictly disqualified for R4 even if score >= 0.90 (cand_neigh has 0.92)
        with pytest.raises(AnchorNotFound):
            AnchorResolver.resolve(
                page,
                AnchorBundle(candidates=[cand_neigh]),
                mutating=True,
                risk_class=RiskClass.R4_IRREVERSIBLE,
            )

        # - High-confidence semantic candidate (score >= 0.90) is accepted for R4
        loc, cand, score = AnchorResolver.resolve(
            page,
            AnchorBundle(candidates=[cand_text, cand_low, cand_mid, cand_neigh, cand_high]),
            mutating=True,
            risk_class=RiskClass.R4_IRREVERSIBLE,
        )
        assert loc.count() == 1
        assert cand.kind == "role_name"
        assert cand.name == "Permanently Delete"
        assert score == 0.95
    finally:
        manager.close()


# 10. Negative Regression: Detour Preserves Original Risk Envelope
def test_detour_preserves_risk_envelope(ephemeral_cdp_url, fixture_server, tmp_path):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        page.set_content("""
            <div id="app">
                <button id="btn-r3">Perform R3 Action</button>
                <button id="btn-r4">Perform R4 Destructive Action</button>
                <div id="output">ready</div>
            </div>
        """)

        store = RecipeStore(root=tmp_path / "recipes")

        # Direct recipe (R2) that fails because target does not exist
        r_direct = Recipe(
            id="r-direct-r2",
            name="Direct R2",
            domain_pattern="*",
            steps=[RecipeStep(action="click", target="#non-existent-direct", risk_class=RiskClass.R2_LOCAL_MUTABLE)],
        )
        store.save(r_direct)

        # Detour edge requiring R3
        r_detour_r3 = Recipe(
            id="r-detour-r3",
            name="Detour R3",
            domain_pattern="*",
            steps=[RecipeStep(action="click", target="#btn-r3", risk_class=RiskClass.R3_PERSISTENT_MUTATION)],
        )
        store.save(r_detour_r3)

        # Detour edge requiring R4
        r_detour_r4 = Recipe(
            id="r-detour-r4",
            name="Detour R4",
            domain_pattern="*",
            steps=[RecipeStep(action="click", target="#btn-r4", risk_class=RiskClass.R4_IRREVERSIBLE)],
        )
        store.save(r_detour_r4)

        graph = StateTransitionGraph()
        graph.add_state(SemanticStateNode(state_id="state:start", domain="example.com"))
        graph.add_state(SemanticStateNode(state_id="state:goal", domain="example.com"))

        graph.add_edge(TransitionEdge(
            edge_id="edge-direct-r2",
            from_state="state:start",
            to_state="state:goal",
            recipe_id=r_direct.id,
            risk_class=RiskClass.R2_LOCAL_MUTABLE,
            cost=1.0,
        ))
        graph.add_edge(TransitionEdge(
            edge_id="edge-detour-r3",
            from_state="state:start",
            to_state="state:goal",
            recipe_id=r_detour_r3.id,
            risk_class=RiskClass.R3_PERSISTENT_MUTATION,
            cost=1.5,
        ))
        graph.add_edge(TransitionEdge(
            edge_id="edge-detour-r4",
            from_state="state:start",
            to_state="state:goal",
            recipe_id=r_detour_r4.id,
            risk_class=RiskClass.R4_IRREVERSIBLE,
            cost=0.5,  # Artificially cheap cost to verify pruning over cost
        ))

        # Test STG graph find_alternate_path risk envelope pruning directly:
        # 1. When max_allowed_risk is R2, both R3 and R4 edges are pruned -> returns None
        assert graph.find_alternate_path(
            "state:start", "state:goal",
            excluded_edge_ids={"edge-direct-r2"},
            max_allowed_risk=RiskClass.R2_LOCAL_MUTABLE,
            allow_r4=False,
        ) is None

        # 2. When max_allowed_risk is R3 and allow_r4=False: R3 is chosen, R4 is pruned even though R4 has lower cost
        alt_r3 = graph.find_alternate_path(
            "state:start", "state:goal",
            excluded_edge_ids={"edge-direct-r2"},
            max_allowed_risk=RiskClass.R3_PERSISTENT_MUTATION,
            allow_r4=False,
        )
        assert alt_r3 is not None
        assert len(alt_r3) == 1
        assert alt_r3[0].edge_id == "edge-detour-r3"

        # 3. When max_allowed_risk is R4 but allow_r4=False: R4 is STILL pruned
        alt_r4_blocked = graph.find_alternate_path(
            "state:start", "state:goal",
            excluded_edge_ids={"edge-direct-r2"},
            max_allowed_risk=RiskClass.R4_IRREVERSIBLE,
            allow_r4=False,
        )
        assert alt_r4_blocked is not None
        assert alt_r4_blocked[0].edge_id == "edge-detour-r3"

        # Test WorkflowEngine execution respects original R2 workflow plan envelope:
        plan_r2 = WorkflowPlan(
            plan_id="plan-r2-bounded",
            start_state="state:start",
            goal_state="state:goal",
            edges=[graph.get_outgoing_edges("state:start")[0]],
            cumulative_risk=RiskClass.R2_LOCAL_MUTABLE,
            estimated_steps=1,
        )

        engine = WorkflowEngine(store=store, graph=graph)
        res = engine.execute(plan_r2, page, manager=manager, allow_detour=True, allow_r4=False)

        # The engine must refuse to take R3 or R4 detours that exceed the original R2 envelope!
        assert res.ok is False
        assert res.outcome == ExecutionOutcome.SAFE_FAILURE
        assert res.detour_taken is False
        assert len(res.edge_results) == 1
        assert res.edge_results[0]["edge_id"] == "edge-direct-r2"
    finally:
        manager.close()


# 11. Negative Regression: Dynamic Replanning Accumulates Failed Edges and Halts on max_detours
def test_detour_bounded_and_accumulates_failed_edges(ephemeral_cdp_url, fixture_server, tmp_path):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        page.set_content("""
            <div id="app">
                <button id="success-btn">Final Success</button>
                <div id="status">idle</div>
            </div>
            <script>
                document.getElementById('success-btn').onclick = () => {
                    document.getElementById('status').textContent = 'Reached Success!';
                };
            </script>
        """)

        store = RecipeStore(root=tmp_path / "recipes")

        # 3 recipes that fail because target selector is missing
        r_fail1 = Recipe(id="r-f1", name="Fail 1", domain_pattern="*", steps=[RecipeStep(action="click", target="#missing-1")])
        r_fail2 = Recipe(id="r-f2", name="Fail 2", domain_pattern="*", steps=[RecipeStep(action="click", target="#missing-2")])
        r_fail3 = Recipe(id="r-f3", name="Fail 3", domain_pattern="*", steps=[RecipeStep(action="click", target="#missing-3")])
        # 1 recipe that succeeds
        r_succ = Recipe(id="r-succ", name="Success", domain_pattern="*", steps=[RecipeStep(action="click", target="#success-btn")])

        store.save(r_fail1)
        store.save(r_fail2)
        store.save(r_fail3)
        store.save(r_succ)

        graph = StateTransitionGraph()
        graph.add_state(SemanticStateNode(state_id="state:start", domain="example.com"))
        graph.add_state(SemanticStateNode(state_id="state:end", domain="example.com"))

        e1 = TransitionEdge(edge_id="edge-fail-1", from_state="state:start", to_state="state:end", recipe_id=r_fail1.id, cost=1.0)
        e2 = TransitionEdge(edge_id="edge-fail-2", from_state="state:start", to_state="state:end", recipe_id=r_fail2.id, cost=2.0)
        e3 = TransitionEdge(edge_id="edge-fail-3", from_state="state:start", to_state="state:end", recipe_id=r_fail3.id, cost=3.0)
        e4 = TransitionEdge(edge_id="edge-success-4", from_state="state:start", to_state="state:end", recipe_id=r_succ.id, cost=4.0)

        graph.add_edge(e1)
        graph.add_edge(e2)
        graph.add_edge(e3)
        graph.add_edge(e4)

        plan = WorkflowPlan(
            plan_id="plan-bounded-replanning",
            start_state="state:start",
            goal_state="state:end",
            edges=[e1],
            cumulative_risk=RiskClass.R2_LOCAL_MUTABLE,
            estimated_steps=1,
        )

        engine = WorkflowEngine(store=store, graph=graph)

        # With max_detours=2:
        # e1 fails -> replan 1 selects e2 (detour_count=1, failed_edges={e1})
        # e2 fails -> replan 2 selects e3 (detour_count=2, failed_edges={e1, e2})
        # e3 fails -> detour_count (2) >= max_detours (2) -> fail closed with WorkflowInterruptedError!
        # e4 must NEVER be dispatched.
        with pytest.raises(WorkflowInterruptedError) as exc_info:
            engine.execute(plan, page, manager=manager, allow_detour=True, max_detours=2)

        err_msg = str(exc_info.value)
        assert "maximum detour limit (2) reached" in err_msg
        assert "edge-fail-1" in err_msg
        assert "edge-fail-2" in err_msg
        assert "edge-fail-3" in err_msg
        assert page.locator("#status").inner_text() == "idle"  # success-btn was never clicked!
    finally:
        manager.close()


# 12. Negative Regression: Applying Stale Repair Candidate with Mismatched Recipe Content Digest Raises StaleRepairError
def test_stale_repair_candidate_rejected(tmp_path):
    store = RecipeStore(root=tmp_path / "recipes")

    bundle_v1 = AnchorBundle(
        candidates=[
            AnchorCandidate(kind="test_attr", selector='[data-testid="old-btn"]', score=0.95),
            AnchorCandidate(kind="role_name", role="button", name="Old Button", score=0.90),
        ]
    )

    recipe = Recipe(
        id="test-stale-repair",
        name="Recipe for Stale Repair Test",
        domain_pattern="*",
        steps=[RecipeStep(action="click", target=bundle_v1)],
    )
    store.save(recipe)

    # Initial saved state
    initial_recipe = store.get(recipe.id)
    v1_digest = initial_recipe.lifecycle.content_digest
    v1_generation = initial_recipe.lifecycle.generation
    assert v1_digest is not None
    assert v1_generation == 1

    # Mutate canonical recipe to generation 2 (simulating a developer/canonical recipe update)
    recipe.steps[0].action = "fill"
    recipe.steps[0].value = "New text"
    store.save(recipe)

    v2_recipe = store.get(recipe.id)
    v2_digest = v2_recipe.lifecycle.content_digest
    v2_generation = v2_recipe.lifecycle.generation
    assert v2_generation == 2
    assert v2_digest != v1_digest

    # Now an old repair candidate captured against generation 1 / v1_digest is submitted
    stale_repair = RepairCandidate(
        recipe_id=recipe.id,
        step_index=0,
        broken_candidate={"kind": "test_attr", "selector": '[data-testid="old-btn"]', "score": 0.95},
        healed_candidate={"kind": "role_name", "role": "button", "name": "Healed Button", "score": 0.91},
        confidence=0.91,
        recipe_generation=v1_generation,
        recipe_content_digest=v1_digest,  # Mismatched against current canonical v2_digest!
    )
    store.record_repair_candidate(stale_repair)

    # Applying the stale repair MUST fail closed with StaleRepairError
    with pytest.raises(StaleRepairError) as exc_info:
        store.apply_repair_candidate(stale_repair.id, actor="offline_learner")

    assert "does not match canonical recipe digest" in str(exc_info.value)

    # Invariant: Recipe on disk was untouched and remains at generation 2 with v2_digest
    after_recipe = store.get(recipe.id)
    assert after_recipe.lifecycle.generation == 2
    assert after_recipe.lifecycle.content_digest == v2_digest
