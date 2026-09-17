import json

import pytest

from browser_core.contracts import ActionTimeoutError, DOMNodeRef, ExitCode, TargetNotFoundError
from browser_core.page_manager import PageManager


def test_ref_round_trip():
    ref = DOMNodeRef.parse("f0.d1.n186")
    assert str(ref) == "f0.d1.n186"
    with pytest.raises(ValueError):
        DOMNodeRef.parse("not-a-ref")
    assert TargetNotFoundError.exit_code is ExitCode.TARGET_NOT_FOUND
    assert ActionTimeoutError.exit_code is ExitCode.ACTION_TIMEOUT


def test_observe_and_resolve_ref(ephemeral_cdp_url, fixture_server):
    with PageManager(ephemeral_cdp_url, test_mode=True) as manager:
        page = manager.primary_page()
        manager.install_scanner(page)
        page.goto(f"{fixture_server}/test_page.html", wait_until="domcontentloaded")

        result = manager.observe(page)
        payload = json.dumps(result.to_dict(), separators=(",", ":")).encode("utf-8")
        assert len(payload) < 15 * 1024

        by_name = {node.name: node for node in result.tree}
        assert {"Student ID", "Course", "Continue"} <= set(by_name)
        student_id = by_name["Student ID"]
        assert student_id.role == "textbox"
        assert manager.resolve_ref(page, student_id.ref)
        assert all(node.name != "do-not-project" for node in result.tree)

        frames = manager.frame_inventory(page)
        assert len(frames) == 2
        child = next(frame for frame in page.frames if frame != page.main_frame)
        child_result = manager.observe(page, frame=child)
        assert any(node.name == "Frame Continue" for node in child_result.tree)

        previous_revision = result.revision
        page.evaluate("document.querySelector('#continue').textContent = 'Next'")
        assert manager.observe(page).revision > previous_revision

        page.reload(wait_until="domcontentloaded")
        reloaded = manager.observe(page)
        assert reloaded.document_epoch != result.document_epoch
        assert not manager.resolve_ref(page, student_id.ref)


def test_test_mode_refuses_live_profile_port():
    with pytest.raises(ValueError, match="17082"):
        PageManager("http://127.0.0.1:17082", test_mode=True).connect()
