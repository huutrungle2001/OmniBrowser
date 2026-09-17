"""Ephemeral-Chrome integration tests for inspection and escape hatches."""

from __future__ import annotations

import json

import pytest
from PIL import Image

from browser_core.contracts import ActionTimeoutError
from browser_core.page_manager import PageManager
from browser_core.primitives import inspect, inspectVisual, runBrowserCode


def _session(ephemeral_cdp_url, fixture_server):
    manager = PageManager(ephemeral_cdp_url, test_mode=True)
    manager.connect()
    page = manager.primary_page()
    manager.install_scanner(page)
    page.goto(f"{fixture_server}/interactive_page.html", wait_until="domcontentloaded")
    return manager, page


def _node(manager, page, name):
    return next(node for node in manager.observe(page).tree if node.name == name)


def test_inspect_dom_ax_and_frame_tree_are_bounded(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        name_ref = _node(manager, page, "Name").ref
        dom = inspect(page, mode="dom", ref=name_ref, depth=2)
        ax = inspect(page, mode="ax", ref=name_ref, depth=2)
        frames = inspect(page, mode="frame-tree", depth=3)

        assert isinstance(dom, str)
        assert isinstance(ax, str)
        assert isinstance(frames, str)
        assert "name" in dom.lower()
        assert "textbox" in ax.lower() or "name" in ax.lower()
        assert "interactive_page" in frames
        assert "Fixture child frame" in frames or "frame" in frames.lower()
        assert all(len(value.encode("utf-8")) <= 32 * 1024 for value in (dom, ax, frames))
    finally:
        manager.close()


def test_run_browser_code_read_and_write_modes(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        read = runBrowserCode(
            page,
            script="return ({title: document.title, ready: document.readyState});",
            mode="read",
        )
        payload = read if isinstance(read, dict) else json.loads(read)
        assert payload["title"] == "OmniBrowser interactive fixture"
        assert payload["ready"] in {"interactive", "complete"}

        written = runBrowserCode(
            page,
            script="document.querySelector('#status').textContent = 'Code wrote this'; return true;",
            mode="write",
        )
        assert written is True or written == "true" or written == {"result": True}
        assert page.locator("#status").inner_text() == "Code wrote this"
    finally:
        manager.close()


def test_run_browser_code_enforces_output_bound(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        with pytest.raises(Exception, match=r"(?i)(32\s*kb|32768|output|size|limit|large)"):
            runBrowserCode(
                page,
                script="return 'x'.repeat(40 * 1024);",
                mode="read",
            )
    finally:
        manager.close()


def test_run_browser_code_read_timeout_is_bounded(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        with pytest.raises((ActionTimeoutError, TimeoutError)) as caught:
            runBrowserCode(
                page,
                script="await new Promise(resolve => setTimeout(resolve, 2500)); return 1;",
                mode="read",
            )
        assert "timeout" in str(caught.value).lower() or isinstance(caught.value, TimeoutError)
    finally:
        manager.close()


def test_inspect_visual_crops_target_and_flags_captcha(ephemeral_cdp_url, fixture_server, tmp_path):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        output_path = tmp_path / "target.png"
        target = _node(manager, page, "Visual target")
        result = inspectVisual(page, ref=target.ref, output_path=str(output_path))
        assert result is not None
        assert output_path.exists()
        with Image.open(output_path) as image:
            width, height = image.size
        # The fixture target is 180x90 plus 24px content padding and 6px border;
        # inspectVisual adds 20px on each side, subject to viewport clipping.
        assert 240 <= width <= 270
        assert 120 <= height <= 180

        page.goto(f"{fixture_server}/interactive_page.html?captcha=1", wait_until="domcontentloaded")
        captcha = _node(manager, page, "CAPTCHA verification")
        captcha_result = inspectVisual(page, ref=captcha.ref)
        assert isinstance(captcha_result, dict)
        assert captcha_result.get("requires_human_verification") is True
    finally:
        manager.close()
