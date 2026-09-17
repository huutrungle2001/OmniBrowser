"""Playwright CDP connection and lifecycle manager for the semantic scanner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
import weakref

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from .contracts import DOMNodeRef, ObserveResult, ObservedNode, TargetNotFoundError


@dataclass
class _FrameState:
    token: str
    epoch: int = 1


class PageManager:
    """Owns a CDP connection; it never starts or owns the production browser."""

    def __init__(self, cdp_url: str, agent_source: str | None = None, *, test_mode: bool = False):
        self.cdp_url = cdp_url
        self.test_mode = test_mode
        self.agent_source = agent_source or Path(__file__).parents[2].joinpath("scripts", "dom_agent.js").read_text()
        self._playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self._frames: dict[object, _FrameState] = {}
        self._next_frame_token = 0
        self._sessions: dict[Page, object] = {}

    def connect(self) -> "PageManager":
        parsed = urlparse(self.cdp_url)
        if self.test_mode and parsed.port == 17082:
            raise ValueError("Test mode refuses the live Chrome debugging port 17082")
        self._playwright = sync_playwright().start()
        self.browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)
        self.context = self.browser.contexts[0]
        return self

    def close(self) -> None:
        if self.browser:
            self.browser.close()
            self.browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None

    def __enter__(self) -> "PageManager":
        return self.connect()

    def __exit__(self, *_: object) -> None:
        self.close()

    def primary_page(self) -> Page:
        if not self.context:
            raise RuntimeError("PageManager is not connected")
        return self.context.pages[0] if self.context.pages else self.context.new_page()

    def install_scanner(self, page: Page) -> None:
        """Register before navigation and bootstrap the current document if present."""
        session = page.context.new_cdp_session(page)
        session.send("Page.enable")
        session.send("Page.addScriptToEvaluateOnNewDocument", {"source": self.agent_source})
        self._sessions[page] = session
        _MANAGERS[page] = self
        page.on("framenavigated", self._on_frame_navigated)
        for frame in page.frames:
            self._frame_state(frame)
            self._bootstrap(frame)

    def frame_inventory(self, page: Page) -> list[dict[str, str]]:
        inventory = []
        for frame in page.frames:
            state = self._frame_state(frame)
            self._bootstrap(frame)
            inventory.append({"token": state.token, "url": frame.url, "name": frame.name})
        return inventory

    def observe(self, page: Page, *, max_elements: int = 80, frame=None) -> ObserveResult:
        frame = frame or page.main_frame
        state = self._frame_state(frame)
        self._bootstrap(frame)
        raw = frame.evaluate(
            """(maxElements) => {
                const agent = window[Symbol.for('__OMNI_DOM_AGENT__')];
                return agent.scan(maxElements);
            }""",
            max_elements,
        )
        nodes = [
            ObservedNode(
                ref=DOMNodeRef.parse(node["ref"]),
                role=node["role"],
                name=node["name"],
                bounds=tuple(node["bounds"]),
                visible=node["visible"],
                interactive=node["interactive"],
                disabled=node.get("disabled", False),
                value=node.get("value"),
            )
            for node in raw["nodes"]
        ]
        return ObserveResult(
            page_url=page.url,
            frame_id=state.token,
            document_epoch=f"d{state.epoch}",
            revision=raw["revision"],
            tree=nodes,
        )

    def resolve_ref(self, page: Page, ref: DOMNodeRef) -> bool:
        try:
            frame = self._frame_for_token(page, ref.frame)
        except TargetNotFoundError:
            return False
        state = self._frame_state(frame)
        if ref.epoch != str(state.epoch):
            return False
        self._bootstrap(frame)
        return bool(frame.evaluate(
            """(value) => window[Symbol.for('__OMNI_DOM_AGENT__')].resolve(value)""",
            str(ref),
        ))

    def resolve_element(self, page: Page, ref: DOMNodeRef):
        """Resolve an opaque reference to its live ElementHandle, if present."""
        frame = self._frame_for_token(page, ref.frame)
        state = self._frame_state(frame)
        if ref.epoch != str(state.epoch):
            return None
        self._bootstrap(frame)
        handle = frame.evaluate_handle(
            """(value) => window[Symbol.for('__OMNI_DOM_AGENT__')].resolveElement(value)""",
            str(ref),
        )
        try:
            return handle.as_element()
        finally:
            if handle.as_element() is None:
                handle.dispose()

    def _frame_for_token(self, page: Page, token: str):
        for frame in page.frames:
            if self._frame_state(frame).token == token:
                return frame
        raise TargetNotFoundError(f"No frame with token f{token}")

    def _frame_state(self, frame) -> _FrameState:
        if frame not in self._frames:
            state = _FrameState(token=str(self._next_frame_token))
            self._next_frame_token += 1
            self._frames[frame] = state
        return self._frames[frame]

    def _on_frame_navigated(self, frame) -> None:
        state = self._frame_state(frame)
        state.epoch += 1

    def _bootstrap(self, frame) -> None:
        state = self._frame_state(frame)
        try:
            frame.evaluate(self.agent_source)
            frame.evaluate(
                """({frame, epoch}) =>
                    window[Symbol.for('__OMNI_DOM_AGENT__')].setContext(frame, epoch)""",
                {"frame": state.token, "epoch": state.epoch},
            )
        except Exception as error:
            raise TargetNotFoundError(f"Could not bootstrap scanner in frame: {frame.url}") from error


_MANAGERS: "weakref.WeakKeyDictionary[Page, PageManager]" = weakref.WeakKeyDictionary()


def manager_for_page(page: Page) -> PageManager | None:
    """Return the manager that installed the scanner on ``page``."""
    return _MANAGERS.get(page)
