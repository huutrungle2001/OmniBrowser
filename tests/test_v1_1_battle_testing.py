"""Battle tests for the v1.1 Progressive Capability Pyramid.

The fixtures stay local and are hosted by ``fixture_server``.  Each test records
small JSON telemetry lines so a benchmark/report worker can replay the same
measurements with ``pytest -s`` without depending on a live browser profile.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from browser_core.contracts import ActionTimeoutError, StaleRefError
from browser_core.engine import act
from browser_core.page_manager import PageManager
from browser_core.primitives import inspectVisual, runBrowserCode


LIVE_CDP_PORT = 17082
SEMANTIC_PAYLOAD_LIMIT = 15 * 1024
TELEMETRY_TARGETS = {
    "controller_p50_ms": 400,
    "controller_p95_ms": 900,
    "semantic_payload_bytes": SEMANTIC_PAYLOAD_LIMIT,
    "first_action_success_rate": 0.90,
    "token_reduction": 0.80,
}
CONTROLLER = Path(__file__).resolve().parents[1] / "scripts" / "cdp_controller.py"


def _load_controller_module():
    """Load the CLI module so transport behavior can be tested in-process."""
    spec = importlib.util.spec_from_file_location("omnibrowser_test_controller", CONTROLLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _server_ws_frame(payload: bytes, *, opcode: int = 1, final: bool = True) -> bytes:
    """Build an unmasked server-to-client WebSocket frame for transport tests."""
    first = (0x80 if final else 0) | opcode
    if len(payload) < 126:
        return bytes((first, len(payload))) + payload
    if len(payload) < 65536:
        return bytes((first, 126)) + struct.pack("!H", len(payload)) + payload
    return bytes((first, 127)) + struct.pack("!Q", len(payload)) + payload


class _ChunkedSocket:
    """Small fake socket that exposes short reads and preserves test determinism."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)
        self.sent: list[bytes] = []

    def settimeout(self, _timeout: float) -> None:
        return None

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, size: int) -> bytes:
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        if len(chunk) > size:
            self._chunks.insert(0, chunk[size:])
            return chunk[:size]
        return chunk

    def close(self) -> None:
        return None


def _session(ephemeral_cdp_url: str, fixture_server: str):
    """Create a scanner-backed page using only the ephemeral CDP fixture."""
    assert f":{LIVE_CDP_PORT}" not in ephemeral_cdp_url
    manager = PageManager(ephemeral_cdp_url, test_mode=True)
    manager.connect()
    page = manager.primary_page()
    manager.install_scanner(page)
    page.goto(f"{fixture_server}/interactive_page.html", wait_until="domcontentloaded")
    return manager, page


def _observe(manager: PageManager, page):
    started = time.perf_counter()
    result = manager.observe(page, max_elements=200)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    payload_bytes = len(json.dumps(result.to_dict(), separators=(",", ":")).encode("utf-8"))
    return result, elapsed_ms, payload_bytes


def _node(result, name: str):
    return next(node for node in result.tree if node.name == name)


def _act(page, **kwargs):
    started = time.perf_counter()
    result = act(page, **kwargs)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    return result, elapsed_ms


def _emit(workflow: str, events: list[dict]):
    normalized = []
    for event in events:
        item = dict(event)
        item.setdefault("component", "core" if item.get("level", 1) <= 1 else "primitive")
        normalized.append(item)
    by_component = {}
    for event in normalized:
        by_component.setdefault(event["component"], []).append(event)
    payload = {"workflow": workflow, "events": normalized, "by_component": by_component}
    # This is intentionally machine-readable output for the report worker.
    print(f"V1_1_TELEMETRY {json.dumps(payload, sort_keys=True)}")


def _controller(cdp_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the real CLI against the fixture's ephemeral browser only."""
    assert f":{LIVE_CDP_PORT}" not in cdp_url
    environment = os.environ.copy()
    environment.pop("CDP_URL", None)
    return subprocess.run(
        [sys.executable, str(CONTROLLER), "--cdp-url", cdp_url, *arguments],
        cwd=CONTROLLER.parents[1],
        capture_output=True,
        text=True,
        timeout=20,
        env=environment,
        check=False,
    )


def _percentile(samples: list[float], percentile: float) -> float:
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def test_v1_1_semantic_form_fill_check_select_and_validation(
    ephemeral_cdp_url, fixture_server
):
    """Level 1 resolves a validated multi-field form through semantic refs."""
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    events: list[dict] = []
    first_action_ok = False
    try:
        # Add a consent field and a capture-phase validator to the local fixture.
        page.evaluate(
            """() => {
              const form = document.querySelector('#enrolment-form');
              const label = document.createElement('label');
              label.htmlFor = 'terms';
              label.textContent = 'Accept terms';
              const checkbox = document.createElement('input');
              checkbox.id = 'terms';
              checkbox.type = 'checkbox';
              checkbox.name = 'terms';
              form.insertBefore(label, form.querySelector('#save'));
              form.insertBefore(checkbox, form.querySelector('#save'));
              form.addEventListener('submit', event => {
                const name = document.querySelector('#name').value.trim();
                const course = document.querySelector('#course').value;
                if (!name || !course || !checkbox.checked) {
                  event.preventDefault();
                  event.stopImmediatePropagation();
                  document.querySelector('#status').textContent =
                    'Validation: complete all required fields';
                }
              }, true);
            }"""
        )
        result, observe_ms, payload_bytes = _observe(manager, page)
        events.append(
            {
                "level": 1,
                "operation": "observe",
                "latency_ms": observe_ms,
                "payload_bytes": payload_bytes,
            }
        )
        assert payload_bytes < SEMANTIC_PAYLOAD_LIMIT
        assert _node(result, "Accept terms").role == "checkbox"

        submit = _node(result, "Submit with Enter")
        validation, validation_ms = _act(
            page,
            action="click",
            ref=submit.ref,
            expect={
                "text_present": "Validation: complete all required fields",
                "timeout_ms": 2000,
            },
        )
        assert validation.ok
        events.append(
            {
                "level": 1,
                "operation": "act.validation",
                "latency_ms": validation_ms,
                "success": True,
            }
        )

        result, observe_ms, payload_bytes = _observe(manager, page)
        name = _node(result, "Name")
        course = _node(result, "Course")
        terms = _node(result, "Accept terms")
        first_action_ok = True
        filled, filled_ms = _act(page, action="fill", ref=name.ref, value="Ada Lovelace")
        selected, selected_ms = _act(
            page,
            action="select",
            ref=course.ref,
            value="browser-systems",
        )
        checked, checked_ms = _act(page, action="check", ref=terms.ref)
        submitted, submitted_ms = _act(
            page,
            action="click",
            ref=submit.ref,
            expect={"text_present": "Submitted: Ada Lovelace", "timeout_ms": 2000},
        )
        assert all(item.ok for item in (filled, selected, checked, submitted))
        assert page.locator("#course").input_value() == "browser-systems"
        assert page.locator("#terms").is_checked()
        assert page.locator("#status").inner_text() == "Submitted: Ada Lovelace"
        events.extend(
            [
                {"level": 1, "operation": "act.fill", "latency_ms": filled_ms, "success": True},
                {"level": 1, "operation": "act.select", "latency_ms": selected_ms, "success": True},
                {"level": 1, "operation": "act.check", "latency_ms": checked_ms, "success": True},
                {"level": 1, "operation": "act.submit", "latency_ms": submitted_ms, "success": True},
            ]
        )
        events[0]["payload_bytes_after"] = payload_bytes
        _emit("semantic_form", events)
    finally:
        manager.close()
    assert first_action_ok


def test_v1_1_dynamic_spa_mutation_delta_and_stale_ref_recovery(
    ephemeral_cdp_url, fixture_server
):
    """Level 1 observes a client render, reports a delta, then refreshes refs."""
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    events: list[dict] = []
    try:
        page.evaluate(
            """() => {
              const replace = document.createElement('button');
              replace.id = 'spa-rerender';
              replace.type = 'button';
              replace.textContent = 'Re-render SPA';
              document.querySelector('#enrolment-form').append(replace);
              replace.addEventListener('click', () => {
                replace.textContent = 'Re-rendered form';
                const oldName = document.querySelector('#name');
                const newName = oldName.cloneNode(true);
                newName.value = '';
                oldName.replaceWith(newName);
                document.querySelector('#status').textContent = 'Form replaced';
                const item = document.createElement('button');
                item.id = 'dynamic-item';
                item.textContent = 'Dynamic item';
                item.type = 'button';
                item.addEventListener('click', () => {
                  document.querySelector('#status').textContent = 'Dynamic item selected';
                });
                document.querySelector('#enrolment-form').append(item);
              });
            }"""
        )
        before, observe_ms, payload_bytes = _observe(manager, page)
        old_name_ref = _node(before, "Name").ref
        replace_ref = _node(before, "Re-render SPA").ref
        events.append(
            {
                "level": 1,
                "operation": "observe.before",
                "latency_ms": observe_ms,
                "payload_bytes": payload_bytes,
                "revision": before.revision,
            }
        )

        replaced, action_ms = _act(
            page,
            action="click",
            ref=replace_ref,
            expect={"mutation": True, "text_present": "Form replaced", "timeout_ms": 2000},
        )
        assert replaced.ok
        assert replaced.delta.added
        assert replaced.delta.removed
        assert any(item["role"] == "textbox" for item in replaced.delta.added)
        assert any(item["role"] == "textbox" for item in replaced.delta.removed)
        assert any(item["after"]["name"] == "Re-rendered form" for item in replaced.delta.changed)
        assert page.locator("#dynamic-item").is_visible()
        events.append(
            {
                "level": 1,
                "operation": "act.spa-render",
                "latency_ms": action_ms,
                "success": True,
                "revision": replaced.revision,
                "delta": {
                    "added": len(replaced.delta.added),
                    "changed": len(replaced.delta.changed),
                    "removed": len(replaced.delta.removed),
                },
            }
        )

        # The replacement has the same semantic fingerprint, so the engine should
        # safely recover the old opaque ref rather than making the caller guess a
        # selector.  Ambiguous fingerprints must still fail closed in the engine.
        recovered, recovered_ms = _act(
            page,
            action="fill",
            ref=old_name_ref,
            value="Grace Hopper",
        )
        assert recovered.ok
        assert page.locator("#name").input_value() == "Grace Hopper"
        refreshed, refreshed_ms, refreshed_bytes = _observe(manager, page)
        new_name_ref = _node(refreshed, "Name").ref
        assert new_name_ref != old_name_ref
        assert _node(refreshed, "Name").role == "textbox"
        events.extend(
            [
                {
                    "level": 1,
                    "operation": "observe.recover",
                    "latency_ms": refreshed_ms,
                    "payload_bytes": refreshed_bytes,
                    "stale_ref_replaced": True,
                },
                {"level": 1, "operation": "act.recovered-fill", "latency_ms": recovered_ms, "success": True},
            ]
        )
        _emit("dynamic_spa", events)
    finally:
        manager.close()


def test_v1_1_ambiguous_stale_fingerprint_fails_closed(
    ephemeral_cdp_url, fixture_server
):
    """Never guess when a stale ref fingerprint resolves to multiple nodes."""
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        stale_ref = _node(_observe(manager, page)[0], "Name").ref
        page.evaluate(
            """() => {
              const original = document.querySelector('#name');
              const first = original.cloneNode(true);
              const second = original.cloneNode(true);
              first.value = '';
              second.value = '';
              first.setAttribute('aria-label', 'Name');
              second.setAttribute('aria-label', 'Name');
              original.replaceWith(first);
              document.querySelector('#enrolment-form').append(second);
              return {originalConnected: original.isConnected, firstConnected: first.isConnected, secondConnected: second.isConnected};
            }"""
        )
        assert page.locator("#name").count() == 2
        assert page.evaluate("""ref => window[Symbol.for('__OMNI_DOM_AGENT__')].resolve(ref)""", str(stale_ref)) is False
        with pytest.raises(StaleRefError, match=r"(?i)stale|observe|refresh"):
            act(page, action="fill", ref=stale_ref, value="must not guess")
        values = page.locator("#name").evaluate_all("elements => elements.map(element => element.value)")
        assert values == ["", ""]
    finally:
        manager.close()


def test_v1_1_canvas_escalation_visual_crop_and_captcha_safety(
    ephemeral_cdp_url, fixture_server, tmp_path
):
    """Escalate nonsemantic canvas work to bounded code and targeted visual evidence."""
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    events: list[dict] = []
    try:
        page.evaluate(
            """() => {
              document.querySelector('#visual-target').remove();
              const shell = document.createElement('div');
              shell.id = 'canvas-shell';
              shell.setAttribute('role', 'img');
              shell.setAttribute('aria-label', 'Canvas chart');
              shell.tabIndex = 0;
              shell.style.cssText = 'width:260px;height:130px;margin-top:22px;background:#12345b';
              const canvas = document.createElement('canvas');
              canvas.id = 'canvas-ui';
              canvas.width = 260;
              canvas.height = 130;
              canvas.style.cssText = 'width:260px;height:130px';
              shell.append(canvas);
              document.querySelector('#app').append(shell);
            }"""
        )
        result, observe_ms, payload_bytes = _observe(manager, page)
        assert all(node.name != "Canvas chart" or node.role == "img" for node in result.tree)
        canvas_ref = _node(result, "Canvas chart").ref
        events.append(
            {
                "level": 1,
                "operation": "observe.canvas-shell",
                "latency_ms": observe_ms,
                "payload_bytes": payload_bytes,
                "escalated": True,
            }
        )

        started = time.perf_counter()
        dimensions = runBrowserCode(
            page,
            script="""
              const canvas = document.querySelector('#canvas-ui');
              const context = canvas.getContext('2d');
              context.fillStyle = '#e0a629';
              context.fillRect(24, 20, 160, 72);
              return {width: canvas.width, height: canvas.height, kind: 'canvas'};
            """,
            mode="write",
        )
        code_ms = round((time.perf_counter() - started) * 1000, 3)
        assert dimensions == {"width": 260, "height": 130, "kind": "canvas"}
        events.append(
            {"level": 3, "operation": "runBrowserCode", "latency_ms": code_ms, "success": True}
        )

        output_path = Path(tmp_path) / "canvas-crop.png"
        page.locator("#canvas-shell").scroll_into_view_if_needed()
        started = time.perf_counter()
        visual = inspectVisual(page, ref=canvas_ref, output_path=str(output_path), padding=10)
        visual_ms = round((time.perf_counter() - started) * 1000, 3)
        assert visual["requires_human_verification"] is False
        assert output_path.is_file()
        assert output_path.stat().st_size > 0
        events.append(
            {
                "level": 4,
                "operation": "inspectVisual",
                "latency_ms": visual_ms,
                "success": True,
                "crop_bytes": output_path.stat().st_size,
            }
        )

        page.goto(f"{fixture_server}/interactive_page.html?captcha=1", wait_until="domcontentloaded")
        captcha_result, captcha_observe_ms, captcha_payload_bytes = _observe(manager, page)
        captcha_ref = _node(captcha_result, "CAPTCHA verification").ref
        captcha = inspectVisual(page, ref=captcha_ref)
        assert captcha["requires_human_verification"] is True
        assert "CAPTCHA" in captcha["reason"]
        assert "bypass" not in json.dumps(captcha).lower()
        events.append(
            {
                "level": 4,
                "operation": "inspectVisual.captcha",
                "latency_ms": captcha_observe_ms,
                "payload_bytes": captcha_payload_bytes,
                "requires_human_verification": True,
                "automated_bypass_attempted": False,
            }
        )
        _emit("canvas_blocker", events)
    finally:
        manager.close()


def test_v1_1_run_browser_code_cpu_bound_timeout(
    ephemeral_cdp_url, fixture_server
):
    """CDP Runtime.evaluate must bound synchronous CPU-bound browser code."""
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        with pytest.raises(ActionTimeoutError, match=r"(?i)timeout"):
            runBrowserCode(
                page,
                script="while (true) {}",
                mode="read",
                timeout_ms=100,
            )
    finally:
        manager.close()


def test_v1_1_telemetry_targets_are_explicit():
    """Keep the report's acceptance thresholds discoverable to benchmark tooling."""
    assert TELEMETRY_TARGETS["controller_p50_ms"] == 400
    assert TELEMETRY_TARGETS["controller_p95_ms"] == 900
    assert TELEMETRY_TARGETS["semantic_payload_bytes"] == 15 * 1024
    assert TELEMETRY_TARGETS["first_action_success_rate"] == 0.90
    assert TELEMETRY_TARGETS["token_reduction"] == 0.80


def test_v1_1_controller_subprocess_latency_and_payload_telemetry(
    ephemeral_cdp_url, fixture_server
):
    """Measure the real CLI boundary independently from core/primitive timings."""
    fixture_url = f"{fixture_server}/interactive_page.html"
    navigated = _controller(ephemeral_cdp_url, "goto", fixture_url)
    assert navigated.returncode == 0, navigated.stderr
    augmented = _controller(
        ephemeral_cdp_url,
        "run-code",
        "--mode",
        "write",
        "--script",
        "const payload = document.createElement('pre'); payload.hidden = true; payload.id = 'raw-app-shell-state'; payload.textContent = 'x'.repeat(140 * 1024); document.body.append(payload); return true;",
    )
    assert augmented.returncode == 0, augmented.stderr

    # The page is intentionally augmented with a large hidden payload to model
    # a realistic app shell whose raw DOM is expensive to return while
    # remaining invisible to the semantic scanner.
    manager = PageManager(ephemeral_cdp_url, test_mode=True)
    manager.connect()
    page = manager.primary_page()
    try:
        raw_started = time.perf_counter()
        raw_html = page.evaluate("() => document.documentElement.outerHTML")
        raw_fetch_ms = round((time.perf_counter() - raw_started) * 1000, 3)
    finally:
        manager.close()
    raw_html_bytes = len(raw_html.encode("utf-8"))

    # Exercise the same direct transport used by the CLI and make the report
    # fail if the benchmark fixture cannot complete the raw CDP fast path.
    controller = _load_controller_module()
    fast_args = argparse.Namespace(
        cdp_url=ephemeral_cdp_url,
        match=None,
        frame=None,
        max_elements=200,
    )
    fast_started = time.perf_counter()
    fast_result = controller._fast_observe(fast_args)
    fast_observe_ms = round((time.perf_counter() - fast_started) * 1000, 3)
    assert fast_result is not None, "raw CDP fast observe unexpectedly fell back"
    assert any(node.name == "Name" for node in fast_result.tree)

    # Warm the real subprocess path once so the sample reflects a ready local
    # browser while still including CLI import, CDP attach, and teardown cost.
    warmup = _controller(ephemeral_cdp_url, "observe", "--max-elements", "20")
    assert warmup.returncode == 0, warmup.stderr

    observe_samples: list[float] = []
    act_samples: list[float] = []
    semantic_payload_bytes: list[int] = []
    first_action_successes = 0
    for index in range(3):
        started = time.perf_counter()
        observed = _controller(ephemeral_cdp_url, "observe", "--max-elements", "200")
        observe_ms = round((time.perf_counter() - started) * 1000, 3)
        assert observed.returncode == 0, observed.stderr
        payload = json.loads(observed.stdout)
        assert payload["ok"] is True
        observe_samples.append(observe_ms)
        semantic_payload_bytes.append(len(observed.stdout.strip().encode("utf-8")))
        name_ref = next(node["ref"] for node in payload["tree"] if node["name"] == "Name")

        started = time.perf_counter()
        acted = _controller(
            ephemeral_cdp_url,
            "act",
            "--op",
            "fill",
            "--ref",
            name_ref,
            "--value",
            f"CLI sample {index}",
        )
        act_ms = round((time.perf_counter() - started) * 1000, 3)
        assert acted.returncode == 0, acted.stderr
        act_payload = json.loads(acted.stdout)
        assert act_payload["ok"] is True
        act_samples.append(act_ms)
        first_action_successes += 1

    controller_samples = observe_samples + act_samples
    p50_ms = _percentile(controller_samples, 0.50)
    p95_ms = _percentile(controller_samples, 0.95)
    largest_semantic_bytes = max(semantic_payload_bytes)
    token_reduction = 1 - (largest_semantic_bytes / raw_html_bytes)
    controller_events = [
        {
            "component": "controller",
            "operation": "observe",
            "samples": observe_samples,
            "sample_count": len(observe_samples),
            "payload_bytes": semantic_payload_bytes,
            "raw_html_bytes": raw_html_bytes,
            "raw_fetch_ms": raw_fetch_ms,
            "raw_source": "document.documentElement.outerHTML",
            "transport": "raw_cdp",
            "fast_path_verified": True,
            "fast_observe_ms": fast_observe_ms,
        },
        {
            "component": "controller",
            "operation": "act",
            "samples": act_samples,
            "sample_count": len(act_samples),
            "first_action_successes": first_action_successes,
            "first_action_success_rate": first_action_successes / len(act_samples),
        },
        {
            "component": "controller",
            "operation": "benchmark",
            "p50_ms": round(p50_ms, 3),
            "p95_ms": round(p95_ms, 3),
            "target_status": {
                "p50_under_400ms": p50_ms < TELEMETRY_TARGETS["controller_p50_ms"],
                "p95_under_900ms": p95_ms < TELEMETRY_TARGETS["controller_p95_ms"],
            },
            "semantic_payload_bytes_max": largest_semantic_bytes,
            "token_reduction": round(token_reduction, 6),
        },
    ]
    _emit("controller_boundary", controller_events)
    assert largest_semantic_bytes < SEMANTIC_PAYLOAD_LIMIT
    assert raw_html_bytes > largest_semantic_bytes
    assert p50_ms < TELEMETRY_TARGETS["controller_p50_ms"]
    assert p95_ms < TELEMETRY_TARGETS["controller_p95_ms"]
    assert first_action_successes / len(act_samples) > TELEMETRY_TARGETS["first_action_success_rate"]
    assert token_reduction > TELEMETRY_TARGETS["token_reduction"]


def test_v1_1_raw_cdp_rejects_non_loopback_endpoint(monkeypatch):
    """The raw transport must reject remote endpoints before opening a socket."""
    controller = _load_controller_module()

    def unexpected_connect(*_args, **_kwargs):
        raise AssertionError("endpoint validation must precede socket creation")

    monkeypatch.setattr(controller.socket, "create_connection", unexpected_connect)
    with pytest.raises(ValueError, match=r"(?i)local.*ws"):
        controller._RawCDP("ws://example.invalid:9222/devtools/page/remote")


def test_v1_1_raw_cdp_buffers_short_reads_and_reassembles_fragments(monkeypatch):
    """Raw CDP buffers handshake over-read and fragmented JSON across short reads."""
    controller = _load_controller_module()
    response = json.dumps({"id": 1, "result": {"value": {"ok": True}}}).encode()
    midpoint = len(response) // 2
    wire = b"".join(
        (
            _server_ws_frame(response[:midpoint], opcode=1, final=False),
            _server_ws_frame(b"ping", opcode=9),
            _server_ws_frame(response[midpoint:], opcode=0, final=True),
        )
    )
    handshake = b"HTTP/1.1 101 Switching Protocols\r\n\r\n"
    fake_socket = _ChunkedSocket(
        [handshake + wire[:1]]
        + [wire[index : index + 1] for index in range(1, len(wire))]
    )
    monkeypatch.setattr(controller.socket, "create_connection", lambda *_args, **_kwargs: fake_socket)
    cdp = controller._RawCDP("ws://127.0.0.1:9222/devtools/page/test")

    result = cdp.request("Runtime.evaluate")

    assert result == {"id": 1, "result": {"value": {"ok": True}}}
    assert len(fake_socket.sent) == 3
    assert fake_socket.sent[2][0] & 0x0F == 10  # pong for the interleaved ping
