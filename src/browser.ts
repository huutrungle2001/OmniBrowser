import { CdpConnection } from "./cdp.js";
import { Page } from "./page.js";
export * from "./cdp.js";
export * from "./page.js";
export * from "./observe.js";
export * from "./artifacts.js";

export interface TabInfo {
  tab_id: string;
  title: string;
  url: string;
  type: string;
}

export class Browser {
  readonly cdp: CdpConnection;
  private sessions = new Map<string, string>();
  private pages = new Map<string, Page>();

  constructor(cdp: CdpConnection) {
    this.cdp = cdp;
    this.cdp.on("Target.targetDestroyed", ({ targetId }: { targetId: string }) => {
      this.sessions.delete(targetId);
      this.pages.delete(targetId);
    });
    this.cdp.on("Target.detachedFromTarget", ({ sessionId }: { sessionId: string }) => {
      for (const [tId, sId] of this.sessions) {
        if (sId === sessionId) {
          this.sessions.delete(tId);
          this.pages.delete(tId);
        }
      }
    });
  }

  static async connect(port = 17082, host = "127.0.0.1"): Promise<Browser> {
    const cdp = await CdpConnection.connect(host, port);
    return new Browser(cdp);
  }

  async listTabs(): Promise<TabInfo[]> {
    const { targetInfos } = await this.cdp.send("Target.getTargets");
    return targetInfos
      .filter((t: any) => t.type === "page")
      .map((t: any) => ({
        tab_id: t.targetId,
        title: t.title,
        url: t.url,
        type: t.type
      }));
  }

  async newTab(url = "about:blank"): Promise<Page> {
    const { targetId } = await this.cdp.send("Target.createTarget", { url });
    return this.getPage(targetId);
  }

  async closeTab(tabId: string): Promise<void> {
    await this.cdp.send("Target.closeTarget", { targetId: tabId });
    this.sessions.delete(tabId);
    this.pages.delete(tabId);
  }

  async getPage(tabId?: string): Promise<Page> {
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
      this.sessions.set(targetId, sessionId!);
    }
    const page = new Page(this.cdp, targetId, sessionId!);
    await page.init();
    this.pages.set(targetId, page);
    return page;
  }

  close(): void {
    this.cdp.close();
  }
}

