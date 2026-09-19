"""
Tests for Task-010: State-Transition Graph & Workflow Composition (Milestone v2.5).
Verifies semantic state nodes, transition edges, Dijkstra path finding,
cumulative risk computation, per-edge safety invariants, and ephemeral browser execution.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from browser_core.contracts import (
    ExecutionOutcome,
    PreconditionFailedError,
    RiskClass,
    RiskGateError,
    SafetySpec,
    SemanticStateNode,
    TransitionEdge,
    UnknownSideEffectError,
    WorkflowExecutionResult,
    WorkflowInterruptedError,
    WorkflowPlan,
    WorkflowSpec,
    WorkflowStep,
)
from browser_core.page_manager import PageManager
from browser_core.recipes import (
    Recipe,
    RecipeEngine,
    RecipeExecutionResult,
    RecipeStep,
    RecipeStore,
)
from browser_core.workflows import (
    RISK_COST_WEIGHTS,
    RISK_RANKS,
    StateTransitionGraph,
    WorkflowComposer,
    WorkflowEngine,
)


def _session(ephemeral_cdp_url, fixture_server):
    manager = PageManager(ephemeral_cdp_url, test_mode=True)
    manager.connect()
    page = manager.primary_page()
    manager.install_scanner(page)
    page.goto(f"{fixture_server}/interactive_page.html", wait_until="domcontentloaded")
    return manager, page


# 1. Contract Serialization & Aliases
def test_workflow_contracts():
    node = SemanticStateNode(
        state_id="auth:login",
        domain="example.com",
        route_pattern="/login",
        required_anchors=[{"role": "heading", "name": "Log in"}],
        description="Login page",
    )
    d_node = node.to_dict()
    assert d_node["state_id"] == "auth:login"
    assert d_node["domain"] == "example.com"
    assert len(d_node["required_anchors"]) == 1

    edge = TransitionEdge(
        edge_id="edge-1",
        from_state="auth:login",
        to_state="auth:dashboard",
        recipe_id="submit_login",
        risk_class=RiskClass.R3_PERSISTENT_MUTATION,
        cost=3.5,
    )
    d_edge = edge.to_dict()
    assert d_edge["edge_id"] == "edge-1"
    assert d_edge["risk_class"] == "R3"

    plan = WorkflowPlan(
        plan_id="plan-123",
        start_state="auth:login",
        goal_state="auth:dashboard",
        edges=[edge],
        cumulative_risk=RiskClass.R3_PERSISTENT_MUTATION,
        estimated_steps=1,
    )
    d_plan = plan.to_dict()
    assert d_plan["plan_id"] == "plan-123"
    assert len(d_plan["edges"]) == 1

    # Aliases
    assert WorkflowSpec is WorkflowPlan
    assert WorkflowStep is TransitionEdge


# 2. State-Transition Graph Ingestion & Cost Weighting
def test_graph_ingestion_and_edge_costs():
    graph = StateTransitionGraph()

    r1 = Recipe(
        id="nav_docs",
        name="Navigate Docs",
        domain_pattern="https://example.com/docs*",
        steps=[RecipeStep(action="wait_for", target={"name": "Docs"}, value="visible")],
        safety=SafetySpec(max_risk=RiskClass.R0_READONLY),
        metadata={"from_state": "example:home", "to_state": "example:docs"},
    )
    r2 = Recipe(
        id="fill_form",
        name="Fill Form",
        domain_pattern="https://example.com/form*",
        steps=[
            RecipeStep(action="fill", target={"name": "Email"}, value="user@example.com"),
            RecipeStep(action="fill", target={"name": "Name"}, value="Alice"),
        ],
        safety=SafetySpec(max_risk=RiskClass.R2_LOCAL_MUTABLE),
        metadata={"from_state": "example:docs", "to_state": "example:form_filled"},
    )
    r3 = Recipe(
        id="delete_account",
        name="Delete Account",
        domain_pattern="https://example.com/settings*",
        steps=[RecipeStep(action="click", target={"name": "Purge Account"})],
        safety=SafetySpec(max_risk=RiskClass.R4_IRREVERSIBLE),
        metadata={"from_state": "example:form_filled", "to_state": "example:account_deleted"},
    )

    graph.ingest_recipe(r1)
    graph.ingest_recipe(r2)
    graph.ingest_recipe(r3)

    assert "example:home" in graph.states
    assert "example:docs" in graph.states
    assert "example:form_filled" in graph.states
    assert "example:account_deleted" in graph.states

    edges_home = graph.get_outgoing_edges("example:home")
    assert len(edges_home) == 1
    assert edges_home[0].risk_class == RiskClass.R0_READONLY
    assert edges_home[0].cost == round(1.0 + RISK_COST_WEIGHTS[RiskClass.R0_READONLY] + 1 * 0.2, 2)

    edges_docs = graph.get_outgoing_edges("example:docs")
    assert len(edges_docs) == 1
    assert edges_docs[0].risk_class == RiskClass.R2_LOCAL_MUTABLE
    assert edges_docs[0].cost == round(1.0 + RISK_COST_WEIGHTS[RiskClass.R2_LOCAL_MUTABLE] + 2 * 0.2, 2)

    edges_form = graph.get_outgoing_edges("example:form_filled")
    assert len(edges_form) == 1
    assert edges_form[0].risk_class == RiskClass.R4_IRREVERSIBLE
    assert edges_form[0].cost == round(1.0 + RISK_COST_WEIGHTS[RiskClass.R4_IRREVERSIBLE] + 1 * 0.2, 2)


# 3. Live State Resolution from DOM Tree
def test_resolve_live_state():
    graph = StateTransitionGraph()
    graph.add_state(SemanticStateNode(
        state_id="portal:login",
        domain="portal.example.com",
        route_pattern="/login",
        required_anchors=[{"role": "button", "name": "Sign In"}],
    ))
    graph.add_state(SemanticStateNode(
        state_id="portal:dashboard",
        domain="portal.example.com",
        route_pattern="/dashboard",
        required_anchors=[{"role": "heading", "name": "Welcome Back"}],
    ))

    nodes_login = [
        {"role": "textbox", "name": "Username", "value": ""},
        {"role": "button", "name": "Sign In", "value": ""},
    ]
    matched = graph.resolve_live_state("https://portal.example.com/login", nodes_login)
    assert matched is not None
    assert matched.state_id == "portal:login"

    nodes_dash = [
        {"role": "heading", "name": "Welcome Back", "value": ""},
        {"role": "button", "name": "Logout", "value": ""},
    ]
    matched_dash = graph.resolve_live_state("https://portal.example.com/dashboard", nodes_dash)
    assert matched_dash is not None
    assert matched_dash.state_id == "portal:dashboard"


# 4. Dijkstra Path Finding & Cumulative Risk Computation
def test_workflow_composer_dijkstra():
    graph = StateTransitionGraph()
    graph.add_state(SemanticStateNode(state_id="S0", domain="test.com"))
    graph.add_state(SemanticStateNode(state_id="S1", domain="test.com"))
    graph.add_state(SemanticStateNode(state_id="S2", domain="test.com"))
    graph.add_state(SemanticStateNode(state_id="S3", domain="test.com"))

    # Path A: S0 -> S1 -> S3 (S0->S1 cost 1.0 R1, S1->S3 cost 15.0 R4)
    graph.add_edge(TransitionEdge(
        edge_id="e01", from_state="S0", to_state="S1", recipe_id="r01", risk_class=RiskClass.R1_REVERSIBLE_NAV, cost=1.0
    ))
    graph.add_edge(TransitionEdge(
        edge_id="e13", from_state="S1", to_state="S3", recipe_id="r13", risk_class=RiskClass.R4_IRREVERSIBLE, cost=15.0
    ))

    # Path B: S0 -> S2 -> S3 (S0->S2 cost 2.0 R1, S2->S3 cost 3.0 R2)
    graph.add_edge(TransitionEdge(
        edge_id="e02", from_state="S0", to_state="S2", recipe_id="r02", risk_class=RiskClass.R1_REVERSIBLE_NAV, cost=2.0
    ))
    graph.add_edge(TransitionEdge(
        edge_id="e23", from_state="S2", to_state="S3", recipe_id="r23", risk_class=RiskClass.R2_LOCAL_MUTABLE, cost=3.0
    ))

    composer = WorkflowComposer(graph)

    # Finding path S0 -> S3 with all risks allowed:
    # Path A total cost: 1.0 + 15.0 = 16.0
    # Path B total cost: 2.0 + 3.0 = 5.0 -> Path B is cheaper!
    plan = composer.plan("S0", "S3", max_allowed_risk=RiskClass.R4_IRREVERSIBLE)
    assert plan is not None
    assert [e.edge_id for e in plan.edges] == ["e02", "e23"]
    assert plan.cumulative_risk == RiskClass.R2_LOCAL_MUTABLE
    assert plan.estimated_steps == 2

    # If Path B didn't exist and we sought S1 -> S3 with max risk R2:
    plan_restricted = composer.plan("S1", "S3", max_allowed_risk=RiskClass.R2_LOCAL_MUTABLE)
    assert plan_restricted is None  # e13 is R4, pruned!


# 5. Composition Safety Invariant: R4 Execution Gate
def test_workflow_engine_r4_gating():
    graph = StateTransitionGraph()
    plan = WorkflowPlan(
        plan_id="plan-destructive",
        start_state="S0",
        goal_state="S1",
        edges=[
            TransitionEdge(
                edge_id="e_destruct",
                from_state="S0",
                to_state="S1",
                recipe_id="r_purge",
                risk_class=RiskClass.R4_IRREVERSIBLE,
                cost=10.0,
            )
        ],
        cumulative_risk=RiskClass.R4_IRREVERSIBLE,
        estimated_steps=1,
    )

    store = MagicMock(spec=RecipeStore)
    recipe_engine = MagicMock(spec=RecipeEngine)
    engine = WorkflowEngine(store=store, recipe_engine=recipe_engine, graph=graph)
    page = MagicMock()
    manager = MagicMock()

    # Blocked without allow_r4=True
    with pytest.raises(RiskGateError) as exc_info:
        engine.execute(plan, page, manager=manager, allow_r4=False)
    assert "Execution blocked without explicit allow_r4=True" in str(exc_info.value)

    # Allowed with allow_r4=True
    recipe_engine.execute.return_value = RecipeExecutionResult(
        ok=True, recipe_id="r_purge", completed_steps=1
    )
    result = engine.execute(plan, page, manager=manager, allow_r4=True)
    assert result.ok is True
    assert result.cumulative_risk == RiskClass.R4_IRREVERSIBLE


# 6. Critical Composition Invariant: UNKNOWN_SIDE_EFFECT Halts Workflow Immediately
def test_workflow_engine_unknown_side_effect_halts_immediately():
    graph = StateTransitionGraph()
    plan = WorkflowPlan(
        plan_id="plan-multi-hop",
        start_state="S0",
        goal_state="S3",
        edges=[
            TransitionEdge(edge_id="e01", from_state="S0", to_state="S1", recipe_id="r01", risk_class=RiskClass.R2_LOCAL_MUTABLE),
            TransitionEdge(edge_id="e12", from_state="S1", to_state="S2", recipe_id="r12", risk_class=RiskClass.R3_PERSISTENT_MUTATION),
            TransitionEdge(edge_id="e23", from_state="S2", to_state="S3", recipe_id="r23", risk_class=RiskClass.R3_PERSISTENT_MUTATION),
        ],
        cumulative_risk=RiskClass.R3_PERSISTENT_MUTATION,
        estimated_steps=3,
    )

    store = MagicMock(spec=RecipeStore)
    recipe_engine = MagicMock(spec=RecipeEngine)
    engine = WorkflowEngine(store=store, recipe_engine=recipe_engine, graph=graph)
    page = MagicMock()
    manager = MagicMock()

    # Edge 0 succeeds
    res_success = RecipeExecutionResult(ok=True, recipe_id="r01", completed_steps=1)
    # Edge 1 produces UNKNOWN_SIDE_EFFECT
    res_unknown = RecipeExecutionResult(
        ok=False,
        recipe_id="r12",
        completed_steps=1,
        outcome=ExecutionOutcome.UNKNOWN_SIDE_EFFECT,
        message="DOM mutated unpredictably during mutation step",
    )

    recipe_engine.execute.side_effect = [res_success, res_unknown]

    result = engine.execute(plan, page, manager=manager)

    # Invariant checks:
    assert result.ok is False
    assert result.outcome == ExecutionOutcome.UNKNOWN_SIDE_EFFECT
    assert result.completed_edges == 1
    assert result.failure_edge == "e12"
    assert "UNKNOWN_SIDE_EFFECT" in result.message
    # Edge 2 (e23) was NEVER invoked
    assert recipe_engine.execute.call_count == 2


# 7. Post-Edge State Transition Verification
def test_workflow_engine_post_edge_state_verification_failure():
    graph = StateTransitionGraph()
    # State S1 requires a specific heading anchor
    graph.add_state(SemanticStateNode(
        state_id="S1",
        domain="test.com",
        required_anchors=[{"role": "heading", "name": "Confirmation Received"}],
    ))
    plan = WorkflowPlan(
        plan_id="plan-verify",
        start_state="S0",
        goal_state="S2",
        edges=[
            TransitionEdge(edge_id="e01", from_state="S0", to_state="S1", recipe_id="r01", risk_class=RiskClass.R2_LOCAL_MUTABLE),
            TransitionEdge(edge_id="e12", from_state="S1", to_state="S2", recipe_id="r12", risk_class=RiskClass.R2_LOCAL_MUTABLE),
        ],
        cumulative_risk=RiskClass.R2_LOCAL_MUTABLE,
        estimated_steps=2,
    )

    store = MagicMock(spec=RecipeStore)
    recipe_engine = MagicMock(spec=RecipeEngine)
    engine = WorkflowEngine(store=store, recipe_engine=recipe_engine, graph=graph)
    page = MagicMock()
    page.url = "https://test.com/step1"
    manager = MagicMock()

    # Edge recipe itself claimed ok=True
    recipe_engine.execute.return_value = RecipeExecutionResult(
        ok=True, recipe_id="r01", completed_steps=1
    )

    # But observe() on page reveals missing heading!
    manager.observe.return_value = MagicMock(tree=[{"role": "paragraph", "name": "Still processing..."}])
    result = engine.execute(plan, page, manager=manager)

    assert result.ok is False
    assert result.outcome == ExecutionOutcome.SAFE_FAILURE
    assert result.completed_edges == 0
    assert result.failure_edge == "e01"
    assert "missing required anchors" in result.message
    # Edge 2 never dispatched
    assert recipe_engine.execute.call_count == 1


# 8. Ephemeral Browser Integration Test
def test_workflow_ephemeral_browser_integration(ephemeral_cdp_url, fixture_server, tmp_path):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        # Create 2 micro-recipes operating on the interactive fixture:
        # Micro-recipe 1: Fill name input (R2)
        r1 = Recipe(
            id="fill_name",
            name="Fill Name",
            domain_pattern=f"{fixture_server}/*",
            steps=[RecipeStep(action="fill", target={"selector": "#name"}, value="Dr. Browser")],
            safety=SafetySpec(max_risk=RiskClass.R2_LOCAL_MUTABLE),
            metadata={"from_state": "fixture:initial", "to_state": "fixture:name_filled"},
        )
        # Micro-recipe 2: Click save details
        r2 = Recipe(
            id="save_form",
            name="Save Form",
            domain_pattern=f"{fixture_server}/*",
            steps=[RecipeStep(action="click", target={"selector": "#save"})],
            safety=SafetySpec(max_risk=RiskClass.R3_PERSISTENT_MUTATION),
            postconditions=[{"role": "status", "name": "Saved: Dr. Browser"}],
            metadata={"from_state": "fixture:name_filled", "to_state": "fixture:saved"},
        )

        store = RecipeStore(root=tmp_path / "recipes")
        store.save(r1)
        store.save(r2)

        graph = StateTransitionGraph()
        graph.ingest_store(store)

        composer = WorkflowComposer(graph)
        plan = composer.plan("fixture:initial", "fixture:saved")
        assert plan is not None
        assert len(plan.edges) == 2
        assert plan.cumulative_risk == RiskClass.R3_PERSISTENT_MUTATION

        engine = WorkflowEngine(store=store, graph=graph)
        exec_res = engine.execute(plan, page, manager=manager)

        assert exec_res.ok is True
        assert exec_res.outcome == ExecutionOutcome.CONFIRMED_SUCCESS
        assert exec_res.completed_edges == 2
        assert exec_res.current_state == "fixture:saved"

        # Verify DOM reflects both steps executed in sequence
        status_text = page.locator("#status").inner_text()
        assert status_text == "Saved: Dr. Browser"
    finally:
        manager.close()


# 9. CLI Workflow Commands Test
def test_workflow_cli_commands(tmp_path, capsys):
    import sys
    scripts_dir = str(Path(__file__).resolve().parents[1] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from cdp_controller import main

    r = Recipe(
        id="test_login",
        name="Test Login",
        domain_pattern="https://auth.example.com/*",
        steps=[RecipeStep(action="click", target={"name": "Login"})],
        safety=SafetySpec(max_risk=RiskClass.R2_LOCAL_MUTABLE),
        metadata={"from_state": "auth:login", "to_state": "auth:welcome"},
    )
    store = RecipeStore(root=tmp_path / "recipes")
    store.save(r)

    # 9a. Test workflow graph
    exit_code = main(["workflow", "graph", "--recipes-dir", str(tmp_path / "recipes")])
    assert exit_code == 0
    captured = capsys.readouterr()
    graph_data = json.loads(captured.out)
    assert "auth:login" in graph_data["states"]
    assert "auth:welcome" in graph_data["states"]
    assert len(graph_data["edges"]["auth:login"]) == 1

    # 9b. Test workflow plan
    exit_code = main([
        "workflow", "plan",
        "--from", "auth:login",
        "--to", "auth:welcome",
        "--recipes-dir", str(tmp_path / "recipes"),
    ])
    assert exit_code == 0
    captured = capsys.readouterr()
    plan_data = json.loads(captured.out)
    assert plan_data["start_state"] == "auth:login"
    assert plan_data["goal_state"] == "auth:welcome"
    assert plan_data["cumulative_risk"] == "R3"
    assert len(plan_data["edges"]) == 1
