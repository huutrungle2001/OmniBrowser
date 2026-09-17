"""Subprocess regression tests for the unified OmniBrowser CLI.

Every command connects to the CDP endpoint supplied by the ephemeral Chrome
fixture; no test uses the user's live browser profile or its fixed port.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).parents[1]
CONTROLLER = ROOT / "scripts" / "cdp_controller.py"


def _run(
    cdp_url: str,
    *args: str,
    check: bool = True,
    match: str | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(CONTROLLER), "--cdp-url", cdp_url]
    if match is not None:
        command += ["--match", match]
    command += list(args)
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        pytest.fail(
            f"CLI failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _json_output(result: subprocess.CompletedProcess[str]):
    """Parse JSON while tolerating a human-readable prefix from the CLI."""
    output = result.stdout.strip()
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        lines = [line for line in output.splitlines() if line.strip()]
        return json.loads(lines[-1])


def _goto(ephemeral_cdp_url: str, fixture_server: str) -> str:
    url = f"{fixture_server}/interactive_page.html"
    _run(ephemeral_cdp_url, "goto", url)
    return url


def _observed_tree(ephemeral_cdp_url: str, fixture_server: str) -> dict:
    _goto(ephemeral_cdp_url, fixture_server)
    result = _run(
        ephemeral_cdp_url,
        "observe",
        "--scope",
        "main",
        "--max-elements",
        "80",
        "--json",
    )
    payload = _json_output(result)
    assert payload["ok"] is True
    assert payload["tree"]
    return payload


def _ref(payload: dict, name: str) -> str:
    return next(node["ref"] for node in payload["tree"] if node["name"] == name)


def test_legacy_cli_commands_use_ephemeral_cdp(ephemeral_cdp_url, fixture_server, tmp_path):
    url = _goto(ephemeral_cdp_url, fixture_server)

    tabs = _run(ephemeral_cdp_url, "list-tabs", match="interactive_page")
    assert tabs.returncode == 0
    assert "interactive_page" in tabs.stdout
    assert url in tabs.stdout

    expression = _run(ephemeral_cdp_url, "eval", "document.title")
    assert expression.returncode == 0
    assert "OmniBrowser interactive fixture" in expression.stdout

    output_path = tmp_path / "legacy.png"
    screenshot = _run(ephemeral_cdp_url, "screenshot", "--output", str(output_path))
    assert screenshot.returncode == 0
    assert output_path.is_file()
    assert output_path.stat().st_size > 0


def test_observe_and_act_cli_round_trip(ephemeral_cdp_url, fixture_server):
    payload = _observed_tree(ephemeral_cdp_url, fixture_server)
    name_ref = _ref(payload, "Name")
    save_ref = _ref(payload, "Save details")

    filled = _run(
        ephemeral_cdp_url,
        "act",
        "--action",
        "fill",
        "--ref",
        name_ref,
        "--value",
        "Ada",
        "--json",
    )
    filled_payload = _json_output(filled)
    assert filled_payload["ok"] is True

    clicked = _run(
        ephemeral_cdp_url,
        "act",
        "--action",
        "click",
        "--ref",
        save_ref,
        "--expect",
        json.dumps({"text_present": "Saved: Ada", "timeout_ms": 2000}),
        "--json",
    )
    assert _json_output(clicked)["ok"] is True


@pytest.mark.parametrize("mode", ["dom", "ax", "frame-tree"])
def test_inspect_cli_modes_are_bounded(ephemeral_cdp_url, fixture_server, mode):
    payload = _observed_tree(ephemeral_cdp_url, fixture_server)
    args = ["inspect", mode, "--depth", "3"]
    if mode != "frame-tree":
        args += ["--ref", _ref(payload, "Name")]
    result = _run(ephemeral_cdp_url, *args)
    assert result.stdout.strip()
    assert len(result.stdout.encode("utf-8")) <= 32 * 1024


def test_run_code_cli_read_and_write(ephemeral_cdp_url, fixture_server):
    _goto(ephemeral_cdp_url, fixture_server)
    read = _run(
        ephemeral_cdp_url,
        "run-code",
        "--script",
        "return ({title: document.title, ready: document.readyState});",
    )
    read_payload = _json_output(read)
    assert read_payload["title"] == "OmniBrowser interactive fixture"
    assert read_payload["ready"] in {"interactive", "complete"}

    write = _run(
        ephemeral_cdp_url,
        "run-code",
        "--script",
        "document.querySelector('#status').textContent = 'CLI wrote this'; return true;",
        "--mode",
        "write",
    )
    assert _json_output(write) is True


def test_visual_cli_writes_targeted_crop(ephemeral_cdp_url, fixture_server, tmp_path):
    payload = _observed_tree(ephemeral_cdp_url, fixture_server)
    target_ref = _ref(payload, "Visual target")
    output_path = tmp_path / "visual.png"
    result = _run(
        ephemeral_cdp_url,
        "visual",
        "--ref",
        target_ref,
        "--output",
        str(output_path),
    )
    visual_payload = _json_output(result)
    assert visual_payload["requires_human_verification"] is False
    assert output_path.is_file()
    assert output_path.stat().st_size > 0
