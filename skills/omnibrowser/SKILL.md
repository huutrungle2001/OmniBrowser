---
name: omnibrowser
description: >-
  Ultra-fast local Chrome automation over CDP port 17082 featuring an API-First architecture,
  Site Playbook procedural caching, and Autonomous Authentication. Provides in-session HTTP fetch,
  direct JS evaluate, scoped DOM observation, smart clicking, batch actions, scrolling, and screenshots.
---

Prefer registered MCP `omni` or the global CLI `omni` (available globally in PATH).
Default CDP: `http://127.0.0.1:17082` (Port 17082, Host 127.0.0.1).
Playbook Repository: `sites/<domain>/playbook.json` (inside OmniBrowser root or active project root)

---

## 1. Master Execution Workflow (Chronological Order)

Every browsing task must follow these 4 chronological phases:

### Phase 1: Check Site Playbook First (Fast Path)
- **Before opening or exploring any domain `<domain>`**, check if `sites/<domain>/playbook.json` exists.
- **Cache Hit**: If present, skip blind exploration! Use the documented `api` endpoints and `auth` hints directly. This saves ~95% of turns and tokens.
- **Cache Miss / Stale**: If missing or if an endpoint returns 4xx/5xx, proceed to Phase 2 & 3 discovery.

### Phase 2: Autonomous Authentication Protocol
When hitting a login screen or session wall:
1. **1-Click SSO / Account Picker**: Click `Sign in with Google`, `Sign in with Microsoft`, or select the existing profile in account choosers.
2. **Chrome Saved Credentials**: Focus the username/password input to trigger Chrome's native autofill, verify populated fields, and submit.
3. **Autonomous OTP Hunting**: If an email verification code / OTP is triggered, query the active mailbox (Warwick Outlook or Gmail) via API to extract the code. Do not prompt the user for email codes.
4. **Physical 2FA Fallback**: ONLY pause for human intervention if physical action is required (mobile push notification like Duo, Touch ID, or image CAPTCHA).

### Phase 3: API-First Data Extraction (With UI Fallback)
- **Beware the Virtual Scroll Trap**:
  - Complex Web Apps (Outlook, Gmail, Twitter/X, Slack, Teams, Notion, Jira) use **virtualized lists** (only ~15 items exist in the DOM).
  - **NEVER loop `scroll` + `observe`** to aggregate data; unmounted nodes are lost and context is wasted.
- **Tier 1 (API-First)**:
  - Use **`fetch(url_or_path, tab_id?, ...)`**: Runs in-session HTTP requests. Inherits origin cookies, tokens, and CSRF without CORS errors.
  - Use **`evaluate(expr, tab_id?)`**: Reads internal state (`window.__INITIAL_DATA__`, `localStorage`).
- **Tier 2 (UI Fallback)**:
  - Use `observe()` + `act()` / `act_batch()` only for static forms, dialog confirmations, or discovering unknown backend routes.

### Phase 4: Self-Healing & Playbook Caching
- Once you successfully discover working endpoints or auth patterns for a site, create or update `sites/<domain>/playbook.json` following the schema below.
- **Security Invariant**: NEVER store passwords, secrets, or raw session cookies in playbooks.

---

## 2. Site Playbook Schema

Playbooks in `sites/<domain>/playbook.json` follow this compact 4-part structure:

```json
{
  "domain": "example.com",
  "updated_at": "YYYY-MM-DD",
  "auth": {
    "login_url": "https://example.com/login",
    "method": "sso" | "chrome_autofill" | "none",
    "account_hint": "user@example.com",
    "trigger_click": "<button selector>"
  },
  "api": {
    "<task_name>": {
      "path": "/api/v1/resource",
      "method": "GET" | "POST",
      "desc": "Short description of what this returns"
    }
  },
  "fallback": {
    "url": "https://example.com/page",
    "container": "<selector>",
    "item": "<selector>"
  },
  "gotchas": [
    "Key warnings (e.g. branch IDs, virtual scroll unmounting, pagination limits)"
  ]
}
```

---

## 3. Available Tools (9 Tools)

1. **`fetch(url, tab_id?, method="GET", headers?, body?)`**:
   - **API-First primitive**: Executes an in-session HTTP request directly inside the tab context via `window.fetch`.
   - Inherits origin credentials, session cookies, and CSRF tokens without CORS issues.
2. **`evaluate(expression, tab_id?)`**:
   - Evaluates arbitrary JavaScript in the tab. Returns serialized JSON.
   - Ideal for querying internal data stores, global clients, or localStorage.
3. **`list_tabs()`**: List all open browser tabs with `tab_id`, `title`, `url`.
4. **`goto(url, tab_id?, wait="load"|"commit"|"none")`**: Navigate tab or open new tab.
5. **`observe(tab_id, selector?, max_chars?)`**: Extract clean text & interactive controls with `ref`. Automatically filters Base64 blobs and ViewState tokens.
6. **`act(tab_id, action, ref?, selector?, value?, snapshot_id?)`**:
   - Actions: `click`, `fill`, `select`, `check`, `uncheck`, `press`.
   - Smart click: Auto-resolves and clicks labels for styled `opacity: 0` inputs.
7. **`act_batch(tab_id, actions, snapshot_id?, stop_on_error=true)`**:
   - Runs multiple actions sequentially in 1 turn (fill + select + submit). Reduces latency by 85%.
8. **`scroll(tab_id, ref?, selector?, direction="down"|"up", amount?)`**:
   - Scrolls the main window or inner scrollable containers (`overflow: auto`).
9. **`screenshot(tab_id, selector?, full_page=false, path?)`**:
   - Captures viewport, full page, or element to PNG.

---

## 4. CLI Quick Reference

```bash
# List tabs
omni --list-tabs

# API-First: In-session HTTP Fetch
omni --tab <TAB_ID> --fetch "/api/v2/items"
omni --tab <TAB_ID> --fetch "/api/v1/update" --method POST --body '{"active":true}'

# State Inspection & Internal Execution
omni --tab <TAB_ID> --eval 'localStorage.getItem("token")'

# UI Fallback: Observe & Action
omni --tab <TAB_ID> --selector '#main-form' --observe
omni --tab <TAB_ID> --ref r1 --act click
omni --tab <TAB_ID> --act-batch '[{"ref":"r1","action":"fill","value":"text"},{"ref":"r2","action":"click"}]'
```

