"""Ephemeral-Chrome integration tests for the atomic action engine."""

from __future__ import annotations

import pytest

from browser_core.contracts import StaleRefError
from browser_core.engine import act
from browser_core.page_manager import PageManager


def _session(ephemeral_cdp_url, fixture_server):
    manager = PageManager(ephemeral_cdp_url, test_mode=True)
    manager.connect()
    page = manager.primary_page()
    manager.install_scanner(page)
    page.goto(f"{fixture_server}/interactive_page.html", wait_until="domcontentloaded")
    return manager, page


def _node(manager, page, name):
    result = manager.observe(page)
    return next(node for node in result.tree if node.name == name)


def _close(manager):
    manager.close()


def test_fill_and_click_return_ok_and_dom_delta(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        name = _node(manager, page, "Name")
        filled = act(page, action="fill", ref=name.ref, value="Ada")
        assert filled.ok is True
        assert filled.revision >= 0

        saved = act(
            page,
            action="click",
            ref=_node(manager, page, "Save details").ref,
            expect={"text_present": "Saved: Ada", "timeout_ms": 2000},
        )
        assert saved.ok is True
        assert page.locator("#status").inner_text() == "Saved: Ada"
        delta = getattr(saved, "delta", None) or getattr(saved, "state_delta", None)
        assert delta is not None
    finally:
        _close(manager)


def test_select_and_press_support_native_controls(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        selected = act(
            page,
            action="select",
            ref=_node(manager, page, "Course").ref,
            value="browser-systems",
            expect={"text_present": "Browser Systems", "timeout_ms": 2000},
        )
        assert selected.ok is True
        assert page.locator("#course").input_value() == "browser-systems"

        act(page, action="fill", ref=_node(manager, page, "Name").ref, value="Grace")
        pressed = act(
            page,
            action="press",
            ref=_node(manager, page, "Name").ref,
            value="Enter",
            expect={"text_present": "Submitted: Grace", "timeout_ms": 2000},
        )
        assert pressed.ok is True
        assert page.locator("#status").inner_text() == "Submitted: Grace"
    finally:
        _close(manager)


def test_click_waits_for_url_navigation_expectation(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        result = act(
            page,
            action="click",
            ref=_node(manager, page, "Continue to stage 2").ref,
            expect={"url_matches": "step=2", "timeout_ms": 2000},
        )
        assert result.ok is True
        assert "step=2" in page.url
        assert page.locator("#status").inner_text() == "Enrolment Stage 2"
    finally:
        _close(manager)


def test_epoch_stale_ref_fails_closed_with_escalation_advice(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        stale = _node(manager, page, "Name").ref
        page.reload(wait_until="domcontentloaded")
        with pytest.raises(StaleRefError, match=r"(?i)(stale|observe|re-scan|rescan)"):
            act(page, action="fill", ref=stale, value="must fail")
        assert page.locator("#name").input_value() == ""
    finally:
        _close(manager)


def test_invalid_action_is_rejected(ephemeral_cdp_url, fixture_server):
    manager, page = _session(ephemeral_cdp_url, fixture_server)
    try:
        with pytest.raises((ValueError, TypeError)):
            act(page, action="dance", ref=_node(manager, page, "Name").ref)
    finally:
        _close(manager)
