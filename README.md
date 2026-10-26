# OmniBrowser v3

Ultra-lightweight, high-performance browser automation engine for AI agents using direct Raw CDP WebSocket (zero Playwright / Puppeteer dependencies).

## Highlights & Upgrades over v2
- **Raw CDP WebSocket:** Direct connection to Chrome on port `17082` (or custom port).
- **Sub-800 Total LOC:** Complete core implementation in ~680 lines of TypeScript across 7 single-responsibility modules.
- **Direct JS Evaluation (`evaluate`):** Fast in-browser JavaScript evaluation without multi-turn roundtrips.
- **Smart Action Resolution (`smart_act`):** Automatically resolves and clicks associated labels when target inputs (radios/checkboxes) are hidden by CSS facades (`opacity: 0`, 0-size).
- **Dropdown Safety:** Native select dropdowns are modified via JavaScript properties and input/change events, preventing native OS popups from freezing automation.
- **Batch Action Execution (`act_batch`):** Sequentially executes multi-step form workflows in a single turn.
- **Signal over Noise (`observe`):** Automatically prunes ASP.NET `__VIEWSTATE`, Base64 image URIs, and supports scoped selector observation.
- **Dual Runtime Distribution:** Built into both a standalone CLI (`dist/cli.js`) and a persistent stdio MCP server (`dist/mcp.js`).

## Architecture & Modules
```text
src/
├── cdp.ts         # WebSocket RPC client, session routing, timeout & event handlers
├── browser.ts     # Target discovery, attach (flatten: true), lifecycle management
├── page.ts        # Evaluate, goto, smart act, act_batch, container scrolling
├── observe.ts     # Scoped DOM extraction, ref generation, ViewState & Base64 cleaner
├── artifacts.ts   # Screenshots (viewport, fullpage, scoped) & downloads
├── cli.ts         # Fast CLI command parser and JSON output
└── mcp.ts         # Stdio MCP JSON-RPC 2.0 server (8 tools)
```

## Quick Start

### Build
```bash
npm install
npm run build
```

### Testing
Ensure Chrome is running with remote debugging:
```bash
# e.g. /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=17082
npm test
```

### CLI Usage
```bash
# List all open tabs
node dist/cli.js --list-tabs

# Navigate to a URL (or new tab)
node dist/cli.js --goto "https://example.com" --new-tab

# Observe page content and extract controls with stable refs (r1, r2, ...)
node dist/cli.js --observe --selector "#form-container"

# Smart act on an element (click, fill, select, check, press)
node dist/cli.js --act click --ref r2
node dist/cli.js --act fill --ref r1 --value "Alice"
node dist/cli.js --act select --ref r4 --value "vn"

# Execute a batch of actions atomically
node dist/cli.js --act-batch '[{"ref":"r1","action":"fill","value":"Bob"},{"ref":"r5","action":"click"}]'

# Evaluate arbitrary JS in tab context
node dist/cli.js --eval "document.title"

# Container-aware scrolling
node dist/cli.js --scroll --selector "#inner-scroll" --direction down --amount 200

# Take screenshot
node dist/cli.js --screenshot --path /tmp/screenshot.png
```

### MCP Server Configuration
Add to your Claude / Codex MCP config:
```json
{
  "mcpServers": {
    "omnibrowser": {
      "command": "node",
      "args": ["/Users/huutrungle2001/Documents/OnGoing/OmniBrowser/dist/mcp.js"],
      "env": {
        "CDP_PORT": "17082"
      }
    }
  }
}
```

