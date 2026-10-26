import { CdpConnection } from "./cdp.js";

export interface ActAction {
  ref?: string;
  selector?: string;
  action: "click" | "fill" | "check" | "uncheck" | "select" | "press";
  value?: any;
}

export interface ActionResult {
  ok: boolean;
  action?: string;
  value?: any;
  error?: { code: string; message: string; retryable?: boolean };
}

export interface BatchResult {
  ok: boolean;
  failedIndex?: number;
  error?: any;
  results: any[];
}

function inBrowserAct(args: any): any {
  const { ref, selector, action, value, snapshotId } = args;
  const win = window as any;
  if (snapshotId && win.__omni_snapshot_id && win.__omni_snapshot_id !== snapshotId) {
    return { ok: false, error: { code: "STALE_REF", message: "Reference belongs to an older observation", retryable: true } };
  }
  let el: any = null;
  if (ref) {
    el = win.__omni_refs ? win.__omni_refs[ref] : null;
    if (!el) return { ok: false, error: { code: "STALE_REF", message: "Element ref not found in snapshot", retryable: true } };
  } else if (selector) {
    el = document.querySelector(selector);
    if (!el) return { ok: false, error: { code: "ELEMENT_NOT_FOUND", message: "Selector not found: " + selector } };
  } else {
    return { ok: false, error: { code: "INVALID_TARGET", message: "Either ref or selector must be provided" } };
  }
  try { el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" }); } catch {}
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
    const isHidden = (tag === "input" && (type === "radio" || type === "checkbox")) &&
      (style.opacity === "0" || style.pointerEvents === "none" || rect.width === 0 || rect.height === 0 || style.display === "none" || style.visibility === "hidden");
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

export class Page {
  constructor(
    readonly cdp: CdpConnection,
    readonly tabId: string,
    readonly sessionId: string
  ) {}

  async init(): Promise<void> {
    await this.send("Page.enable");
    await this.send("Runtime.enable");
    this.cdp.on("Page.javascriptDialogOpening", async (_, sId) => {
      if (sId === this.sessionId) {
        try { await this.send("Page.handleJavaScriptDialog", { accept: true }); } catch {}
      }
    });
  }

  send(method: string, params: any = {}, timeoutMs = 15000): Promise<any> {
    return this.cdp.send(method, params, this.sessionId, timeoutMs);
  }

  async evaluate<T = any>(
    expression: string,
    opts?: { awaitPromise?: boolean; timeoutMs?: number; maxOutputChars?: number }
  ): Promise<T> {
    const res = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: opts?.awaitPromise ?? true,
      returnByValue: true,
      userGesture: true
    }, opts?.timeoutMs ?? 15000);
    if (res.exceptionDetails) {
      const desc = res.exceptionDetails.exception?.description || res.exceptionDetails.text || "Execution failed";
      throw new Error(`EvalError: ${desc}`);
    }
    let val = res.result?.value;
    const max = opts?.maxOutputChars ?? 50000;
    if (typeof val === "string" && val.length > max) val = val.slice(0, max) + "... [TRUNCATED]";
    return val;
  }

  async goto(url: string, opts?: { wait?: "load" | "commit" | "none"; timeoutMs?: number }): Promise<void> {
    const wait = opts?.wait ?? "load";
    if (wait === "none") {
      await this.send("Page.navigate", { url });
      return;
    }
    const timeoutMs = opts?.timeoutMs ?? 15000;
    return new Promise<void>(async (resolve, reject) => {
      let cleanup: (() => void)[] = [];
      const timer = setTimeout(() => { cleanup.forEach((f) => f()); resolve(); }, timeoutMs);
      cleanup.push(() => clearTimeout(timer));
      const ev = wait === "commit" ? "Page.frameNavigated" : "Page.loadEventFired";
      const off = this.cdp.on(ev, (_, sId) => {
        if (sId === this.sessionId) { cleanup.forEach((f) => f()); resolve(); }
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

  async act(action: ActAction, opts?: { snapshotId?: string }): Promise<ActionResult> {
    const code = `(${inBrowserAct.toString()})(${JSON.stringify({ ...action, snapshotId: opts?.snapshotId })})`;
    return this.evaluate<ActionResult>(code);
  }

  async actBatch(actions: ActAction[], opts?: { snapshotId?: string; stopOnError?: boolean }): Promise<BatchResult> {
    const stopOnError = opts?.stopOnError ?? true;
    const results: any[] = [];
    for (let i = 0; i < actions.length; i++) {
      const res = await this.act(actions[i], { snapshotId: opts?.snapshotId });
      results.push({ index: i, ...res });
      if (!res.ok && stopOnError) return { ok: false, failedIndex: i, error: res.error, results };
    }
    return { ok: true, results };
  }

  async scroll(opts?: { ref?: string; selector?: string; direction?: "up" | "down" | "left" | "right"; amount?: number }): Promise<void> {
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
}

