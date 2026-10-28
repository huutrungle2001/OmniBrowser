#!/usr/bin/env node
import {
  Browser,
  observe,
  screenshot
} from "./chunk-FN6CJW7Z.js";

// src/mcp.ts
import readline from "readline";
var str = () => ({ type: "string" });
var num = () => ({ type: "number" });
var tool = (name, description, properties = {}, required) => ({
  name,
  description,
  inputSchema: { type: "object", properties, required }
});
var TOOLS = [
  tool("list_tabs", "List all open browser tabs"),
  tool("goto", "Navigate a tab to URL", { tab_id: str(), url: str(), wait: { type: "string", enum: ["load", "commit", "none"] } }, ["url"]),
  tool("observe", "Extract clean page text, controls with refs, and links", { tab_id: str(), selector: str(), max_chars: num() }),
  tool("act", "Perform smart action (click, fill, select, check, press)", { tab_id: str(), ref: str(), selector: str(), action: str(), value: str(), snapshot_id: str() }, ["action"]),
  tool("act_batch", "Execute multiple actions sequentially in 1 turn", { tab_id: str(), snapshot_id: str(), actions: { type: "array" }, stop_on_error: { type: "boolean" } }, ["actions"]),
  tool("fetch", "In-session HTTP request directly inside tab context (inherits origin cookies, CSRF, auth tokens; bypasses CORS)", { tab_id: str(), url: str(), method: str(), headers: { type: "object" }, body: {} }, ["url"]),
  tool("evaluate", "Evaluate JavaScript in page context", { tab_id: str(), expression: str() }, ["expression"]),
  tool("scroll", "Scroll page or container element", { tab_id: str(), ref: str(), selector: str(), direction: str(), amount: num() }),
  tool("screenshot", "Capture screenshot of viewport, full page, or element", { tab_id: str(), selector: str(), full_page: { type: "boolean" }, path: str() })
];
var browserInstance = null;
async function getBrowser() {
  if (!browserInstance) {
    const port = Number(process.env.CDP_PORT || 17082);
    browserInstance = await Browser.connect(port, process.env.CDP_HOST || "127.0.0.1");
  }
  return browserInstance;
}
async function handleToolCall(name, args) {
  const browser = await getBrowser();
  if (name === "list_tabs") return { tabs: await browser.listTabs() };
  const page = await browser.getPage(args.tab_id);
  switch (name) {
    case "goto":
      await page.goto(args.url, { wait: args.wait });
      return { ok: true, tab_id: page.tabId, url: args.url };
    case "observe":
      return observe(page, { selector: args.selector, maxChars: args.max_chars });
    case "act":
      return page.act({ action: args.action, ref: args.ref, selector: args.selector, value: args.value }, { snapshotId: args.snapshot_id });
    case "act_batch":
      return page.actBatch(args.actions, { snapshotId: args.snapshot_id, stopOnError: args.stop_on_error });
    case "fetch":
      return page.fetch(args.url, { method: args.method, headers: args.headers, body: args.body });
    case "evaluate":
      return { ok: true, value: await page.evaluate(args.expression) };
    case "scroll":
      await page.scroll({ ref: args.ref, selector: args.selector, direction: args.direction, amount: args.amount });
      return { ok: true };
    case "screenshot":
      const shot = await screenshot(page, { selector: args.selector, fullPage: args.full_page, path: args.path });
      return { ok: true, path: shot.path, length: shot.data?.length };
    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}
var send = (res) => process.stdout.write(JSON.stringify(res) + "\n");
var rl = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: false });
rl.on("line", async (line) => {
  if (!line.trim()) return;
  try {
    const req = JSON.parse(line);
    const { id, method, params } = req;
    if (method === "initialize") {
      send({ jsonrpc: "2.0", id, result: { protocolVersion: "2024-11-05", capabilities: { tools: {} }, serverInfo: { name: "omnibrowser", version: "3.0.0" } } });
    } else if (method === "notifications/initialized") {
    } else if (method === "tools/list") {
      send({ jsonrpc: "2.0", id, result: { tools: TOOLS } });
    } else if (method === "tools/call") {
      try {
        const res = await handleToolCall(params?.name, params?.arguments || {});
        send({ jsonrpc: "2.0", id, result: { content: [{ type: "text", text: JSON.stringify(res, null, 2) }] } });
      } catch (err) {
        send({ jsonrpc: "2.0", id, result: { isError: true, content: [{ type: "text", text: err.message }] } });
      }
    } else {
      send({ jsonrpc: "2.0", id, error: { code: -32601, message: "Method not found" } });
    }
  } catch (err) {
    send({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error: " + err.message } });
  }
});
