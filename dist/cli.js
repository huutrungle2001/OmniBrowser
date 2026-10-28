#!/usr/bin/env node
import {
  Browser,
  observe,
  screenshot
} from "./chunk-FN6CJW7Z.js";

// src/cli.ts
var args = process.argv.slice(2);
var flags = {};
for (let i = 0; i < args.length; i++) {
  if (args[i].startsWith("--")) {
    const k = args[i].slice(2), next = args[i + 1];
    if (next && !next.startsWith("--")) {
      flags[k] = next;
      i++;
    } else flags[k] = true;
  }
}
async function main() {
  const browser = await Browser.connect(Number(flags.port || 17082), String(flags.host || "127.0.0.1"));
  try {
    if (flags["list-tabs"]) return console.log(JSON.stringify({ ok: true, tabs: await browser.listTabs() }, null, 2));
    if (flags["close-tab"]) {
      await browser.closeTab(String(flags["close-tab"]));
      return console.log(JSON.stringify({ ok: true }));
    }
    if (flags.goto && flags["new-tab"]) {
      const page2 = await browser.newTab(String(flags.goto));
      return console.log(JSON.stringify({ ok: true, tab_id: page2.tabId, url: flags.goto }));
    }
    const page = await browser.getPage(flags.tab ? String(flags.tab) : void 0);
    if (flags.goto) {
      await page.goto(String(flags.goto));
      return console.log(JSON.stringify({ ok: true, tab_id: page.tabId, url: flags.goto }));
    }
    if (flags.eval) return console.log(JSON.stringify({ ok: true, value: await page.evaluate(String(flags.eval)) }, null, 2));
    if (flags.fetch) {
      let headers;
      if (flags.headers) {
        try {
          headers = JSON.parse(flags.headers);
        } catch {
          headers = void 0;
        }
      }
      let body;
      if (flags.body) {
        try {
          body = JSON.parse(flags.body);
        } catch {
          body = flags.body;
        }
      }
      const res = await page.fetch(String(flags.fetch), { method: flags.method ? String(flags.method) : void 0, headers, body });
      return console.log(JSON.stringify({ ok: true, ...res }, null, 2));
    }
    if (flags.observe) {
      const max = flags["max-chars"] ? Number(flags["max-chars"]) : void 0;
      return console.log(JSON.stringify({ ok: true, ...await observe(page, { selector: flags.selector, maxChars: max }) }, null, 2));
    }
    if (flags.act) {
      return console.log(JSON.stringify(await page.act({ action: flags.act, ref: flags.ref, selector: flags.selector, value: flags.value }, { snapshotId: flags.snapshot }), null, 2));
    }
    if (flags["act-batch"]) {
      return console.log(JSON.stringify(await page.actBatch(JSON.parse(flags["act-batch"]), { snapshotId: flags.snapshot }), null, 2));
    }
    if (flags.scroll) {
      await page.scroll({ ref: flags.ref, selector: flags.selector, direction: flags.direction, amount: flags.amount ? Number(flags.amount) : void 0 });
      return console.log(JSON.stringify({ ok: true }));
    }
    if (flags.screenshot) {
      const res = await screenshot(page, { path: flags.path, selector: flags.selector, fullPage: Boolean(flags["full-page"]) });
      return console.log(JSON.stringify({ ok: true, path: res.path, bytes: res.data?.length }));
    }
    console.log(JSON.stringify({ ok: false, error: "No valid command specified" }));
  } finally {
    browser.close();
  }
}
main().catch((err) => {
  console.error(JSON.stringify({ ok: false, error: err.message }));
  process.exit(1);
});
