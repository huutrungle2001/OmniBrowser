#!/usr/bin/env node

// src/cdp.ts
import WebSocket from "ws";
var CdpConnection = class _CdpConnection {
  ws;
  nextId = 1;
  pending = /* @__PURE__ */ new Map();
  handlers = /* @__PURE__ */ new Map();
  constructor(ws) {
    this.ws = ws;
    this.ws.on("message", (data) => this.onMessage(data.toString()));
    this.ws.on("close", () => this.onClose("WebSocket closed"));
    this.ws.on("error", (err) => this.onClose(`WebSocket error: ${err.message}`));
  }
  static async connect(host = "127.0.0.1", port = 17082, timeout = 5e3) {
    const res = await fetch(`http://${host}:${port}/json/version`, { signal: AbortSignal.timeout(timeout) });
    if (!res.ok) throw new Error(`Failed to fetch CDP version: ${res.statusText}`);
    const { webSocketDebuggerUrl } = await res.json();
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(webSocketDebuggerUrl);
      const timer = setTimeout(() => {
        ws.close();
        reject(new Error("CDP WS connection timeout"));
      }, timeout);
      ws.once("open", () => {
        clearTimeout(timer);
        resolve(new _CdpConnection(ws));
      });
      ws.once("error", (err) => {
        clearTimeout(timer);
        reject(err);
      });
    });
  }
  send(method, params = {}, sessionId, timeoutMs = 15e3) {
    return new Promise((resolve, reject) => {
      if (this.ws.readyState !== WebSocket.OPEN) return reject(new Error("CDP connection is not open"));
      const id = this.nextId++;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP command ${method} timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      const payload = { id, method, params };
      if (sessionId) payload.sessionId = sessionId;
      this.ws.send(JSON.stringify(payload), (err) => {
        if (err) {
          clearTimeout(timer);
          this.pending.delete(id);
          reject(err);
        }
      });
    });
  }
  on(method, handler) {
    if (!this.handlers.has(method)) this.handlers.set(method, /* @__PURE__ */ new Set());
    this.handlers.get(method).add(handler);
    return () => this.handlers.get(method)?.delete(handler);
  }
  onMessage(raw) {
    try {
      const msg = JSON.parse(raw);
      if (msg.id !== void 0) {
        const item = this.pending.get(msg.id);
        if (item) {
          this.pending.delete(msg.id);
          clearTimeout(item.timer);
          if (msg.error) item.reject(new Error(msg.error.message || JSON.stringify(msg.error)));
          else item.resolve(msg.result);
        }
      } else if (msg.method) {
        this.handlers.get(msg.method)?.forEach((fn) => fn(msg.params, msg.sessionId));
        this.handlers.get("*")?.forEach((fn) => fn(msg.params, msg.sessionId));
      }
    } catch {
    }
  }
  onClose(reason) {
    for (const [, item] of this.pending) {
      clearTimeout(item.timer);
      item.reject(new Error(`CDP closed: ${reason}`));
    }
    this.pending.clear();
  }
  close() {
    this.ws.close();
  }
};

// src/page.ts
function inBrowserAct(args) {
  const { ref, selector, action, value, snapshotId } = args;
  const win = window;
  if (snapshotId && win.__omni_snapshot_id && win.__omni_snapshot_id !== snapshotId) {
    return { ok: false, error: { code: "STALE_REF", message: "Reference belongs to an older observation", retryable: true } };
  }
  let el = null;
  if (ref) {
    el = win.__omni_refs ? win.__omni_refs[ref] : null;
    if (!el) return { ok: false, error: { code: "STALE_REF", message: "Element ref not found in snapshot", retryable: true } };
  } else if (selector) {
    el = document.querySelector(selector);
    if (!el) return { ok: false, error: { code: "ELEMENT_NOT_FOUND", message: "Selector not found: " + selector } };
  } else {
    return { ok: false, error: { code: "INVALID_TARGET", message: "Either ref or selector must be provided" } };
  }
  try {
    el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
  } catch {
  }
  const tag = el.tagName.toLowerCase();
  const type = (el.getAttribute("type") || "").toLowerCase();
  if (tag === "select" || action === "select") {
    if (tag !== "select") return { ok: false, error: { code: "NOT_SELECT_ELEMENT", message: "Target is not select" } };
    el.value = String(value ?? "");
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true, action: "select", value: el.value };
  }
  if (action === "click") {
    let target = el;
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const isHidden = tag === "input" && (type === "radio" || type === "checkbox") && (style.opacity === "0" || style.pointerEvents === "none" || rect.width === 0 || rect.height === 0 || style.display === "none" || style.visibility === "hidden");
    if (isHidden) {
      const lbl = (el.id ? document.querySelector('label[for="' + CSS.escape(el.id) + '"]') : null) || el.closest("label") || el.parentElement;
      if (lbl) target = lbl;
    }
    target.click();
    return { ok: true, action: "click", resolved: target === el ? "element" : "label" };
  }
  if (action === "fill") {
    el.focus();
    el.value = String(value ?? "");
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true, action: "fill", value: el.value };
  }
  if (action === "check" || action === "uncheck") {
    el.checked = action === "check";
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true, action, checked: el.checked };
  }
  if (action === "press") {
    const key = String(value ?? "Enter");
    el.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keypress", { key, bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { key, bubbles: true }));
    return { ok: true, action: "press", key };
  }
  return { ok: false, error: { code: "UNKNOWN_ACTION", message: "Unsupported action: " + action } };
}
var Page = class {
  constructor(cdp, tabId, sessionId) {
    this.cdp = cdp;
    this.tabId = tabId;
    this.sessionId = sessionId;
  }
  cdp;
  tabId;
  sessionId;
  async init() {
    await this.send("Page.enable");
    await this.send("Runtime.enable");
    this.cdp.on("Page.javascriptDialogOpening", async (_, sId) => {
      if (sId === this.sessionId) {
        try {
          await this.send("Page.handleJavaScriptDialog", { accept: true });
        } catch {
        }
      }
    });
  }
  send(method, params = {}, timeoutMs = 15e3) {
    return this.cdp.send(method, params, this.sessionId, timeoutMs);
  }
  async evaluate(expression, opts) {
    const res = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: opts?.awaitPromise ?? true,
      returnByValue: true,
      userGesture: true
    }, opts?.timeoutMs ?? 15e3);
    if (res.exceptionDetails) {
      const desc = res.exceptionDetails.exception?.description || res.exceptionDetails.text || "Execution failed";
      throw new Error(`EvalError: ${desc}`);
    }
    let val = res.result?.value;
    const max = opts?.maxOutputChars ?? 5e4;
    if (typeof val === "string" && val.length > max) val = val.slice(0, max) + "... [TRUNCATED]";
    return val;
  }
  async goto(url, opts) {
    const wait = opts?.wait ?? "load";
    if (wait === "none") {
      await this.send("Page.navigate", { url });
      return;
    }
    const timeoutMs = opts?.timeoutMs ?? 15e3;
    return new Promise(async (resolve, reject) => {
      let cleanup = [];
      const timer = setTimeout(() => {
        cleanup.forEach((f) => f());
        resolve();
      }, timeoutMs);
      cleanup.push(() => clearTimeout(timer));
      const ev = wait === "commit" ? "Page.frameNavigated" : "Page.loadEventFired";
      const off = this.cdp.on(ev, (_, sId) => {
        if (sId === this.sessionId) {
          cleanup.forEach((f) => f());
          resolve();
        }
      });
      cleanup.push(off);
      try {
        await this.send("Page.navigate", { url });
      } catch (err) {
        cleanup.forEach((f) => f());
        reject(err);
      }
    });
  }
  async act(action, opts) {
    const code = `(${inBrowserAct.toString()})(${JSON.stringify({ ...action, snapshotId: opts?.snapshotId })})`;
    return this.evaluate(code);
  }
  async actBatch(actions, opts) {
    const stopOnError = opts?.stopOnError ?? true;
    const results = [];
    for (let i = 0; i < actions.length; i++) {
      const res = await this.act(actions[i], { snapshotId: opts?.snapshotId });
      results.push({ index: i, ...res });
      if (!res.ok && stopOnError) return { ok: false, failedIndex: i, error: res.error, results };
    }
    return { ok: true, results };
  }
  async scroll(opts) {
    const dir = opts?.direction ?? "down";
    const amt = opts?.amount ?? 500;
    const dx = dir === "left" ? -amt : dir === "right" ? amt : 0;
    const dy = dir === "up" ? -amt : dir === "down" ? amt : 0;
    await this.evaluate(`((dx, dy, ref, sel) => {
      let target = window;
      if (ref && window.__omni_refs && window.__omni_refs[ref]) target = window.__omni_refs[ref];
      else if (sel) target = document.querySelector(sel) || window;
      target.scrollBy({ left: dx, top: dy, behavior: "instant" });
    })(${dx}, ${dy}, ${JSON.stringify(opts?.ref)}, ${JSON.stringify(opts?.selector)})`);
  }
};

// src/observe.ts
function inBrowserObserve(opts) {
  const root = (opts?.selector ? document.querySelector(opts.selector) : null) || document.body || document.documentElement;
  if (!root) throw new Error("Target root element not found");
  const snapshotId = "s_" + Date.now().toString(36);
  window.__omni_snapshot_id = snapshotId;
  window.__omni_refs = {};
  let counter = 0;
  const controls = [];
  const elements = root.querySelectorAll('button, input, select, textarea, a[href], [role="button"], [role="radio"], [role="checkbox"], [contenteditable="true"]');
  for (const el of Array.from(elements)) {
    const style = window.getComputedStyle(el);
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    const isStyledInput = tag === "input" && (type === "radio" || type === "checkbox");
    if (style.display === "none" || style.visibility === "hidden" || el.getAttribute("aria-hidden") === "true") continue;
    if (!isStyledInput && (style.opacity === "0" || el.offsetParent === null && tag !== "body")) continue;
    const ref = "r" + ++counter;
    window.__omni_refs[ref] = el;
    let label = "";
    if (el.id) {
      const lbl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lbl) label = lbl.innerText.trim();
    }
    if (!label) {
      const pLbl = el.closest("label");
      if (pLbl) label = pLbl.innerText.trim();
    }
    if (!label) {
      label = el.getAttribute("aria-label") || el.getAttribute("placeholder") || el.getAttribute("title") || "";
    }
    if (!label && (tag === "button" || tag === "a" || el.getAttribute("role") === "button")) {
      label = (el.innerText || "").trim().slice(0, 80);
    }
    let val = el.value;
    if (val !== void 0 && val !== null) {
      const strVal = String(val);
      if (strVal.length > 100 || strVal.startsWith("data:") || el.id?.includes("VIEWSTATE") || el.getAttribute("name")?.includes("VIEWSTATE")) {
        val = "[TRUNCATED_BLOB]";
      }
    }
    controls.push({
      ref,
      tag,
      type: type || void 0,
      label: label ? label.slice(0, 100) : void 0,
      name: el.getAttribute("name") || void 0,
      value: val !== void 0 ? String(val).slice(0, 100) : void 0,
      checked: el.checked !== void 0 ? el.checked : void 0,
      disabled: el.disabled || void 0
    });
  }
  function extractText(node) {
    let out = "";
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
      acceptNode(n) {
        if (n.nodeType === Node.ELEMENT_NODE) {
          const s = window.getComputedStyle(n);
          const t = n.tagName.toLowerCase();
          if (t === "script" || t === "style" || t === "noscript" || t === "svg") return NodeFilter.FILTER_REJECT;
          if (s.display === "none" || s.visibility === "hidden" || n.getAttribute("aria-hidden") === "true") return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_SKIP;
        }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    let cur;
    while (cur = walker.nextNode()) {
      const v = cur.nodeValue;
      if (v) {
        const cleaned = v.replace(/data:[^;]+;base64,[A-Za-z0-9+/=]+/g, "[BASE64_IMAGE]").trim();
        if (cleaned) out += cleaned + "\n";
      }
    }
    return out.replace(/\n{3,}/g, "\n\n").trim();
  }
  const rawText = extractText(root);
  const max = opts?.maxChars ?? 5e4;
  const truncated = rawText.length > max;
  const text = truncated ? rawText.slice(0, max) + "\n... [OBSERVE_TRUNCATED]" : rawText;
  return {
    snapshot_id: snapshotId,
    url: window.location.href,
    title: document.title,
    text,
    controls,
    truncated
  };
}
async function observe(page, opts) {
  const code = `(${inBrowserObserve.toString()})(${JSON.stringify(opts || {})})`;
  return page.evaluate(code);
}

// src/artifacts.ts
import fs from "fs/promises";
import path from "path";
async function screenshot(page, opts = {}) {
  let clip = void 0;
  if (opts.selector) {
    const rect = await page.evaluate(`(() => {
      const el = document.querySelector(${JSON.stringify(opts.selector)});
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: r.x + window.scrollX, y: r.y + window.scrollY, width: r.width, height: r.height, scale: 1 };
    })()`);
    if (rect) clip = rect;
  } else if (opts.fullPage) {
    const metrics = await page.evaluate(`(() => ({
      width: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth),
      height: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)
    }))()`);
    if (metrics) clip = { x: 0, y: 0, width: metrics.width, height: metrics.height, scale: 1 };
  }
  const params = { format: opts.format || "png" };
  if (clip) params.clip = clip;
  if (opts.quality && opts.format === "jpeg") params.quality = opts.quality;
  const { data } = await page.send("Page.captureScreenshot", params);
  if (opts.path) {
    const resolved = path.resolve(opts.path);
    await fs.mkdir(path.dirname(resolved), { recursive: true });
    await fs.writeFile(resolved, Buffer.from(data, "base64"));
    return { data, path: resolved };
  }
  return { data };
}
async function download(page, opts) {
  const dir = path.resolve(opts.downloadPath);
  await fs.mkdir(dir, { recursive: true });
  await page.send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: dir });
  if (opts.ref || opts.selector) {
    await page.act({ action: "click", ref: opts.ref, selector: opts.selector });
  }
  return { status: "download_triggered", path: dir };
}

// src/browser.ts
var Browser = class _Browser {
  cdp;
  sessions = /* @__PURE__ */ new Map();
  pages = /* @__PURE__ */ new Map();
  constructor(cdp) {
    this.cdp = cdp;
    this.cdp.on("Target.targetDestroyed", ({ targetId }) => {
      this.sessions.delete(targetId);
      this.pages.delete(targetId);
    });
    this.cdp.on("Target.detachedFromTarget", ({ sessionId }) => {
      for (const [tId, sId] of this.sessions) {
        if (sId === sessionId) {
          this.sessions.delete(tId);
          this.pages.delete(tId);
        }
      }
    });
  }
  static async connect(port = 17082, host = "127.0.0.1") {
    const cdp = await CdpConnection.connect(host, port);
    return new _Browser(cdp);
  }
  async listTabs() {
    const { targetInfos } = await this.cdp.send("Target.getTargets");
    return targetInfos.filter((t) => t.type === "page").map((t) => ({
      tab_id: t.targetId,
      title: t.title,
      url: t.url,
      type: t.type
    }));
  }
  async newTab(url = "about:blank") {
    const { targetId } = await this.cdp.send("Target.createTarget", { url });
    return this.getPage(targetId);
  }
  async closeTab(tabId) {
    await this.cdp.send("Target.closeTarget", { targetId: tabId });
    this.sessions.delete(tabId);
    this.pages.delete(tabId);
  }
  async getPage(tabId) {
    let targetId = tabId;
    if (!targetId) {
      const tabs = await this.listTabs();
      if (!tabs.length) throw new Error("No open tabs found in browser");
      targetId = tabs[0].tab_id;
    }
    const cached = this.pages.get(targetId);
    if (cached) return cached;
    let sessionId = this.sessions.get(targetId);
    if (!sessionId) {
      const res = await this.cdp.send("Target.attachToTarget", { targetId, flatten: true });
      sessionId = res.sessionId;
      this.sessions.set(targetId, sessionId);
    }
    const page = new Page(this.cdp, targetId, sessionId);
    await page.init();
    this.pages.set(targetId, page);
    return page;
  }
  close() {
    this.cdp.close();
  }
};

export {
  CdpConnection,
  Page,
  observe,
  screenshot,
  download,
  Browser
};
