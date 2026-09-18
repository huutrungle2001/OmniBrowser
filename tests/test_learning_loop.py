"""Task 006 learning-loop tests; browser tests use the isolated shared fixture."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from browser_core.engine import act
from browser_core.page_manager import PageManager
from browser_core.recipes import AnchorAmbiguous, AnchorCompiler, LearningMemory, Recipe, RecipeEngine


def _session(ephemeral_cdp_url, fixture_server):
    manager = PageManager(ephemeral_cdp_url, test_mode=True)
    manager.connect()
    page = manager.primary_page()
    manager.install_scanner(page)
    page.goto(f"{fixture_server}/interactive_page.html", wait_until="domcontentloaded")
    return manager, page


def _ref(manager, page, name):
    return next(node.ref for node in manager.observe(page).tree if node.name == name)


def _memory_text(root: Path) -> str:
    return "\n".join(path.read_bytes().decode("utf-8", errors="ignore") for path in root.rglob("*") if path.is_file())


def test_semantic_action_journal_never_persists_fill_value_or_url_query(tmp_path, ephemeral_cdp_url, fixture_server):
    memory_root = tmp_path / "memory"
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        run = LearningMemory(memory_root).begin("save an account", agent_id="agent-a")
        secret = "Correct-Horse-Battery-Staple"
        page.goto(f"{fixture_server}/interactive_page.html?code=oauth-secret", wait_until="domcontentloaded")
        act(page, "fill", _ref(manager, page, "Name"), secret, manager=manager, learn_run=run["run_id"], memory_root=str(memory_root))
        result = LearningMemory(memory_root).complete(run["run_id"], success=True)
        assert result["candidate_id"]
        stored = _memory_text(memory_root)
        assert secret not in stored
        assert "oauth-secret" not in stored
        assert "?code=" not in stored
        candidate = json.loads((memory_root / "candidates" / f"{result['candidate_id']}.json").read_text())
        assert candidate["steps"][0]["value"]["$ref"] == "input_value"
        replay = Recipe.from_dict(candidate)
        replay_result = RecipeEngine().execute(replay, page, {"input_value": "Ada"}, manager=manager)
        assert replay_result.ok is True
        assert page.locator("#name").input_value() == "Ada"
    finally:
        manager.close()


def test_learning_runs_are_isolated_and_promote_only_local_candidate(tmp_path):
    memory = LearningMemory(tmp_path / "memory")
    one = memory.begin("first", agent_id="one")
    two = memory.begin("second", agent_id="two")
    anchor = {"candidates": [{"kind": "scoped_css", "selector": "#save", "score": 0.78}]}
    memory.append_action(one["run_id"], action="click", anchor=anchor, url="https://example.test/form?token=discard")
    memory.append_action(two["run_id"], action="click", anchor=anchor, url="https://other.test/form")
    candidate = memory.complete(one["run_id"], success=True)["candidate_id"]
    assert memory.complete(two["run_id"], success=False)["candidate_id"] is None
    assert [item["id"] for item in memory.candidates("https://example.test/form")] == [candidate]
    assert memory.promote(candidate, approve=True)["status"] == "promoted"
    assert memory.suggest("https://example.test/form")[0]["candidate_id"] == candidate
    assert not (Path.cwd() / "recipes" / f"{candidate}.json").exists()


def test_anchor_compiler_refuses_ambiguous_mutating_selector(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        page.set_content("<button class='same'>one</button><button class='same'>two</button>")
        with pytest.raises(AnchorAmbiguous):
            AnchorCompiler.resolve(page, ".same", mutating=True)
        with pytest.raises(ValueError, match="eval"):
            Recipe.from_dict({
                "id": "unsafe", "name": "unsafe", "domain_pattern": "*",
                "steps": [{"action": "eval", "value": "alert(1)"}], "metadata": {"automatic": True},
            })
    finally:
        manager.close()


def test_action_url_changed_uses_pre_dispatch_url(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        result = act(page, "click", _ref(manager, page, "Continue to stage 2"),
                     expect={"url_changed": True, "timeout_ms": 2000}, manager=manager)
        assert result.ok and "step=2" in page.url
    finally:
        manager.close()


def test_cli_learning_lifecycle_and_recipe_commands_are_exposed(tmp_path):
    controller = Path(__file__).parents[1] / "scripts" / "cdp_controller.py"
    root = tmp_path / "memory"
    begin = subprocess.run(
        [sys.executable, str(controller), "learn", "begin", "--goal", "CLI run", "--memory-root", str(root)],
        cwd=controller.parents[1], text=True, capture_output=True, check=False,
    )
    assert begin.returncode == 0, begin.stderr
    run_id = json.loads(begin.stdout)["run_id"]
    # Empty successful runs are rejected rather than creating a misleading recipe.
    complete = subprocess.run(
        [sys.executable, str(controller), "learn", "complete", run_id, "--success", "--memory-root", str(root)],
        cwd=controller.parents[1], text=True, capture_output=True, check=False,
    )
    assert complete.returncode != 0
    candidates = subprocess.run(
        [sys.executable, str(controller), "recipe", "candidates", "--memory-root", str(root)],
        cwd=controller.parents[1], text=True, capture_output=True, check=False,
    )
    assert candidates.returncode == 0 and json.loads(candidates.stdout) == []
