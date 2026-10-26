import fs from "node:fs/promises";
import path from "node:path";
import { Page } from "./page.js";

export interface ScreenshotOptions {
  selector?: string;
  fullPage?: boolean;
  format?: "png" | "jpeg";
  quality?: number;
  path?: string;
}

export async function screenshot(page: Page, opts: ScreenshotOptions = {}): Promise<{ data: string; path?: string }> {
  let clip: any = undefined;
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

  const params: any = { format: opts.format || "png" };
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

export async function download(page: Page, opts: { downloadPath: string; ref?: string; selector?: string }): Promise<{ status: string; path: string }> {
  const dir = path.resolve(opts.downloadPath);
  await fs.mkdir(dir, { recursive: true });
  await page.send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: dir });
  if (opts.ref || opts.selector) {
    await page.act({ action: "click", ref: opts.ref, selector: opts.selector });
  }
  return { status: "download_triggered", path: dir };
}

