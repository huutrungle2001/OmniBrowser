# OmniBrowser v3

> **Ultra-lightweight, deterministic Raw CDP browser automation engine and MCP Server for AI agents** (zero Playwright / Puppeteer dependencies).

OmniBrowser allows LLM agents and developers to interact directly with authenticated desktop Chrome sessions over Chrome DevTools Protocol (CDP). Designed with an **API-First** philosophy, it bypasses virtual scrolling limitations and complex bot detections by running in-session HTTP requests and smart DOM operations.

---

## Key Highlights

- **Direct Raw CDP over WebSocket**: Connects directly to Google Chrome via port `17082`. No heavy Chromium downloads, zero Playwright/Puppeteer overhead.
- **API-First In-Session `fetch()`**: Executes HTTP requests inside the browser tab context. Naturally inherits ambient session cookies, CSRF tokens, and Bearer tokens without CORS friction.
- **Smart Act & Anti-Stuck Primitives**:
  - Automatically resolves and clicks styled labels for invisible/facade inputs (`opacity: 0`, 0-size radio/checkbox).
  - Native select dropdowns modified safely via DOM properties and event dispatching, preventing OS popups from freezing headless runs.
- **Batch Actions (`act_batch`)**: Performs multi-action workflows (fill, check, select, submit) sequentially in 1 single agent turn, cutting roundtrip latency by up to 85%.
- **Signal-over-Noise Observation (`observe`)**: Strips ASP.NET `__VIEWSTATE`, Base64 image blobs, and clutter while indexing interactive elements with stable reference IDs (`r1`, `r2`...).
- **Site Playbook & Procedural Memory**: Agent skill includes procedural caching schemas (`sites/<domain>/playbook.json`) for instant fast-path navigation on repeated site visits.
- **Dual Runtime**: Use as a global CLI (`omni`) or as a standard MCP server (`omni-mcp` or `node dist/mcp.js`).

---

## Architecture & Modules

```text
src/
├── cdp.ts         # WebSocket RPC client, session routing, timeout & event handlers
├── browser.ts     # Target discovery, attach (flatten: true), lifecycle management
├── page.ts        # Evaluate, in-session fetch, smart act, act_batch, container scrolling
├── observe.ts     # Scoped DOM extraction, ref generation, ViewState & Base64 cleaner
├── artifacts.ts   # Screenshots (viewport, fullpage, scoped)
├── cli.ts         # Fast CLI command parser and JSON output
└── mcp.ts         # Stdio MCP JSON-RPC 2.0 server (9 tools)
```

---

## Quick Start & Setup

### 1. Prerequisites
- **Node.js**: v18+ (Node 20+ recommended)
- **Google Chrome** (or Chromium-based browser)

### 2. Clone & Install
```bash
git clone https://github.com/huutrungle2001/OmniBrowser.git
cd OmniBrowser

# Install runtime dependencies
npm install

# Link CLI globally (provides 'omni' and 'omni-mcp' in your terminal PATH)
npm link
```
*(Pre-compiled JavaScript is already provided in `dist/`. If you modify TypeScript source code in `src/`, run `npm run build` to re-bundle).*

### 3. Launch Chrome with Remote Debugging
OmniBrowser connects to Chrome via port `17082`. Launch Chrome with this flag:

- **macOS**:
  ```bash
  /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=17082
  ```
- **Linux**:
  ```bash
  google-chrome --remote-debugging-port=17082
  ```
- **Windows** (PowerShell):
  ```powershell
  Start-Process "chrome.exe" -ArgumentList "--remote-debugging-port=17082"
  ```

*(Tip: You can use your daily Chrome profile or a dedicated automation profile).*

---

## MCP Server Configuration

OmniBrowser exposes 9 tools for AI agents: `list_tabs`, `goto`, `observe`, `act`, `act_batch`, `fetch`, `evaluate`, `scroll`, `screenshot`.

### For Claude Desktop
Add to your `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "omni": {
      "command": "node",
      "args": ["/ABSOLUTE/PATH/TO/OmniBrowser/dist/mcp.js"],
      "env": {
        "CDP_HOST": "127.0.0.1",
        "CDP_PORT": "17082"
      }
    }
  }
}
```

### For OpenAI Codex CLI (`~/.codex/config.toml`)
```toml
[mcp_servers.omni]
command = "node"
args = ["/ABSOLUTE/PATH/TO/OmniBrowser/dist/mcp.js"]
tool_timeout_sec = 180.0

[mcp_servers.omni.env]
CDP_HOST = "127.0.0.1"
CDP_PORT = "17082"
```

### Installing the Agent Skill
To equip your AI assistant with OmniBrowser's 4-Phase API-First workflow and procedural playbook caching:
```bash
# For Codex:
mkdir -p ~/.codex/skills
ln -s "$(pwd)/skills/omnibrowser" ~/.codex/skills/omnibrowser
```

---

## CLI Quick Reference

If linked globally via `npm link`, run with `omni`. Otherwise, use `node dist/cli.js`:

```bash
# 1. Tab Discovery & Navigation
omni --list-tabs
omni --goto "https://news.ycombinator.com"
omni --tab <TAB_ID> --goto "https://example.com"

# 2. Inspecting Pages
omni --observe
omni --selector "#main-content" --observe
omni --tab <TAB_ID> --eval "document.title"

# 3. API-First In-Session Fetch (Bypasses virtual scroll & bot detections)
omni --tab <TAB_ID> --fetch "/api/v1/profile"
omni --tab <TAB_ID> --fetch "/api/v1/items" --method POST --body '{"limit": 20}'

# 4. Smart Actions & Form Filling
omni --tab <TAB_ID> --act click --ref r1
omni --tab <TAB_ID> --act fill --ref r2 --value "hello@example.com"
omni --tab <TAB_ID> --act select --ref r3 --value "vietnam"

# 5. Atomic Batch Execution (Multiple actions in 1 call)
omni --tab <TAB_ID> --act-batch '[{"ref":"r1","action":"fill","value":"my_user"},{"ref":"r2","action":"click"}]'

# 6. Container Scrolling & Screenshots
omni --tab <TAB_ID> --scroll --selector ".scrollable-box" --direction down --amount 300
omni --tab <TAB_ID> --screenshot --path /tmp/page.png
```

---

## License
MIT

