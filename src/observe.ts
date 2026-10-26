import { Page } from "./page.js";

export interface ControlInfo {
  ref: string;
  tag: string;
  type?: string;
  label?: string;
  name?: string;
  value?: string;
  checked?: boolean;
  disabled?: boolean;
}

export interface Observation {
  snapshot_id: string;
  url: string;
  title: string;
  text: string;
  controls: ControlInfo[];
  truncated: boolean;
}

function inBrowserObserve(opts: { selector?: string; maxChars?: number }): any {
  const root = (opts?.selector ? document.querySelector(opts.selector) : null) || document.body || document.documentElement;
  if (!root) throw new Error("Target root element not found");

  const snapshotId = "s_" + Date.now().toString(36);
  (window as any).__omni_snapshot_id = snapshotId;
  (window as any).__omni_refs = {};

  let counter = 0;
  const controls: any[] = [];
  const elements = root.querySelectorAll('button, input, select, textarea, a[href], [role="button"], [role="radio"], [role="checkbox"], [contenteditable="true"]');

  for (const el of Array.from(elements) as any[]) {
    const style = window.getComputedStyle(el);
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    const isStyledInput = tag === "input" && (type === "radio" || type === "checkbox");

    if (style.display === "none" || style.visibility === "hidden" || el.getAttribute("aria-hidden") === "true") continue;
    if (!isStyledInput && (style.opacity === "0" || (el.offsetParent === null && tag !== "body"))) continue;

    const ref = "r" + (++counter);
    (window as any).__omni_refs[ref] = el;

    let label = "";
    if (el.id) {
      const lbl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lbl) label = (lbl as HTMLElement).innerText.trim();
    }
    if (!label) {
      const pLbl = el.closest("label");
      if (pLbl) label = (pLbl as HTMLElement).innerText.trim();
    }
    if (!label) {
      label = el.getAttribute("aria-label") || el.getAttribute("placeholder") || el.getAttribute("title") || "";
    }
    if (!label && (tag === "button" || tag === "a" || el.getAttribute("role") === "button")) {
      label = (el.innerText || "").trim().slice(0, 80);
    }

    let val = el.value;
    if (val !== undefined && val !== null) {
      const strVal = String(val);
      if (strVal.length > 100 || strVal.startsWith("data:") || el.id?.includes("VIEWSTATE") || el.getAttribute("name")?.includes("VIEWSTATE")) {
        val = "[TRUNCATED_BLOB]";
      }
    }

    controls.push({
      ref,
      tag,
      type: type || undefined,
      label: label ? label.slice(0, 100) : undefined,
      name: el.getAttribute("name") || undefined,
      value: val !== undefined ? String(val).slice(0, 100) : undefined,
      checked: el.checked !== undefined ? el.checked : undefined,
      disabled: el.disabled || undefined
    });
  }

  function extractText(node: Node): string {
    let out = "";
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
      acceptNode(n) {
        if (n.nodeType === Node.ELEMENT_NODE) {
          const s = window.getComputedStyle(n as Element);
          const t = (n as Element).tagName.toLowerCase();
          if (t === "script" || t === "style" || t === "noscript" || t === "svg") return NodeFilter.FILTER_REJECT;
          if (s.display === "none" || s.visibility === "hidden" || (n as Element).getAttribute("aria-hidden") === "true") return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_SKIP;
        }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    let cur: Node | null;
    while ((cur = walker.nextNode())) {
      const v = cur.nodeValue;
      if (v) {
        const cleaned = v.replace(/data:[^;]+;base64,[A-Za-z0-9+/=]+/g, "[BASE64_IMAGE]").trim();
        if (cleaned) out += cleaned + "\n";
      }
    }
    return out.replace(/\n{3,}/g, "\n\n").trim();
  }

  const rawText = extractText(root);
  const max = opts?.maxChars ?? 50000;
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

export async function observe(page: Page, opts?: { selector?: string; maxChars?: number }): Promise<Observation> {
  const code = `(${inBrowserObserve.toString()})(${JSON.stringify(opts || {})})`;
  return page.evaluate<Observation>(code);
}

