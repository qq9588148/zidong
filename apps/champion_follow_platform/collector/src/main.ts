import { readFileSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  safeStorage,
  type IpcMainEvent,
  type IpcMainInvokeEvent,
} from "electron";

import { CAPTURE_EVENT_CHUNK_LIMIT } from "./capture-pipeline.js";
import {
  collectorStartupEntryUrl,
  CollectorEntryStore,
} from "./collector-entry.js";
import {
  LocalCollectorServer,
  collectorWindowTitle,
  resolveCollectorConfig,
} from "./collector-mode.js";
import { connectionPageUrl } from "./connection-page.js";
import { capturedEventSchema, type CapturedEvent } from "./contracts.js";
import { configureCollectorDiagnostics } from "./diagnostic-port.js";
import {
  CollectorCredentialStore,
  credentialInputStream,
  parseCredentialImportProcessArgs,
  type CollectorCredential,
} from "./credential-store.js";
import { IdentityStore } from "./identity-store.js";
import { identityToWire } from "./identity-wire.js";
import {
  HistoryBoundaryTracker,
  HistoryPageChunkAssembler,
  type HistoryPageEnvelope,
} from "./history-page.js";
import { AppendOnlyJournal } from "./journal.js";
import { NavigationRecoveryCoordinator } from "./navigation-recovery.js";
import { ignoreBrokenPipe } from "./process-output.js";
import {
  CollectorRuntime,
  bootstrapCollector,
  startupErrorCode,
  type HistoryPage,
} from "./runtime.js";
import {
  HttpCollectorServer,
  type CollectorServerPort,
} from "./server-api.js";
import {
  collectorWebPreferences,
  configureCollectorSession,
  installCollectorWindowPolicy,
  isSecurePlatformNavigation,
  loadPlatformUntilAccepted,
} from "./window-policy.js";

const distRoot = dirname(fileURLToPath(import.meta.url));
const preloadPath = join(distRoot, "preload.cjs");
const pageHookPath = join(distRoot, "page-hook.js");

type BootstrapCredential = CollectorCredential | {
  readonly collector_id: "collector-main-local";
};

ignoreBrokenPipe(process.stdout);
ignoreBrokenPipe(process.stderr);

function strictCaptureBatch(value: unknown): CapturedEvent[] {
  if (
    !Array.isArray(value) ||
    value.length < 1 ||
    value.length > CAPTURE_EVENT_CHUNK_LIMIT
  ) {
    throw new Error("collector_capture_invalid");
  }
  try {
    return value.map((item) => capturedEventSchema.parse(item));
  } catch {
    throw new Error("collector_capture_invalid");
  }
}

async function run(): Promise<void> {
  configureCollectorDiagnostics(
    app.commandLine,
    process.env.CHAMPION_COLLECTOR_LOCAL_DIAGNOSTICS,
  );
  if (!app.requestSingleInstanceLock()) {
    app.quit();
    return;
  }
  await app.whenReady();

  const config = resolveCollectorConfig(process.env);
  const platformUrl = config.platformUrl;
  const runtimeRoot = join(app.getPath("userData"), "main-collector-v1");
  await mkdir(runtimeRoot, { recursive: true, mode: 0o700 });
  const entryStore = new CollectorEntryStore(
    join(runtimeRoot, "platform-entry.json"),
  );
  const entryUrl = collectorStartupEntryUrl(
    await entryStore.load(platformUrl),
    platformUrl,
  );

  const credentialStore = new CollectorCredentialStore(
    join(runtimeRoot, "collector-credential.enc"),
    safeStorage,
  );
  const identityStore = new IdentityStore(
    join(runtimeRoot, "identity-key.enc"),
    safeStorage,
  );
  const journal = new AppendOnlyJournal(runtimeRoot);
  const importMode = parseCredentialImportProcessArgs(
    process.argv,
    app.isPackaged,
  );

  let namespaceKey: Buffer | null = null;
  let collectorId: string | null = null;
  let collectorRuntime: CollectorRuntime | null = null;
  let collectorWindow: BrowserWindow | null = null;
  let collectorWindowMayClose = false;
  let loopController: AbortController | null = null;
  let loopTasks: Promise<void>[] = [];
  let historyController: AbortController | null = null;
  let historyRecoveryTask: Promise<void> | null = null;
  let navigationRecovery: NavigationRecoveryCoordinator | null = null;
  let statusTimer: ReturnType<typeof setInterval> | null = null;
  let captureStopped = false;
  let historyRequestNumber = 0;
  let historyCursorMs = Date.now() + 1;
  const historyBoundaries = new HistoryBoundaryTracker();
  let historyRecoveryStarted = false;
  let historyGeneration = 0;
  let cleaned = false;
  let pendingHistory:
    | {
        requestId: string;
        assembler: HistoryPageChunkAssembler;
        resolve(value: HistoryPageEnvelope): void;
        reject(error: Error): void;
      }
    | null = null;

  function recordCaptureStatus(status: string): void {
    void writeFile(join(runtimeRoot, "capture-status.txt"), `${status}\n`, {
      encoding: "utf8",
      mode: 0o600,
    }).catch(() => undefined);
  }

  function recordCaptureStop(reason: string): void {
    captureStopped = true;
    recordCaptureStatus(reason);
  }

  recordCaptureStatus("starting");

  app.on("second-instance", () => {
    if (!collectorWindow || collectorWindow.isDestroyed()) return;
    collectorWindow.show();
    collectorWindow.focus();
  });

  function validSender(event: IpcMainInvokeEvent | IpcMainEvent): boolean {
    const frameUrl = event.senderFrame?.url ?? "";
    return (
      collectorWindow !== null &&
      event.sender.id === collectorWindow.webContents.id &&
      event.senderFrame === collectorWindow.webContents.mainFrame &&
      isSecurePlatformNavigation(frameUrl)
    );
  }

  function requireRuntime(): CollectorRuntime {
    if (!collectorRuntime || captureStopped) {
      throw new Error("collector_capture_unavailable");
    }
    return collectorRuntime;
  }

  function cancelPendingHistory(code: string): void {
    const pending = pendingHistory;
    if (!pending) return;
    pendingHistory = null;
    pending.reject(new Error(code));
  }

  function historyCurrent(generation: number, signal: AbortSignal): boolean {
    return (
      !signal.aborted &&
      navigationRecovery?.acceptsPageState(generation) === true
    );
  }

  function waitForRetry(signal: AbortSignal): Promise<void> {
    if (signal.aborted) return Promise.resolve();
    return new Promise((resolve) => {
      const timer = setTimeout(done, 500);
      function done(): void {
        clearTimeout(timer);
        signal.removeEventListener("abort", done);
        resolve();
      }
      signal.addEventListener("abort", done, { once: true });
    });
  }

  async function readHistoryPage(
    generation: number,
    signal: AbortSignal,
  ): Promise<HistoryPage> {
    for (;;) {
      if (
        captureStopped ||
        !collectorWindow ||
        !historyCurrent(generation, signal)
      ) {
        throw new Error("collector_history_read_failed");
      }
      if (!requireRuntime().historyRecoveryOpen()) {
        return {
          events: [],
          ...historyBoundaries.observe(0, null),
        };
      }
      const requestId = `history-${++historyRequestNumber}`;
      const response = new Promise<HistoryPageEnvelope>((resolve, reject) => {
        const finish = <T>(complete: (value: T) => void, value: T): void => {
          clearTimeout(timer);
          signal.removeEventListener("abort", abort);
          complete(value);
        };
        const timer = setTimeout(() => {
          if (pendingHistory?.requestId !== requestId) return;
          pendingHistory = null;
          finish(reject, new Error("collector_history_read_failed"));
        }, 5_000);
        const abort = (): void => {
          if (pendingHistory?.requestId !== requestId) return;
          pendingHistory = null;
          finish(reject, new Error("collector_history_read_failed"));
        };
        pendingHistory = {
          requestId,
          assembler: new HistoryPageChunkAssembler(requestId),
          resolve(value) {
            finish(resolve, value);
          },
          reject(error) {
            finish(reject, error);
          },
        };
        signal.addEventListener("abort", abort, { once: true });
      });
      const request = {
        marker: "champion-follow-public-room-v1",
        kind: "pull-history",
        requestId,
        timetag: historyCursorMs,
      };
      let page: HistoryPageEnvelope;
      try {
        [, page] = await Promise.all([
          collectorWindow.webContents.executeJavaScript(
            `window.postMessage(${JSON.stringify(request)}, location.origin)`,
          ),
          response,
        ]);
      } catch {
        if (pendingHistory?.requestId === requestId) {
          cancelPendingHistory("collector_history_read_failed");
        }
        await response.catch(() => undefined);
        if (!historyCurrent(generation, signal)) {
          throw new Error("collector_history_read_failed");
        }
        if (!requireRuntime().historyRecoveryOpen()) {
          return {
            events: [],
            ...historyBoundaries.observe(0, null),
          };
        }
        await waitForRetry(signal);
        continue;
      }
      if (page.messageCount === 0) {
        return {
          events: [],
          ...historyBoundaries.observe(0, null),
        };
      }
      if (page.minSourceMs === null) {
        throw new Error("collector_history_response_invalid");
      }
      const nextCursor = page.minSourceMs - 1;
      if (nextCursor < 0 || nextCursor >= historyCursorMs) {
        throw new Error("collector_history_cursor_stalled");
      }
      const boundary = historyBoundaries.observe(
        page.messageCount,
        page.minSourceMs,
      );
      historyCursorMs = nextCursor;
      if (page.events.length > 0) {
        return { events: page.events, ...boundary };
      }
    }
  }

  function startCollectorLoops(): void {
    if (config.mode === "local") return;
    if (captureStopped || loopController) return;
    const runtime = requireRuntime();
    const controller = new AbortController();
    loopController = controller;
    loopTasks = [
      runtime.runUploads(controller.signal),
      runtime.runHeartbeats(controller.signal, () =>
        runtime.currentHeartbeat(),
      ),
    ];
    for (const task of loopTasks) {
      void task.catch(() => {
        if (controller.signal.aborted) return;
        runtime.markCaptureUnhealthy();
        recordCaptureStop("collector_loop_failed");
        controller.abort();
      });
    }
  }

  async function stopCollectorLoops(): Promise<void> {
    const controller = loopController;
    const tasks = loopTasks;
    loopController = null;
    loopTasks = [];
    controller?.abort();
    await Promise.allSettled(tasks);
  }

  async function stopHistoryRecovery(): Promise<void> {
    const controller = historyController;
    const task = historyRecoveryTask;
    historyController = null;
    historyRecoveryTask = null;
    controller?.abort();
    cancelPendingHistory("collector_history_read_failed");
    if (task) await task.catch(() => undefined);
  }

  function startHistoryRecovery(generation: number): void {
    if (
      historyRecoveryStarted ||
      captureStopped ||
      historyGeneration !== generation ||
      navigationRecovery?.acceptsPageState(generation) !== true ||
      navigationRecovery.historyReady(generation) !== true
    ) {
      return;
    }
    historyRecoveryStarted = true;
    const controller = new AbortController();
    historyController = controller;
    const runtime = requireRuntime();
    const task = config.mode === "local"
      ? runtime.startLiveCollectionWithoutHistory()
      : runtime.recoverHistory(() =>
          readHistoryPage(generation, controller.signal),
        );
    historyRecoveryTask = task;
    void task
      .then(() => {
        if (!historyCurrent(generation, controller.signal)) return;
        navigationRecovery?.historyRecovered(generation);
      })
      .catch(() => {
        if (controller.signal.aborted || historyGeneration !== generation) {
          return;
        }
        collectorRuntime?.markCaptureUnhealthy();
        recordCaptureStop("history_recovery_failed");
        void stopCollectorLoops();
      })
      .finally(() => {
        if (historyController === controller) historyController = null;
        if (historyRecoveryTask === task) historyRecoveryTask = null;
      });
  }

  async function cleanupRuntime(): Promise<void> {
    if (cleaned) return;
    cleaned = true;
    captureStopped = true;
    if (statusTimer !== null) clearInterval(statusTimer);
    statusTimer = null;
    await navigationRecovery?.stop();
    await stopHistoryRecovery();
    await stopCollectorLoops();
    ipcMain.removeHandler("collector:identity");
    ipcMain.removeHandler("collector:append");
    ipcMain.removeHandler("collector:state");
    ipcMain.removeHandler("collector:history-page");
    ipcMain.removeAllListeners("collector:unsafe-state");
    ipcMain.removeAllListeners("collector:history-error");
    cancelPendingHistory("collector_stopping");
    namespaceKey?.fill(0);
    namespaceKey = null;
    await journal.close().catch(() => undefined);
  }

  const result = await bootstrapCollector<
    BootstrapCredential,
    CollectorServerPort,
    BrowserWindow
  >({
    async loadIdentity() {
      namespaceKey = await identityStore.loadOrCreate();
    },
    async loadCredential() {
      if (config.mode === "local") {
        const credential = { collector_id: "collector-main-local" } as const;
        collectorId = credential.collector_id;
        return credential;
      }
      let credential: CollectorCredential;
      if (importMode.kind === "file") {
        credential = await credentialStore.importFromFile(importMode.path);
      } else if (importMode.kind === "stdin") {
        credential = await credentialStore.importFromStdin(
          credentialInputStream(
            process.platform,
            process.stdin,
            (input) => readFileSync(input),
            process.env.CHAMPION_COLLECTOR_STDIN_PIPE,
          ),
        );
      } else {
        credential = await credentialStore.load();
      }
      collectorId = credential.collector_id;
      return credential;
    },
    createServer(credential) {
      if (config.mode === "local") return new LocalCollectorServer(journal);
      if (!("bearer" in credential)) throw new Error("collector_config_invalid");
      return new HttpCollectorServer(config.serverUrl!, credential.bearer);
    },
    async openJournal() {
      await journal.start();
    },
    async reconcileSession(server) {
      if (!collectorId) throw new Error("collector_credential_invalid");
      collectorRuntime = new CollectorRuntime({
        collectorId,
        journal,
        server,
        stopCapture(reason) {
          recordCaptureStop(reason);
        },
      });
      await collectorRuntime.reconcileSession();
    },
    async openWindow() {
      const pageHook = await readFile(pageHookPath, "utf8");
      const window = new BrowserWindow({
        width: 460,
        height: 820,
        minWidth: 390,
        minHeight: 680,
        show: true,
        autoHideMenuBar: true,
        backgroundColor: "#0a0f1a",
        title: collectorWindowTitle({ healthy: true, issue: null, saved: 0 }),
        webPreferences: collectorWebPreferences(preloadPath),
      });
      collectorWindow = window;
      let platformConnected = false;
      window.on("close", (event) => {
        if (collectorWindowMayClose) return;
        event.preventDefault();
        window.hide();
      });
      const updateTitle = (): void => {
        if (window.isDestroyed()) return;
        if (!platformConnected) {
          window.setTitle("NG 主采集 · 正在连接（自动重试）");
          return;
        }
        const heartbeat = collectorRuntime?.currentHeartbeat();
        window.setTitle(collectorWindowTitle({
          healthy: captureStopped ? false : (heartbeat?.capture_healthy ?? true),
          issue: heartbeat?.issue ?? null,
          saved: journal.lastSeq,
        }));
      };
      statusTimer = setInterval(updateTitle, 1_000);
      updateTitle();
      recordCaptureStatus("waiting_for_page");
      await configureCollectorSession(
        window.webContents.session,
        process.versions.chrome,
        process.env.CHAMPION_PLATFORM_PROXY_URL,
      );
      installCollectorWindowPolicy(
        window.webContents.session,
        window.webContents,
      );
      const rememberEntry = (_event: unknown, url: string): void => {
        void entryStore.save(url);
      };
      window.webContents.on("did-navigate", rememberEntry);
      window.webContents.on("did-navigate-in-page", rememberEntry);
      await window.loadURL(connectionPageUrl(0));
      navigationRecovery = new NavigationRecoveryCoordinator({
        async pause() {
          await stopHistoryRecovery();
          await stopCollectorLoops();
          await requireRuntime().suspendForReconnect();
        },
        resetHistory(generation) {
          historyGeneration = generation;
          historyCursorMs = Date.now() + 1;
          historyBoundaries.reset();
          historyRecoveryStarted = false;
        },
        async reconcileSession() {
          await requireRuntime().reconcileSession();
        },
        async injectPageHook() {
          await window.webContents.executeJavaScript(pageHook);
        },
        async waitForSessionRetry(signal) {
          await waitForRetry(signal);
        },
        sessionReady(generation) {
          if (collectorRuntime?.currentHeartbeat().phase === "BETTING") {
            startHistoryRecovery(generation);
          }
        },
        startLoops() {
          startCollectorLoops();
        },
        failClosed() {
          collectorRuntime?.markCaptureUnhealthy();
          recordCaptureStop("navigation_recovery_failed");
          loopController?.abort();
          historyController?.abort();
        },
      });
      window.webContents.on("did-frame-finish-load", (_event, isMainFrame) => {
        if (
          !isMainFrame ||
          !isSecurePlatformNavigation(window.webContents.getURL())
        ) {
          return;
        }
        void navigationRecovery?.committedMainFrame().done.catch(() => undefined);
      });

      ipcMain.handle("collector:identity", (event) => {
        if (!validSender(event) || !namespaceKey) {
          throw new Error("collector_ipc_rejected");
        }
        return identityToWire(namespaceKey);
      });
      ipcMain.handle("collector:append", async (event, value: unknown) => {
        if (!validSender(event)) throw new Error("collector_ipc_rejected");
        const generation = navigationRecovery?.currentGeneration ?? 0;
        if (navigationRecovery?.acceptsPageState(generation) !== true) {
          return journal.lastSeq;
        }
        return requireRuntime().ingest(strictCaptureBatch(value));
      });
      ipcMain.handle("collector:state", async (event, value: unknown) => {
        if (!validSender(event)) throw new Error("collector_ipc_rejected");
        const generation = navigationRecovery?.currentGeneration ?? 0;
        if (navigationRecovery?.acceptsPageState(generation) !== true) {
          return journal.lastSeq;
        }
        const state = value as Record<string, unknown>;
        const issue = state?.issue;
        const phase = state?.phase;
        const countdownMs = state?.countdownMs;
        const observedAtMs = state?.observedAtMs;
        if (
          !state ||
          Object.keys(state).sort().join(",") !==
            "countdownMs,issue,observedAtMs,phase" ||
          typeof issue !== "string" ||
          !/^\d{8,16}$/.test(issue) ||
          (phase !== "BETTING" && phase !== "CLOSED" && phase !== "UNKNOWN") ||
          typeof countdownMs !== "number" ||
          !Number.isSafeInteger(countdownMs) ||
          countdownMs < 0 ||
          typeof observedAtMs !== "number" ||
          !Number.isSafeInteger(observedAtMs) ||
          observedAtMs < 0
        ) {
          throw new Error("collector_page_state_invalid");
        }
        const sequence = await requireRuntime().observePageState({
          issue,
          phase,
          countdownMs,
          observedAtMs,
        });
        recordCaptureStatus("capturing");
        if (phase === "BETTING") startHistoryRecovery(generation);
        return sequence;
      });
      ipcMain.handle("collector:history-page", (event, value: unknown) => {
        if (!validSender(event)) throw new Error("collector_ipc_rejected");
        const requestId =
          value !== null && typeof value === "object" && !Array.isArray(value)
            ? (value as Record<string, unknown>).requestId
            : null;
        const pending = pendingHistory;
        if (!pending || pending.requestId !== requestId) {
          return journal.lastSeq;
        }
        let page: HistoryPageEnvelope | null;
        try {
          page = pending.assembler.push(value);
        } catch {
          pendingHistory = null;
          pending.reject(new Error("collector_history_response_invalid"));
          throw new Error("collector_history_response_invalid");
        }
        if (page === null) return journal.lastSeq;
        pendingHistory = null;
        pending.resolve(page);
        return journal.lastSeq;
      });
      ipcMain.on("collector:unsafe-state", (event, reason: unknown) => {
        if (!validSender(event)) return;
        collectorRuntime?.markCaptureUnhealthy();
        const safeReason = typeof reason === "string" &&
            /^[a-z0-9_]{1,64}$/.test(reason)
          ? reason
          : "page_state_unsafe";
        recordCaptureStop(safeReason);
      });
      ipcMain.on("collector:history-error", (event, requestId: unknown) => {
        if (
          !validSender(event) ||
          typeof requestId !== "string" ||
          pendingHistory?.requestId !== requestId
        ) {
          return;
        }
        const pending = pendingHistory;
        pendingHistory = null;
        pending.reject(new Error("collector_history_read_failed"));
      });

      void loadPlatformUntilAccepted(
        async () => {
          await window.loadURL(entryUrl);
          platformConnected = true;
          recordCaptureStatus("page_connected");
          updateTitle();
        },
        () => !window.isDestroyed() && !cleaned,
        async (retryCount) => {
          platformConnected = false;
          recordCaptureStatus("waiting_for_page");
          updateTitle();
          await window.loadURL(connectionPageUrl(retryCount)).catch(
            () => undefined,
          );
          await new Promise((resolve) => setTimeout(resolve, 3_000));
        },
      );
      return window;
    },
    startLoops(_server, window) {
      startCollectorLoops();
      window.on("closed", () => {
        if (collectorWindow === window) collectorWindow = null;
      });
    },
    cleanup: cleanupRuntime,
  });

  collectorWindow = result.window;

  let closing = false;
  app.on("before-quit", (event) => {
    if (closing) return;
    event.preventDefault();
    closing = true;
    collectorWindowMayClose = true;
    void cleanupRuntime().finally(() => app.exit(0));
  });
}

void run().catch(async (error: unknown) => {
  const code =
    error instanceof Error && error.message === "collector_config_invalid"
      ? error.message
      : startupErrorCode(error);
  try {
    const root = join(app.getPath("userData"), "main-collector-v1");
    await mkdir(root, { recursive: true, mode: 0o700 });
    await writeFile(join(root, "startup-error.txt"), `${code}\n`, {
      encoding: "utf8",
      mode: 0o600,
    });
  } catch {
    // The visible error still reports the safe startup code.
  }
  try {
    dialog.showErrorBox(
      "NG 主采集器未能启动",
      `错误编号：${code}\n登录资料和采集账本没有被删除。`,
    );
  } catch {
    // Electron may already be shutting down.
  }
  try {
    process.stderr.write(`${code}\n`);
  } catch {
    // Packaged GUI launches may not have an attached output pipe.
  }
  app.exit(1);
});
