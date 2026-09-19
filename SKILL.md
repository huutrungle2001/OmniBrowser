---
name: omnibrowser
description: >-
  Automate and control live Chromium browsers via Chrome DevTools Protocol (CDP) on port 17082/9222 using dedicated profiles (~/.chrome-ai-profile) and OmniBrowser's Progressive Capability Pyramid. Use this skill whenever you need to automate web pages, bypass Cloudflare/bot-detection/CAPTCHAs with real user sessions, control authenticated platforms (ChatGPT, Autodesk, AutoCAD Web, Gmail, Google Cloud, VSTEP, Codeforces), execute adaptive fast-path recipes, inspect open tabs, capture screenshots, observe semantic DOM projections, execute atomic actions, download generative images, or consult LLM web interfaces.
---

# OmniBrowser: Progressive Capability Browser Automation & Procedural Memory

OmniBrowser is an open-source, deterministic browser automation engine and procedural memory system tailored for autonomous AI agents. It connects directly to live Chromium browsers over the Chrome DevTools Protocol (CDP), providing rock-solid reliability, zero anti-bot triggering, semantic DOM observation, atomic postcondition actions, and self-healing recipes.

---

## 0. Iron Invariants & Operational Rules (MANDATORY)

1. **Absolute Prohibition on Ad-Hoc Scripts (Zero-Tolerance)**:
   - **NEVER** run `python3 -c "from playwright..."` or write ad-hoc Playwright/Puppeteer/Selenium scripts in `scratch/`, `.tmp/`, or the shell.
   - All browser operations MUST strictly follow the OmniBrowser standard pipeline:
     ```text
     list-tabs ⟶ observe (--match) ⟶ act (--action, --ref) ⟶ observe
     ```
2. **"Stop & Ask" Protocol for Unsupported Capabilities**:
   - If `cdp_controller.py` does not currently support a specialized capability (e.g., complex canvas drag-and-drop, specialized image CAPTCHA, nested cross-origin OOPIF frames), the agent **MUST STOP IMMEDIATELY** and ask the user.
   - Absolutely **NEVER** attempt to firefight or bypass limitations by generating unauthorized ad-hoc scripts.

---

## 1. Environment & Setup

* **Default CDP Port**: `17082` (fallback `9222`)
* **Dedicated User Profile**: `~/.chrome-ai-profile` (prevents polluting daily user browsing)
* **Launcher Script**: [launch_chrome.sh](./scripts/launch_chrome.sh)
* **Main Controller CLI**: [cdp_controller.py](./scripts/cdp_controller.py)
* **ChatGPT Web Oracle**: [scripts/oracle](./scripts/oracle)
* **Python Core Library**: `src/browser_core/`

### Launch Chrome (if not already running)
```bash
./scripts/launch_chrome.sh
```

---

## 2. Progressive Capability Pyramid & CLI Usage

```text
┌─────────────────────────────────────────────────────────────┐
│ LEVEL 4: Vision & Targeted Perception                       │
│ • Coordinate clicks, bounded viewport crops, visual audits  │
├─────────────────────────────────────────────────────────────┤
│ LEVEL 3: Deep Diagnostics & Network Interception            │
│ • CDP network capture, HAR logging, diagnostic DOM tree     │
├─────────────────────────────────────────────────────────────┤
│ LEVEL 2: Atomic Action Engine & Adaptive Recipes            │
│ • Native user dispatch, verified postconditions, fast-paths │
├─────────────────────────────────────────────────────────────┤
│ LEVEL 1: Semantic DOM Observation                           │
│ • Hierarchical token projection (<=15KB), opaque node refs  │
└─────────────────────────────────────────────────────────────┘
```

### 2.1 Tab Navigation & Management

```bash
# List all active browser tabs
python3 scripts/cdp_controller.py list-tabs

# Navigate active or matched tab to URL
python3 scripts/cdp_controller.py goto "https://example.com"

# Capture viewport screenshot
python3 scripts/cdp_controller.py screenshot --output /tmp/page.png
```

### 2.2 Level 1: Semantic Observation (`observe`)
Projects interactive, visible elements into a compact token stream ($\le 15$ KB) with stable opaque refs (`f<frame>.d<epoch>.n<node>`):

```bash
python3 scripts/cdp_controller.py observe
python3 scripts/cdp_controller.py observe --json
python3 scripts/cdp_controller.py observe --max-elements 50
```

### 2.3 Level 2: Atomic Actions (`act`)
Executes native user events against opaque refs (`click`, `fill`, `select`, `press`) and validates postconditions:

```bash
# Click a semantic ref
python3 scripts/cdp_controller.py act click f0.d1.n14

# Fill an input ref
python3 scripts/cdp_controller.py act fill f0.d1.n8 "my_query"

# Select an option in a dropdown
python3 scripts/cdp_controller.py act select f0.d1.n12 "OptionValue"
```

### 2.4 Level 2: Procedural Memory & Adaptive Recipes (`recipe`)
Executes declarative fast-paths for recurring workflows without LLM reasoning latency, falling back to Level 1 observe/act when UI drift occurs:

```bash
# List all stored recipes across domain folders
python3 scripts/cdp_controller.py recipe list

# Inspect recipe specification
python3 scripts/cdp_controller.py recipe show chatgpt_upload_files

# Execute recipe against active tab with optional parameter substitutions
python3 scripts/cdp_controller.py recipe run chatgpt_ask_question --params '{"prompt": "Hello world"}'

# Upload files using procedural recipe
python3 scripts/cdp_controller.py recipe run chatgpt_upload_files --params '{"files": ["/path/to/doc.pdf", "/path/to/data.yaml"]}'

# Copy latest assistant response to system clipboard (raw pristine markdown)
python3 scripts/cdp_controller.py recipe run chatgpt_copy_response
```

### 2.5 Native File Upload & Direct Controller Actions
For direct multi-file attachment without recipe parameter encoding:

```bash
# Directly upload files to #upload-files (input[type=file])
python3 scripts/cdp_controller.py --match chatgpt upload /path/to/doc1.md /path/to/doc2.yaml
```

### 2.6 Dedicated LLM Web Oracle (`scripts/oracle`) & Tab-Isolated Sessions
Orchestrates end-to-end consultations on authenticated ChatGPT sessions with strict tab isolation, persistent URLs, and tab teardown:

```bash
# List all active consultation sessions and tab states (🟢 OPEN vs ⚪ DETACHED)
./scripts/oracle session list

# Ask within a named persistent session (auto-creates thread URL in chatgpt_sessions.json)
./scripts/oracle session ask my_session "Explain this architecture" \
  -f src/browser_core/engine.py doc.md \
  --save-md .tmp/answer.md

# Follow up in the exact same thread without tab collisions or re-explaining context
./scripts/oracle session ask my_session "How to handle race conditions?"

# Close the session browser tab when finished to save RAM and clear tab clutter
./scripts/oracle session close my_session

# One-shot ask and immediate tab close
./scripts/oracle session ask quick_query "Check theorem 2" --close

# Extract latest pristine markdown answer and code blocks from active tab
./scripts/oracle extract --out /tmp/solution.py --save-md /tmp/answer.md

# Check current logged-in user profile & workspace
./scripts/oracle account

# Download all generated DALL-E / generative images
./scripts/oracle download-images --out-dir .tmp/images/
```

### 2.7 Multi-Agent Concurrency Broker & Progressive Bulkheading (`broker`, `--lease`)
Orchestrates concurrent browser leases across multi-agent systems with failure domain isolation, fencing tokens, and profile sanctity (Invariant 9):
- **Class S (Shared Multi-Context)**: Warm headless daemon with ephemeral isolated BrowserContexts for read-heavy or light tasks.
- **Class I (Isolated Dedicated Ephemeral)**: Dedicated Chrome process and isolated user-data-dir for untrusted sites or heavy DOM manipulation.
- **Class A (Authenticated & Interactive)**: Dedicated headful Chrome process with exclusive identity lock for sensitive authenticated accounts.

```bash
# Check broker subsystems status (active leases, daemons, admission telemetry)
python3 scripts/cdp_controller.py broker status

# Request a Class S lease (fast warm context)
python3 scripts/cdp_controller.py broker lease request --class S --agent-id my-agent

# Request an exclusive Class A lease with auth identity
python3 scripts/cdp_controller.py broker lease request --class A --identity "user@gmail.com" --exclusive

# Execute CLI commands scoped strictly to a lease (scoped tabs and target isolation)
python3 scripts/cdp_controller.py --lease <lease_id> list-tabs
python3 scripts/cdp_controller.py --lease <lease_id> observe
python3 scripts/cdp_controller.py --lease <lease_id> act click <ref>

# Release lease and tear down scoped context/process
python3 scripts/cdp_controller.py broker lease release <lease_id>
```

---

## 3. Python API Integration

```python
from browser_core.page_manager import PageManager
from browser_core.engine import observe, act
from browser_core.recipes import RecipeEngine, RecipeStore

# Connect to CDP
manager = PageManager("http://127.0.0.1:17082")
manager.connect()
page = manager.primary_page()

# Level 1: Observe
projection = observe(page, manager=manager)
print(projection.to_markdown())

# Level 2: Recipe fast-path
store = RecipeStore("recipes")
engine = RecipeEngine(store)
result = engine.execute("chatgpt_new_chat", page, manager=manager)
if not result.ok:
    # Automatic fallback to Level 1
    re_projection = observe(page, manager=manager)
```

---

## 4. Unbreakable Invariants

1. **Zero Anti-Bot Triggering**: Never set `navigator.webdriver = true`. Always connect via real Chrome CDP over dedicated user data directories.
2. **Deterministic Gatekeeping**: Actions must specify and verify postconditions. LLM confirmations require exit code 0 or verified DOM state.
3. **No Blind Wait Loops**: Use event-driven state checks (`wait_for`, `networkidle`, or status polling) with hard timeouts.
