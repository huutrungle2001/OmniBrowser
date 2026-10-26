import WebSocket from "ws";

export class CdpConnection {
  private ws: WebSocket;
  private nextId = 1;
  private pending = new Map<number, { resolve: (res: any) => void; reject: (err: any) => void; timer: NodeJS.Timeout }>();
  private handlers = new Map<string, Set<(params: any, sessionId?: string) => void>>();

  constructor(ws: WebSocket) {
    this.ws = ws;
    this.ws.on("message", (data) => this.onMessage(data.toString()));
    this.ws.on("close", () => this.onClose("WebSocket closed"));
    this.ws.on("error", (err) => this.onClose(`WebSocket error: ${err.message}`));
  }

  static async connect(host = "127.0.0.1", port = 17082, timeout = 5000): Promise<CdpConnection> {
    const res = await fetch(`http://${host}:${port}/json/version`, { signal: AbortSignal.timeout(timeout) });
    if (!res.ok) throw new Error(`Failed to fetch CDP version: ${res.statusText}`);
    const { webSocketDebuggerUrl } = await res.json();
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(webSocketDebuggerUrl);
      const timer = setTimeout(() => { ws.close(); reject(new Error("CDP WS connection timeout")); }, timeout);
      ws.once("open", () => { clearTimeout(timer); resolve(new CdpConnection(ws)); });
      ws.once("error", (err) => { clearTimeout(timer); reject(err); });
    });
  }

  send(method: string, params: any = {}, sessionId?: string, timeoutMs = 15000): Promise<any> {
    return new Promise((resolve, reject) => {
      if (this.ws.readyState !== WebSocket.OPEN) return reject(new Error("CDP connection is not open"));
      const id = this.nextId++;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP command ${method} timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      const payload: any = { id, method, params };
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

  on(method: string, handler: (params: any, sessionId?: string) => void): () => void {
    if (!this.handlers.has(method)) this.handlers.set(method, new Set());
    this.handlers.get(method)!.add(handler);
    return () => this.handlers.get(method)?.delete(handler);
  }

  private onMessage(raw: string): void {
    try {
      const msg = JSON.parse(raw);
      if (msg.id !== undefined) {
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
    } catch {}
  }

  private onClose(reason: string): void {
    for (const [, item] of this.pending) {
      clearTimeout(item.timer);
      item.reject(new Error(`CDP closed: ${reason}`));
    }
    this.pending.clear();
  }

  close(): void {
    this.ws.close();
  }
}

