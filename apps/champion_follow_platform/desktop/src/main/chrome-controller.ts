import { spawn, type ChildProcess } from "node:child_process";
import { access, mkdir, readFile } from "node:fs/promises";
import { join } from "node:path";

import WebSocket, { type RawData } from "ws";

export type ChromePageTarget = {
  id: string;
  type: string;
  url: string;
  webSocketDebuggerUrl?: string;
};

export type ChromeLaunchOptions = {
  profileDirectory: string;
  initialUrl: string;
  proxyUrl?: string;
};

export type ChromeControllerOptions = ChromeLaunchOptions & {
  executable: string;
  launch?: typeof spawn;
  now?: () => number;
};

type DevToolsEndpoint = {
  port: number;
  browserWebSocketUrl: string;
};

type CdpResponse = {
  id?: number;
  result?: Record<string, unknown>;
  error?: { message?: string };
};

export function chromeLaunchArguments(options: ChromeLaunchOptions): string[] {
  const arguments_ = [
    `--user-data-dir=${options.profileDirectory}`,
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=0",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-mode",
  ];
  if (options.proxyUrl) {
    arguments_.push(`--proxy-server=${options.proxyUrl}`);
  } else {
    arguments_.push("--no-proxy-server");
  }
  arguments_.push("--new-window", options.initialUrl);
  return arguments_;
}

export function parseDevToolsActivePort(value: string): DevToolsEndpoint {
  const [rawPort, rawPath, ...rest] = value.trim().split(/\r?\n/);
  const port = Number(rawPort);
  if (rest.length !== 0 || !Number.isInteger(port) || port < 1 || port > 65_535 ||
      !rawPath || !/^\/devtools\/browser\/[A-Za-z0-9-]{1,128}$/.test(rawPath)) {
    throw new Error("chrome_debug_endpoint_invalid");
  }
  return {
    port,
    browserWebSocketUrl: `ws://127.0.0.1:${port}${rawPath}`,
  };
}

export function selectPlatformPageTarget(
  targets: readonly ChromePageTarget[],
): ChromePageTarget | null {
  return targets.find((target) => {
    if (target.type !== "page" || typeof target.webSocketDebuggerUrl !== "string") {
      return false;
    }
    try {
      const url = new URL(target.url);
      const websocket = new URL(target.webSocketDebuggerUrl);
      return url.protocol === "https:" && !url.username && !url.password &&
        websocket.protocol === "ws:" &&
        (websocket.hostname === "127.0.0.1" || websocket.hostname === "localhost");
    } catch {
      return false;
    }
  }) ?? null;
}

export async function findChromeExecutable(
  candidates: readonly string[],
): Promise<string> {
  for (const candidate of candidates) {
    try {
      await access(candidate);
      return candidate;
    } catch {
      // Continue to the next packaged or installed Chrome location.
    }
  }
  throw new Error("chrome_executable_not_found");
}

export class ChromeBrowserController {
  private endpoint: DevToolsEndpoint | null = null;
  private browser: CdpConnection | null = null;
  private page: CdpConnection | null = null;
  private process: ChildProcess | null = null;
  private opening: Promise<void> | null = null;
  private targetId: string | null = null;

  constructor(private readonly options: ChromeControllerOptions) {}

  isReady(): boolean {
    return this.page?.isOpen() === true;
  }

  async open(url: string = this.options.initialUrl): Promise<void> {
    if (this.opening) await this.opening;
    if (this.isReady()) {
      await this.navigate(url);
      return;
    }
    const opening = this.openInternal(url);
    this.opening = opening;
    try {
      await opening;
    } finally {
      if (this.opening === opening) this.opening = null;
    }
  }

  async navigate(url: string): Promise<void> {
    if (!this.page?.isOpen()) {
      await this.open(url);
      return;
    }
    await this.page.send("Page.navigate", { url });
    if (this.targetId && this.browser?.isOpen()) {
      await this.browser.send("Target.activateTarget", { targetId: this.targetId });
    }
  }

  async bringToFront(): Promise<void> {
    if (!this.page?.isOpen() || !this.browser?.isOpen() || !this.targetId) {
      throw new Error("chrome_page_unavailable");
    }
    await this.browser.send("Target.activateTarget", { targetId: this.targetId });
  }

  async evaluate<T>(code: string): Promise<T> {
    if (!this.page?.isOpen()) throw new Error("chrome_page_unavailable");
    const tree = await this.page.send("Page.getFrameTree") as {
      frameTree?: { frame?: { id?: string } };
    };
    const frameId = tree.frameTree?.frame?.id;
    if (!frameId) throw new Error("chrome_main_frame_unavailable");
    const world = await this.page.send("Page.createIsolatedWorld", {
      frameId,
      worldName: "champion-follow-client-v1",
      grantUniveralAccess: false,
    }) as { executionContextId?: number };
    if (!Number.isInteger(world.executionContextId)) {
      throw new Error("chrome_isolated_world_unavailable");
    }
    const evaluated = await this.page.send("Runtime.evaluate", {
      expression: code,
      contextId: world.executionContextId,
      returnByValue: true,
      awaitPromise: true,
      userGesture: false,
    }) as {
      result?: { value?: T; type?: string };
      exceptionDetails?: unknown;
    };
    if (evaluated.exceptionDetails || !evaluated.result ||
        !("value" in evaluated.result)) {
      throw new Error("chrome_evaluation_failed");
    }
    return evaluated.result.value as T;
  }

  async evaluateMainWorld<T>(code: string): Promise<T> {
    if (!this.page?.isOpen()) throw new Error("chrome_page_unavailable");
    const evaluated = await this.page.send("Runtime.evaluate", {
      expression: code,
      returnByValue: true,
      awaitPromise: true,
      userGesture: false,
    }) as {
      result?: { value?: T; type?: string };
      exceptionDetails?: unknown;
    };
    if (evaluated.exceptionDetails || !evaluated.result ||
        !("value" in evaluated.result)) {
      throw new Error("chrome_evaluation_failed");
    }
    return evaluated.result.value as T;
  }

  async close(): Promise<void> {
    try {
      if (this.browser?.isOpen()) await this.browser.send("Browser.close");
    } catch {
      // A browser already closed by the user needs no further action.
    }
    this.page?.close();
    this.browser?.close();
    this.page = null;
    this.browser = null;
    this.endpoint = null;
    this.targetId = null;
    this.process = null;
  }

  private async openInternal(url: string): Promise<void> {
    await mkdir(this.options.profileDirectory, { recursive: true });
    const existing = await this.readLiveEndpoint();
    if (existing) {
      await this.attach(existing);
      await this.navigate(url);
      return;
    }

    const launch = this.options.launch ?? spawn;
    const child = launch(
      this.options.executable,
      chromeLaunchArguments({
        profileDirectory: this.options.profileDirectory,
        initialUrl: url,
        ...(this.options.proxyUrl ? { proxyUrl: this.options.proxyUrl } : {}),
      }),
      { detached: true, stdio: "ignore", windowsHide: false },
    );
    this.process = child;
    const launchFailed = new Promise<never>((_resolve, reject) => {
      child.once("error", () => reject(new Error("chrome_start_failed")));
    });
    child.unref();
    const deadline = (this.options.now ?? Date.now)() + 20_000;
    do {
      await Promise.race([delay(100), launchFailed]);
      const endpoint = await this.readLiveEndpoint();
      if (!endpoint) continue;
      await this.attach(endpoint);
      return;
    } while ((this.options.now ?? Date.now)() < deadline);
    throw new Error("chrome_start_timeout");
  }

  private async readLiveEndpoint(): Promise<DevToolsEndpoint | null> {
    let endpoint: DevToolsEndpoint;
    try {
      endpoint = parseDevToolsActivePort(await readFile(
        join(this.options.profileDirectory, "DevToolsActivePort"),
        "utf8",
      ));
      const response = await fetch(`http://127.0.0.1:${endpoint.port}/json/version`, {
        signal: AbortSignal.timeout(1_000),
      });
      if (!response.ok) return null;
    } catch {
      return null;
    }
    return endpoint;
  }

  private async attach(endpoint: DevToolsEndpoint): Promise<void> {
    const response = await fetch(`http://127.0.0.1:${endpoint.port}/json/list`, {
      signal: AbortSignal.timeout(2_000),
    });
    if (!response.ok) throw new Error("chrome_target_list_unavailable");
    const target = selectPlatformPageTarget(await response.json() as ChromePageTarget[]);
    if (!target?.webSocketDebuggerUrl) {
      throw new Error("chrome_platform_page_unavailable");
    }
    this.browser?.close();
    this.page?.close();
    this.endpoint = endpoint;
    this.browser = await CdpConnection.connect(endpoint.browserWebSocketUrl);
    this.page = await CdpConnection.connect(target.webSocketDebuggerUrl);
    this.targetId = target.id;
    await this.page.send("Page.enable");
    await this.page.send("Runtime.enable");
    await this.browser.send("Target.activateTarget", { targetId: target.id });
  }
}

class CdpConnection {
  private sequence = 0;
  private readonly pending = new Map<number, {
    method: string;
    resolve(value: Record<string, unknown>): void;
    reject(error: Error): void;
  }>();

  private constructor(private readonly socket: WebSocket) {
    socket.on("message", (raw: RawData) => this.onMessage(raw));
    socket.on("close", () => this.onClosed());
    socket.on("error", () => this.onClosed());
  }

  static async connect(url: string): Promise<CdpConnection> {
    const parsed = new URL(url);
    if (parsed.protocol !== "ws:" ||
        (parsed.hostname !== "127.0.0.1" && parsed.hostname !== "localhost")) {
      throw new Error("chrome_debug_endpoint_invalid");
    }
    const socket = new WebSocket(url);
    await new Promise<void>((resolve, reject) => {
      socket.once("open", () => resolve());
      socket.once("error", () => reject(new Error("chrome_debug_connect_failed")));
    });
    return new CdpConnection(socket);
  }

  isOpen(): boolean {
    return this.socket.readyState === WebSocket.OPEN;
  }

  async send(method: string, params: Record<string, unknown> = {}): Promise<
    Record<string, unknown>
  > {
    if (!this.isOpen()) throw new Error("chrome_debug_disconnected");
    const id = ++this.sequence;
    return await new Promise((resolve, reject) => {
      this.pending.set(id, { method, resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params }), (error) => {
        if (!error) return;
        this.pending.delete(id);
        reject(new Error("chrome_debug_send_failed"));
      });
    });
  }

  close(): void {
    try {
      this.socket.close();
    } catch {
      // Already closed.
    }
    this.onClosed();
  }

  private onMessage(raw: RawData): void {
    let message: CdpResponse;
    try {
      message = JSON.parse(raw.toString()) as CdpResponse;
    } catch {
      return;
    }
    if (!message.id) return;
    const pending = this.pending.get(message.id);
    if (!pending) return;
    this.pending.delete(message.id);
    if (message.error) {
      const detail = typeof message.error.message === "string"
        ? message.error.message.replace(/[^A-Za-z0-9 ._:-]/g, "").slice(0, 120)
        : "unknown";
      pending.reject(new Error(
        `chrome_debug_command_failed:${pending.method}:${detail}`,
      ));
    } else {
      pending.resolve(message.result ?? {});
    }
  }

  private onClosed(): void {
    for (const pending of this.pending.values()) {
      pending.reject(new Error("chrome_debug_disconnected"));
    }
    this.pending.clear();
  }
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
