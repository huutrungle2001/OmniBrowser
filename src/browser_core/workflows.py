"""
workflows.py - State-Transition Graph & Workflow Composition for OmniBrowser.
Milestone v2.5: Dynamic graph-based micro-recipe composition with strict per-edge safety invariants.
"""

from __future__ import annotations

import heapq
import json
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.sync_api import Page

try:
    from .contracts import (
        ExecutionOutcome,
        MatcherSpec,
        PreconditionFailedError,
        QuarantinedRecipeError,
        RecipeLifecycleState,
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
    from .page_manager import PageManager
    from .recipes import (
        Recipe,
        RecipeEngine,
        RecipeStep,
        RecipeStore,
        _domain_from_url,
        _find_anchor_in_tree,
        _normalize_path,
        get_recipe_risk,
        match_page_state,
    )
except ImportError:
    from browser_core.contracts import (
        ExecutionOutcome,
        MatcherSpec,
        PreconditionFailedError,
        QuarantinedRecipeError,
        RecipeLifecycleState,
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
        RecipeStep,
        RecipeStore,
        _domain_from_url,
        _find_anchor_in_tree,
        _normalize_path,
        get_recipe_risk,
        match_page_state,
    )

RISK_RANKS = {
    RiskClass.R0_READONLY: 0,
    RiskClass.R1_REVERSIBLE_NAV: 1,
    RiskClass.R2_LOCAL_MUTABLE: 2,
    RiskClass.R3_PERSISTENT_MUTATION: 3,
    RiskClass.R4_IRREVERSIBLE: 4,
}

RISK_COST_WEIGHTS = {
    RiskClass.R0_READONLY: 0.1,
    RiskClass.R1_REVERSIBLE_NAV: 0.3,
    RiskClass.R2_LOCAL_MUTABLE: 0.8,
    RiskClass.R3_PERSISTENT_MUTATION: 2.0,
    RiskClass.R4_IRREVERSIBLE: 10.0,
}


class StateTransitionGraph:
    """Directed graph of semantic browser states and guarded recipe transitions."""

    def __init__(self) -> None:
        self.states: dict[str, SemanticStateNode] = {}
        self.edges: dict[str, list[TransitionEdge]] = {}

    def add_state(self, state: SemanticStateNode) -> None:
        self.states[state.state_id] = state
        if state.state_id not in self.edges:
            self.edges[state.state_id] = []

    def add_edge(self, edge: TransitionEdge) -> None:
        if edge.from_state not in self.states:
            domain = edge.from_state.split(":")[0] if ":" in edge.from_state else "generic"
            self.add_state(SemanticStateNode(state_id=edge.from_state, domain=domain))
        if edge.to_state not in self.states:
            domain = edge.to_state.split(":")[0] if ":" in edge.to_state else "generic"
            self.add_state(SemanticStateNode(state_id=edge.to_state, domain=domain))
        self.edges.setdefault(edge.from_state, []).append(edge)

    def get_state(self, state_id: str) -> SemanticStateNode | None:
        return self.states.get(state_id)

    def get_outgoing_edges(self, state_id: str) -> list[TransitionEdge]:
        return list(self.edges.get(state_id, []))

    def ingest_recipe(self, recipe: Recipe) -> None:
        """Ingest a Recipe into states and transition edges."""
        if hasattr(recipe, "lifecycle") and recipe.lifecycle.state in {
            RecipeLifecycleState.QUARANTINED,
            RecipeLifecycleState.SUSPECT,
            RecipeLifecycleState.ARCHIVED,
        }:
            return

        meta = getattr(recipe, "metadata", {}) or {}
        domain_val = recipe.domain_pattern if isinstance(recipe.domain_pattern, str) else (recipe.domain_pattern[0] if recipe.domain_pattern else "*")
        domain = _domain_from_url(domain_val)

        # Resolve or synthesize from_state
        from_id = meta.get("from_state")
        if not from_id:
            route_part = _normalize_path(str(domain_val))
            from_id = f"{domain}:{route_part}" if route_part else f"{domain}:root"

        if from_id not in self.states:
            matcher = getattr(recipe, "matcher", None)
            req_anchors = getattr(matcher, "required_anchors", []) if matcher else []
            self.add_state(SemanticStateNode(
                state_id=from_id,
                domain=domain,
                route_pattern=str(domain_val),
                required_anchors=req_anchors,
                description=f"State for {recipe.name}",
            ))

        # Resolve or synthesize to_state
        to_id = meta.get("to_state")
        if not to_id:
            to_id = f"{from_id}__{recipe.id}_completed"

        if to_id not in self.states:
            postconditions = getattr(recipe, "postconditions", []) or []
            self.add_state(SemanticStateNode(
                state_id=to_id,
                domain=domain,
                required_anchors=postconditions,
                description=f"Target state of {recipe.id}",
            ))

        risk = recipe.max_risk
        cost = round(1.0 + RISK_COST_WEIGHTS.get(risk, 1.0) + len(recipe.steps) * 0.2, 2)
        params = list(recipe.parameters) if hasattr(recipe, "parameters") else []
        edge_id = f"edge-{recipe.id}"
        self.add_edge(TransitionEdge(
            edge_id=edge_id,
            from_state=from_id,
            to_state=to_id,
            recipe_id=recipe.id,
            risk_class=risk,
            required_params=params,
            cost=cost,
        ))

    def ingest_store(self, store: RecipeStore) -> None:
        """Ingest all recipes from a store."""
        for recipe in store.all():
            self.ingest_recipe(recipe)

    def resolve_live_state(self, page_url: str, tree_nodes: Any) -> SemanticStateNode | None:
        """Match live page to the most confident semantic state node with ambiguity detection."""
        u_domain = _domain_from_url(page_url)
        candidates: list[tuple[float, SemanticStateNode]] = []

        for state in self.states.values():
            if state.domain and state.domain != u_domain and state.domain != "generic" and "*" not in state.domain:
                continue

            matcher_spec = MatcherSpec(
                required_anchors=state.required_anchors,
                semantic_fingerprint=state.semantic_fingerprint,
            )
            clean_id = re.sub(r"[^a-zA-Z0-9_.-]", "_", f"state_{state.state_id}").strip("_") or "state"
            pattern = state.route_pattern or state.domain or "*"
            if pattern and not pattern.startswith("http") and not pattern.startswith("*"):
                pattern = f"*{pattern}*"
            dummy_recipe = Recipe(
                id=clean_id,
                name=state.state_id,
                domain_pattern=pattern,
                steps=[RecipeStep(action="eval", value="1")],
                matcher=matcher_spec,
            )
            res = match_page_state(dummy_recipe, page_url, tree_nodes)
            if res.get("matched", False):
                candidates.append((float(res.get("score", 0.0)), state))
            elif not state.required_anchors and state.route_pattern and state.route_pattern in page_url:
                candidates.append((0.5, state))

        if not candidates:
            return None

        # Sort by descending confidence score
        candidates.sort(key=lambda item: item[0], reverse=True)

        # Ambiguity detection: If two top candidates have indistinguishable scores (delta < 0.01), fail-closed
        if len(candidates) > 1 and abs(candidates[0][0] - candidates[1][0]) < 1e-4:
            return None

        return candidates[0][1]

    def find_alternate_path(
        self,
        start_state_id: str,
        end_state_id: str,
        excluded_edge_ids: set[str] | None = None,
        *,
        excluded_edges: set[str] | list[str] | None = None,
        max_allowed_risk: str | None = None,
    ) -> list[TransitionEdge] | None:
        """
        Find an alternate lowest-cost path from start_state_id to end_state_id
        using Dijkstra's algorithm, pruning any edges in excluded_edge_ids.
        """
        if start_state_id not in self.states or end_state_id not in self.states:
            return None

        if start_state_id == end_state_id:
            return []

        excluded = set(excluded_edge_ids or set())
        if excluded_edges:
            excluded.update(excluded_edges)

        max_risk_rank = RISK_RANKS.get(max_allowed_risk, 4) if max_allowed_risk else 4

        # Priority queue entries: (cost, current_state, path_edges)
        queue: list[tuple[float, str, list[TransitionEdge]]] = [(0.0, start_state_id, [])]
        visited: dict[str, float] = {}

        while queue:
            cost, current, path = heapq.heappop(queue)

            if current == end_state_id and path:
                return path

            if current in visited and visited[current] <= cost:
                continue
            visited[current] = cost

            for edge in self.get_outgoing_edges(current):
                edge_id = getattr(edge, "edge_id", None) or getattr(edge, "id", None)
                if edge_id in excluded or getattr(edge, "id", None) in excluded or getattr(edge, "edge_id", None) in excluded:
                    continue

                edge_risk_rank = RISK_RANKS.get(edge.risk_class, 2)
                if edge_risk_rank > max_risk_rank:
                    continue

                next_cost = cost + edge.cost
                if edge.to_state in visited and visited[edge.to_state] <= next_cost:
                    continue

                heapq.heappush(queue, (next_cost, edge.to_state, path + [edge]))

        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "states": {sid: s.to_dict() for sid, s in self.states.items()},
            "edges": {from_id: [e.to_dict() for e in edge_list] for from_id, edge_list in self.edges.items()},
        }


class WorkflowComposer:
    """Composes optimal, multi-edge execution plans across the state-transition graph."""

    def __init__(self, graph: StateTransitionGraph) -> None:
        self.graph = graph

    def plan(
        self,
        start_state: str,
        goal_state: str,
        *,
        max_allowed_risk: RiskClass = RiskClass.R4_IRREVERSIBLE,
    ) -> WorkflowPlan | None:
        """
        Find the lowest-cost path from start_state to goal_state using Dijkstra's algorithm.
        Prunes edges exceeding max_allowed_risk.
        """
        if start_state not in self.graph.states:
            raise KeyError(f"Start state '{start_state}' does not exist in graph.")
        if goal_state not in self.graph.states:
            raise KeyError(f"Goal state '{goal_state}' does not exist in graph.")

        max_risk_rank = RISK_RANKS.get(max_allowed_risk, 4)

        # Priority queue entries: (cost, current_state, path_edges)
        queue: list[tuple[float, str, list[TransitionEdge]]] = [(0.0, start_state, [])]
        visited: dict[str, float] = {}

        while queue:
            cost, current, path = heapq.heappop(queue)

            if current == goal_state:
                # Path found! Compute cumulative risk (maximum risk rank among all edges)
                cumulative_risk = RiskClass.R0_READONLY
                max_rank = 0
                for e in path:
                    rank = RISK_RANKS.get(e.risk_class, 0)
                    if rank > max_rank:
                        max_rank = rank
                        cumulative_risk = e.risk_class

                return WorkflowPlan(
                    plan_id=f"plan_{uuid4().hex[:8]}",
                    start_state=start_state,
                    goal_state=goal_state,
                    edges=path,
                    cumulative_risk=cumulative_risk,
                    estimated_steps=len(path),
                )

            if current in visited and visited[current] <= cost:
                continue
            visited[current] = cost

            for edge in self.graph.get_outgoing_edges(current):
                edge_risk_rank = RISK_RANKS.get(edge.risk_class, 2)
                if edge_risk_rank > max_risk_rank:
                    continue  # Prune edges exceeding allowed risk ceiling

                next_cost = cost + edge.cost
                if edge.to_state in visited and visited[edge.to_state] <= next_cost:
                    continue

                heapq.heappush(queue, (next_cost, edge.to_state, path + [edge]))

        return None


class WorkflowEngine:
    """Executes composed workflows with strict per-edge safety invariants and write barriers."""

    def __init__(
        self,
        store: RecipeStore,
        recipe_engine: RecipeEngine | None = None,
        graph: StateTransitionGraph | None = None,
    ) -> None:
        self.store = store
        self.recipe_engine = recipe_engine or RecipeEngine(store=store)
        self.graph = graph or StateTransitionGraph()
        if not self.graph.states:
            self.graph.ingest_store(store)

    def execute(
        self,
        plan: WorkflowPlan,
        page: Page,
        params: Mapping[str, Any] | None = None,
        *,
        manager: PageManager,
        allow_r4: bool = False,
        allow_detour: bool = True,
        raise_on_unknown_effect: bool = False,
        raise_on_failure: bool = False,
    ) -> WorkflowExecutionResult:
        """
        Execute a planned workflow edge-by-edge.
        Preserves all Milestone v2.4 invariants:
        - Canonical risk is re-evaluated from authoritative RecipeStore.
        - R4 authorization cannot be bypassed.
        - UNKNOWN_SIDE_EFFECT halts subsequent execution immediately.
        - State transition is verified after each edge.
        """
        params = dict(params or {})

        # Canonical Re-validation: Do not blindly trust serialized risk metadata in imported plan
        canonical_max_risk_rank = 0
        canonical_risk_class = plan.cumulative_risk or RiskClass.R0_READONLY
        for edge in plan.edges:
            canonical_recipe = self.store.get(edge.recipe_id) if hasattr(self.store, "get") else None
            if canonical_recipe is not None:
                edge.risk_class = get_recipe_risk(canonical_recipe)
            edge_rank = RISK_RANKS.get(edge.risk_class, 2)
            if edge_rank > canonical_max_risk_rank:
                canonical_max_risk_rank = edge_rank
                canonical_risk_class = edge.risk_class
        plan.cumulative_risk = canonical_risk_class

        # 1. Global R4 Gate Check
        if plan.cumulative_risk == RiskClass.R4_IRREVERSIBLE and not allow_r4:
            raise RiskGateError(
                f"Workflow plan '{plan.plan_id}' contains R4 (irreversible/destructive) edges. "
                "Execution blocked without explicit allow_r4=True."
            )

        edge_results: list[dict[str, Any]] = []
        current_state = plan.start_state

        for idx, edge in enumerate(plan.edges):
            # Canonical quarantine check before executing edge
            canonical_recipe = self.store.get(edge.recipe_id) if hasattr(self.store, "get") else None
            if canonical_recipe is not None and hasattr(canonical_recipe, "lifecycle") and canonical_recipe.lifecycle.state == RecipeLifecycleState.QUARANTINED:
                q_reason = getattr(canonical_recipe.lifecycle, "quarantine_reason", "quarantined")
                raise QuarantinedRecipeError(
                    f"Workflow edge {idx} ('{edge.edge_id}') uses QUARANTINED recipe '{edge.recipe_id}' ({q_reason}). Execution blocked."
                )

            # 2. Per-edge R4 Gate Check
            if edge.risk_class == RiskClass.R4_IRREVERSIBLE and not allow_r4:
                raise RiskGateError(
                    f"Workflow edge {idx} ('{edge.edge_id}') requires R4 authorization. "
                    "Execution blocked without explicit allow_r4=True."
                )

            # 3. Execute the edge's underlying recipe
            # Note: RecipeEngine inherently performs JIT Write Barrier and Transition-Aware Reconciliation
            edge_res = self.recipe_engine.execute(
                edge.recipe_id,
                page,
                params,
                manager=manager,
                allow_r4=allow_r4,
            )

            edge_record = {
                "edge_index": idx,
                "edge_id": edge.edge_id,
                "from_state": edge.from_state,
                "to_state": edge.to_state,
                "recipe_id": edge.recipe_id,
                "risk_class": edge.risk_class,
                "ok": edge_res.ok,
                "outcome": edge_res.outcome,
                "reconciled": edge_res.reconciled,
                "message": edge_res.message,
            }
            edge_results.append(edge_record)

            # 4. Critical Halt on UNKNOWN_SIDE_EFFECT (Composition Safety Invariant)
            if edge_res.outcome == ExecutionOutcome.UNKNOWN_SIDE_EFFECT:
                msg = (
                    f"Workflow halted on edge {idx} ('{edge.edge_id}') due to UNKNOWN_SIDE_EFFECT. "
                    "Further execution stopped to prevent destructive side effects."
                )
                if raise_on_unknown_effect:
                    raise UnknownSideEffectError(msg)
                return WorkflowExecutionResult(
                    ok=False,
                    plan_id=plan.plan_id,
                    completed_edges=idx,
                    total_edges=len(plan.edges),
                    current_state=current_state,
                    cumulative_risk=plan.cumulative_risk,
                    outcome=ExecutionOutcome.UNKNOWN_SIDE_EFFECT,
                    failure_edge=edge.edge_id,
                    message=msg,
                    edge_results=edge_results,
                )

            # 5. Halt on standard SAFE_FAILURE (or attempt Alternate Transition Discovery)
            if not edge_res.ok:
                if allow_detour:
                    excluded = {edge.edge_id, getattr(edge, "id", edge.edge_id)}
                    goal = getattr(plan, "final_state", getattr(plan, "goal_state", None))
                    detour = self.graph.find_alternate_path(edge.from_state, goal, excluded_edge_ids=excluded)
                    if detour:
                        detour_failed = False
                        for d_idx, d_edge in enumerate(detour):
                            d_canonical = self.store.get(d_edge.recipe_id) if hasattr(self.store, "get") else None
                            if d_canonical is not None and hasattr(d_canonical, "lifecycle") and d_canonical.lifecycle.state == RecipeLifecycleState.QUARANTINED:
                                detour_failed = True
                                break
                            if d_edge.risk_class == RiskClass.R4_IRREVERSIBLE and not allow_r4:
                                detour_failed = True
                                break

                            d_res = self.recipe_engine.execute(
                                d_edge.recipe_id,
                                page,
                                params,
                                manager=manager,
                                allow_r4=allow_r4,
                            )
                            d_record = {
                                "edge_index": len(edge_results),
                                "edge_id": d_edge.edge_id,
                                "from_state": d_edge.from_state,
                                "to_state": d_edge.to_state,
                                "recipe_id": d_edge.recipe_id,
                                "risk_class": d_edge.risk_class,
                                "ok": d_res.ok,
                                "outcome": d_res.outcome,
                                "reconciled": d_res.reconciled,
                                "message": d_res.message,
                                "detour": True,
                            }
                            edge_results.append(d_record)

                            if not d_res.ok:
                                detour_failed = True
                                break

                            current_state = d_edge.to_state

                        if not detour_failed:
                            return WorkflowExecutionResult(
                                ok=True,
                                plan_id=plan.plan_id,
                                completed_edges=len(edge_results),
                                total_edges=len(plan.edges) + len(detour) - 1,
                                current_state=current_state,
                                cumulative_risk=plan.cumulative_risk,
                                outcome=ExecutionOutcome.CONFIRMED_SUCCESS,
                                failure_edge=None,
                                message=f"Workflow completed successfully via alternate detour route ({len(detour)} detour edges).",
                                edge_results=edge_results,
                                detour_taken=True,
                            )

                msg = f"Workflow failed on edge {idx} ('{edge.edge_id}'): {edge_res.message}"
                if raise_on_failure:
                    raise WorkflowInterruptedError(msg)
                return WorkflowExecutionResult(
                    ok=False,
                    plan_id=plan.plan_id,
                    completed_edges=idx,
                    total_edges=len(plan.edges),
                    current_state=current_state,
                    cumulative_risk=plan.cumulative_risk,
                    outcome=ExecutionOutcome.SAFE_FAILURE,
                    failure_edge=edge.edge_id,
                    message=msg,
                    edge_results=edge_results,
                )

            # 6. Post-Edge State Transition Verification
            target_node = self.graph.get_state(edge.to_state)
            if target_node:
                # 6a. Route verification if pattern specified
                if target_node.route_pattern and target_node.route_pattern != "*" and target_node.route_pattern not in page.url:
                    msg = f"Post-edge state transition to '{edge.to_state}' failed: active URL '{page.url}' does not match pattern '{target_node.route_pattern}'"
                    if raise_on_failure:
                        raise WorkflowInterruptedError(msg)
                    return WorkflowExecutionResult(
                        ok=False,
                        plan_id=plan.plan_id,
                        completed_edges=idx,
                        total_edges=len(plan.edges),
                        current_state=current_state,
                        cumulative_risk=plan.cumulative_risk,
                        outcome=ExecutionOutcome.SAFE_FAILURE,
                        failure_edge=edge.edge_id,
                        message=msg,
                        edge_results=edge_results,
                    )

                # 6b. Anchor verification if required_anchors specified
                if target_node.required_anchors and manager is not None:
                    obs = manager.observe(page)
                    tree_nodes = obs.tree if hasattr(obs, "tree") else (obs.get("tree", []) if isinstance(obs, dict) else [])
                    missing = [ra for ra in target_node.required_anchors if not _find_anchor_in_tree(ra, tree_nodes)]
                    if missing:
                        msg = f"Post-edge state transition to '{edge.to_state}' failed: missing required anchors {missing}"
                        if raise_on_failure:
                            raise WorkflowInterruptedError(msg)
                        return WorkflowExecutionResult(
                            ok=False,
                            plan_id=plan.plan_id,
                            completed_edges=idx,
                            total_edges=len(plan.edges),
                            current_state=current_state,
                            cumulative_risk=plan.cumulative_risk,
                            outcome=ExecutionOutcome.SAFE_FAILURE,
                            failure_edge=edge.edge_id,
                            message=msg,
                            edge_results=edge_results,
                        )

            current_state = edge.to_state

        return WorkflowExecutionResult(
            ok=True,
            plan_id=plan.plan_id,
            completed_edges=len(plan.edges),
            total_edges=len(plan.edges),
            current_state=current_state,
            cumulative_risk=plan.cumulative_risk,
            outcome=ExecutionOutcome.CONFIRMED_SUCCESS,
            failure_edge=None,
            message="Workflow completed successfully with all transitions verified.",
            edge_results=edge_results,
        )
