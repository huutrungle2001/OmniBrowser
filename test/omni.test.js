import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { spawn } from "node:child_process";
import readline from "node:readline";
import { Browser, observe, screenshot } from "../dist/index.js";

const testHtmlUrl = "file://" + path.resolve("test_page.html");

test("OmniBrowser v3 Comprehensive Real-world Test Suite", async (t) => {
  let browser;
  let page;

  await t.test("1. Connect to CDP port 17082", async () => {
    browser = await Browser.connect(17082);
    assert.ok(browser, "Browser connection established");
    const tabs = await browser.listTabs();
    assert.ok(Array.isArray(tabs), "Tabs is array");
    assert.ok(tabs.length > 0, "Found open tabs");
  });

  await t.test("2. Create new tab and navigate (goto)", async () => {
    page = await browser.newTab(testHtmlUrl);
    assert.ok(page.tabId, "Tab created with targetId");
    assert.ok(page.sessionId, "Tab attached with sessionId");
  });

  await t.test("3. Evaluate JS directly (evaluate)", async () => {
    const title = await page.evaluate("document.title");
    assert.equal(title, "OmniBrowser v3 Comprehensive Test");

    const calculation = await page.evaluate("100 * 42 + 7");
    assert.equal(calculation, 4207);
  });

  let snapshotId;
  let radioRef;
  let selectRef;
  let inputRef;
  let submitRef;

  await t.test("4. Scoped and Noise-filtered Observation (observe)", async () => {
    const obs = await observe(page, { selector: "#test-form" });
    assert.ok(obs.snapshot_id, "Snapshot ID generated");
    snapshotId = obs.snapshot_id;

    // Verify Base64 bloat is filtered out
    assert.ok(!obs.text.includes("iVBORw0KGgoAAAANSUhEUg"), "Raw Base64 blob is filtered from text");
    assert.ok(obs.text.includes("[BASE64_IMAGE]"), "Base64 image replaced with marker");

    // Verify __VIEWSTATE is not present in controls
    const viewstate = obs.controls.find(c => c.name === "__VIEWSTATE");
    assert.equal(viewstate, undefined, "ViewState hidden input is pruned from controls");

    // Verify controls
    const inputControl = obs.controls.find(c => c.tag === "input" && c.type === "text");
    assert.ok(inputControl, "Text input detected");
    inputRef = inputControl.ref;

    const radioControl = obs.controls.find(c => c.tag === "input" && c.type === "radio" && c.value === "pro");
    assert.ok(radioControl, "Styled opacity:0 radio detected");
    assert.equal(radioControl.label, "Pro Plan", "Label auto-resolved in observe");
    radioRef = radioControl.ref;

    const selectControl = obs.controls.find(c => c.tag === "select");
    assert.ok(selectControl, "Select dropdown detected");
    selectRef = selectControl.ref;

    const submitControl = obs.controls.find(c => c.tag === "button");
    assert.ok(submitControl, "Submit button detected");
    submitRef = submitControl.ref;
  });

  await t.test("5. Smart Act: Auto-resolve label for hidden opacity:0 input", async () => {
    const res = await page.act({ action: "click", ref: radioRef }, { snapshotId });
    assert.equal(res.ok, true, "Act click succeeded");
    assert.equal(res.resolved, "label", "Resolved target is label");

    const logText = await page.evaluate("document.getElementById('output-log').innerText");
    assert.ok(logText.includes("Radio changed to: pro"), "Change event fired on radio via label click");
  });

  await t.test("6. Smart Act: Safe select dropdown without native popup hang", async () => {
    const res = await page.act({ action: "select", ref: selectRef, value: "vn" }, { snapshotId });
    assert.equal(res.ok, true, "Select action succeeded");
    assert.equal(res.value, "vn", "Selected value is vn");

    const logText = await page.evaluate("document.getElementById('output-log').innerText");
    assert.ok(logText.includes("Country changed to: vn"), "Select change event fired");
  });

  await t.test("7. Batch Action Execution (act_batch)", async () => {
    const batchRes = await page.actBatch([
      { action: "fill", ref: inputRef, value: "Bob" },
      { action: "click", ref: submitRef }
    ], { snapshotId });

    assert.equal(batchRes.ok, true, "Batch succeeded");
    assert.equal(batchRes.results.length, 2, "2 actions executed");

    const logText = await page.evaluate("document.getElementById('output-log').innerText");
    assert.ok(logText.includes("Submitted! Username=Bob"), "Form submitted with filled username");
  });

  await t.test("8. Container-aware scrolling (scroll)", async () => {
    await page.scroll({ selector: "#inner-scroll", direction: "down", amount: 120 });
    const scrollTop = await page.evaluate("document.getElementById('inner-scroll').scrollTop");
    assert.equal(scrollTop, 120, "Inner container scrolled by 120px");
  });

  await t.test("9. Screenshot capture (screenshot)", async () => {
    const shotPath = "/tmp/omni_test_suite_shot.png";
    const shot = await screenshot(page, { selector: "#test-form", path: shotPath });
    assert.ok(shot.data.length > 100, "Screenshot data returned");
    assert.equal(shot.path, shotPath, "Screenshot saved to disk");
  });

  await t.test("10. Close tab cleanly", async () => {
    await browser.closeTab(page.tabId);
    browser.close();
  });

  await t.test("11. Stdio MCP Server protocol integration", async () => {
    const proc = spawn("node", ["dist/mcp.js"], {
      env: { ...process.env, CDP_PORT: "17082" }
    });
    const rl = readline.createInterface({ input: proc.stdout });

    let reqId = 100;
    const rpc = (method, params = {}) => {
      const id = ++reqId;
      return new Promise((resolve) => {
        const onLine = (line) => {
          const res = JSON.parse(line);
          if (res.id === id) {
            rl.off("line", onLine);
            resolve(res);
          }
        };
        rl.on("line", onLine);
        proc.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
      });
    };

    const init = await rpc("initialize");
    assert.equal(init.result?.serverInfo?.name, "omnibrowser");

    const toolsRes = await rpc("tools/list");
    assert.ok(toolsRes.result?.tools?.some(t => t.name === "act_batch"), "act_batch in tools");
    assert.ok(toolsRes.result?.tools?.some(t => t.name === "evaluate"), "evaluate in tools");
    assert.ok(toolsRes.result?.tools?.some(t => t.name === "fetch"), "fetch in tools");

    const callRes = await rpc("tools/call", { name: "list_tabs", arguments: {} });
    assert.ok(!callRes.result.isError, "list_tabs returned cleanly");

    proc.kill();
  });
});

