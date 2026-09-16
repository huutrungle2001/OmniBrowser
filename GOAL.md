# OmniBrowser: Master Strategic Goal & Core Mission

## 1. Core Objective
Transform rudimentary browser automation (which suffers from high latency, massive token consumption, and fragile full-screen image scraping) into **OmniBrowser**: a high-speed, token-efficient, **Progressive Capability Browser Engine** built on native Chrome DevTools Protocol (CDP) and Playwright.

The resulting system must serve as the primary browser interaction engine for AI agents across the operating system and seamlessly deploy to `~/.gemini/config/skills/browser-automation-cdp/`.

---

## 2. The Progressive Capability Pyramid

OmniBrowser implements a layered escalation model that minimizes token usage and latency by default, while providing unbreakable escape hatches for arbitrary web complexity:

```text
                     ▲
                    / \
                   /   \     Layer 4: Targeted Visual Crop (inspectVisual)
                  / Visual\  - Cropped bbox screenshots for CAPTCHAs, canvas, charts.
                 /─────────\
                / Bounded   \   Layer 3: Bounded Code Execution (runBrowserCode)
               /  JS / Py    \  - Arbitrary scripts for complex SPAs, drag-and-drop.
              /───────────────\
             /  Native Action  \   Layer 2: Atomic Actions with Postconditions (act)
            /   (Click, Fill)   \  - Click/type by opaque node ref, verify state delta.
           /─────────────────────\
          /   Semantic DOM Tree   \   Layer 1: Fast Path Semantic Projection (observe)
         /      (Opaque Refs)      \  - Compact JSON (<15KB), clean accessibility tree.
        └───────────────────────────┘
```

### Core Capabilities:
1. **Layer 1 (`observe`)**: Injects a lightweight in-browser scanner (`dom_agent.js`) that produces a compact, sanitized DOM projection with opaque refs (`f0.d9.n186`), ARIA roles, bounding boxes, and interactive flags. Strips script, style, SVG bloat, and non-interactive layout nodes.
2. **Layer 2 (`act`)**: Executes atomic actions (`click`, `fill`, `press`, `scroll`, `hover`, `select`) targeting opaque refs. Enforces pre-action visibility checks and post-action DOM/network quiescence watchers, returning state deltas in one round-trip.
3. **Layer 3 (`runBrowserCode`)**: Sandboxed in-page code execution for multi-step batch operations or complex UI interactions that would take 10 conversational turns.
4. **Layer 4 (`inspectVisual`)**: Crops high-resolution images strictly around disputed bounding boxes, completely avoiding full-screen 4K screenshot token waste.

---

## 3. Hard Engineering Invariants & Production Gates

1. **Zero Breaking Changes**:
   - Existing CLI commands (`list-tabs`, `goto`, `eval`, `screenshot`) must continue to work with identical parameter formats and output contracts.
2. **Zero Live Profile Pollution**:
   - Automated tests and benchmarks must strictly run in ephemeral Chrome sessions (`--user-data-dir` in `.tmp/`). Under no circumstances may automated test scripts connect to `~/.chrome-ai-profile` on port 17082.
3. **Payload & Size Budget**:
   - `observe` output must remain strictly bounded ($\le 15\text{ KB}$ JSON).
4. **Independent Adversarial Gate**:
   - No code is committed to production without passing independent verification from `omni_reviewer` and exiting 0 on clean integration tests.
