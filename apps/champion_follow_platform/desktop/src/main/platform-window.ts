import {
  BrowserWindow,
  type BrowserWindowConstructorOptions,
  type WebFrameMain,
} from "electron";
import { Buffer } from "node:buffer";
import { join } from "node:path";

import {
  applyPlatformNavigationPolicy,
  configurePlatformSession,
  platformWebPreferences,
} from "./platform-session";
import { PlatformEndpointRegistry } from "./platform-endpoint-config";
import { desktopPaths } from "./paths";
import {
  ProtectedSessionSnapshotStore,
  type SessionSnapshotWriteResult,
  type SessionStorageEntry,
} from "./platform-session-snapshot";
import {
  mergePlatformPageProbes,
  parsePlatformPageProbe,
  platformPageProbeScript,
  type PlatformPageProbe,
} from "./platform-page-probe";

export const platformEndpointRegistry = new PlatformEndpointRegistry();

let platformWindow: BrowserWindow | null = null;
let platformWindowMayClose = false;
let latestPlatformProbe: PlatformPageProbe | null = null;
let platformProbeTimer: ReturnType<typeof setInterval> | null = null;
let platformProbeInFlight = false;
let platformSessionTimer: ReturnType<typeof setInterval> | null = null;
let platformSessionInFlight = false;

export type PlatformSessionPersistenceState = Readonly<{
  encryptionAvailable: boolean | null;
  snapshotLoaded: boolean;
  snapshotPresent: boolean;
  pageOriginAllowed: boolean | null;
  captureStatus: "IDLE" | "SAVED" | "UNCHANGED" | "SKIPPED" | "FAILED";
  restoreStatus: "IDLE" | "RESTORED" | "NOT_FOUND" | "SKIPPED" | "FAILED";
  errorCode: string | null;
}>;

let latestPlatformSessionState: PlatformSessionPersistenceState =
  emptyPlatformSessionState();

export function platformWindowOptions(): BrowserWindowConstructorOptions {
  return {
    width: 460,
    height: 820,
    minWidth: 390,
    minHeight: 680,
    show: false,
    autoHideMenuBar: true,
    backgroundColor: "#0a0f1a",
    title: "NG 平台登录 - Champion Follow",
    webPreferences: platformWebPreferences("local-desktop"),
  };
}

export function isPlatformWindowOpen(): boolean {
  return platformWindow !== null && !platformWindow.isDestroyed();
}

export function getLatestPlatformPageProbe(): PlatformPageProbe | null {
  return latestPlatformProbe;
}

export function getPlatformSessionPersistenceState(): PlatformSessionPersistenceState {
  return { ...latestPlatformSessionState };
}

export function allowPlatformWindowCloseForExit(): void {
  platformWindowMayClose = true;
}

export function openNgPlatformWindow(): BrowserWindow {
  if (isPlatformWindowOpen()) {
    platformWindow!.show();
    platformWindow!.focus();
    return platformWindow!;
  }

  const window = new BrowserWindow(platformWindowOptions());
  platformWindow = window;
  latestPlatformProbe = null;
  latestPlatformSessionState = emptyPlatformSessionState();
  const endpoint = platformEndpointRegistry.current();
  const protectedSession = new ProtectedSessionSnapshotStore(
    platformSessionSnapshotPath(),
  );
  applyPlatformNavigationPolicy(window.webContents, endpoint.allowedOrigins);
  window.once("ready-to-show", () => window.show());
  window.on("close", (event) => {
    if (!platformWindowMayClose) {
      event.preventDefault();
      window.hide();
    }
  });
  window.on("closed", () => {
    stopPlatformPageProbe();
    stopPlatformSessionCapture();
    latestPlatformProbe = null;
    if (platformWindow === window) platformWindow = null;
  });
  void configurePlatformSession(window.webContents.session)
    .then(async () => {
      if (!window.isDestroyed()) {
        await protectedSession.load();
        updatePlatformSessionStateFromStore(protectedSession);
        installPlatformSessionRestore(
          window,
          protectedSession,
          endpoint.allowedOrigins,
        );
        await window.loadURL(endpoint.entryUrl);
        if (!window.isDestroyed()) {
          startPlatformPageProbe(window);
          startPlatformSessionCapture(
            window,
            protectedSession,
            endpoint.allowedOrigins,
          );
        }
      }
    })
    .catch(() => {
      if (!window.isDestroyed()) window.destroy();
    });
  return window;
}

function installPlatformSessionRestore(
  window: BrowserWindow,
  store: ProtectedSessionSnapshotStore,
  allowedOrigins: readonly string[],
): void {
  let restoreAttempted = false;
  window.webContents.on("did-finish-load", () => {
    if (restoreAttempted || window.isDestroyed()) return;
    restoreAttempted = true;
    void restoreFrameSessionStorage(
      window.webContents.mainFrame,
      store,
      allowedOrigins,
    ).then((restored) => {
      latestPlatformSessionState = {
        ...latestPlatformSessionState,
        restoreStatus: restored ? "RESTORED" :
          latestPlatformSessionState.snapshotPresent ? "SKIPPED" : "NOT_FOUND",
      };
      if (restored && !window.isDestroyed()) window.webContents.reload();
    }).catch(() => {
      latestPlatformSessionState = {
        ...latestPlatformSessionState,
        restoreStatus: "FAILED",
        errorCode: "SESSION_RESTORE_FAILED",
      };
    });
  });
}

function startPlatformSessionCapture(
  window: BrowserWindow,
  store: ProtectedSessionSnapshotStore,
  allowedOrigins: readonly string[],
): void {
  stopPlatformSessionCapture();
  const run = async () => {
    if (platformSessionInFlight || window.isDestroyed() ||
        window.webContents.isLoadingMainFrame()) return;
    platformSessionInFlight = true;
    try {
      const result = await captureFrameSessionStorage(
        window.webContents.mainFrame,
        store,
        allowedOrigins,
      );
      if (result === "ORIGIN_NOT_ALLOWED") {
        latestPlatformSessionState = {
          ...latestPlatformSessionState,
          pageOriginAllowed: false,
          captureStatus: "SKIPPED",
          errorCode: "PAGE_ORIGIN_NOT_ALLOWED",
        };
      } else {
        updatePlatformSessionStateFromStore(store, true, result);
      }
    } catch {
      latestPlatformSessionState = {
        ...latestPlatformSessionState,
        captureStatus: "FAILED",
        errorCode: "SESSION_CAPTURE_FAILED",
      };
    } finally {
      platformSessionInFlight = false;
    }
  };
  // The first page navigation can interrupt executeJavaScript without settling
  // its promise. Wait for the loaded page before the first capture so one stale
  // in-flight attempt cannot suppress every later snapshot.
  platformSessionTimer = setInterval(() => void run(), 1_000);
}

function stopPlatformSessionCapture(): void {
  if (platformSessionTimer !== null) clearInterval(platformSessionTimer);
  platformSessionTimer = null;
  platformSessionInFlight = false;
}

async function captureFrameSessionStorage(
  frame: WebFrameMain,
  store: ProtectedSessionSnapshotStore,
  allowedOrigins: readonly string[],
): Promise<SessionSnapshotWriteResult | "ORIGIN_NOT_ALLOWED"> {
  const result = await frame.executeJavaScript(`(() => {
    const entries = [];
    for (let index = 0; index < sessionStorage.length; index += 1) {
      const key = sessionStorage.key(index);
      if (typeof key !== "string") continue;
      const value = sessionStorage.getItem(key);
      if (typeof value === "string") entries.push([key, value]);
    }
    return { origin: location.origin, entries };
  })()`);
  if (!isObject(result) || typeof result.origin !== "string" ||
      !allowedOrigins.includes(result.origin) || !Array.isArray(result.entries)) {
    return "ORIGIN_NOT_ALLOWED";
  }
  return store.replaceOrigin(
    result.origin,
    result.entries as SessionStorageEntry[],
  );
}

async function restoreFrameSessionStorage(
  frame: WebFrameMain,
  store: ProtectedSessionSnapshotStore,
  allowedOrigins: readonly string[],
): Promise<boolean> {
  let origin: string;
  try {
    origin = new URL(frame.url).origin;
  } catch {
    return false;
  }
  if (!allowedOrigins.includes(origin)) return false;
  const entries = store.entriesForOrigin(origin);
  if (entries.length === 0) return false;
  const payload = Buffer.from(JSON.stringify(entries), "utf8").toString("base64");
  const restored = await frame.executeJavaScript(`(() => {
    const bytes = Uint8Array.from(atob(${JSON.stringify(payload)}),
      (character) => character.charCodeAt(0));
    const entries = JSON.parse(new TextDecoder().decode(bytes));
    sessionStorage.clear();
    for (const [key, value] of entries) sessionStorage.setItem(key, value);
    return entries.length;
  })()`);
  return restored === entries.length;
}

function platformSessionSnapshotPath(): string {
  return join(
    desktopPaths().profile,
    "Partitions",
    "champion-platform-local-desktop",
    "Protected Session",
    "session-storage.enc",
  );
}

function startPlatformPageProbe(window: BrowserWindow): void {
  stopPlatformPageProbe();
  const run = async () => {
    if (platformProbeInFlight || window.isDestroyed() ||
        window.webContents.isLoadingMainFrame()) return;
    platformProbeInFlight = true;
    try {
      const script = platformPageProbeScript();
      const values: unknown[] = [];
      values.push(await window.webContents.executeJavaScriptInIsolatedWorld(
        1001,
        [{ code: script }],
      ));
      for (const frame of window.webContents.mainFrame.frames) {
        if (frame.isDestroyed()) continue;
        try {
          values.push(await frame.executeJavaScript(script));
        } catch {
          // A frame may navigate while the read-only probe is running.
        }
      }
      const probes = values
        .map(parsePlatformPageProbe)
        .filter((value): value is PlatformPageProbe => value !== null);
      latestPlatformProbe = probes.length > 0
        ? mergePlatformPageProbes(probes)
        : null;
    } catch {
      latestPlatformProbe = null;
    } finally {
      platformProbeInFlight = false;
    }
  };
  void run();
  platformProbeTimer = setInterval(() => void run(), 1_000);
}

function stopPlatformPageProbe(): void {
  if (platformProbeTimer !== null) clearInterval(platformProbeTimer);
  platformProbeTimer = null;
  platformProbeInFlight = false;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function emptyPlatformSessionState(): PlatformSessionPersistenceState {
  return {
    encryptionAvailable: null,
    snapshotLoaded: false,
    snapshotPresent: false,
    pageOriginAllowed: null,
    captureStatus: "IDLE",
    restoreStatus: "IDLE",
    errorCode: null,
  };
}

function updatePlatformSessionStateFromStore(
  store: ProtectedSessionSnapshotStore,
  pageOriginAllowed: boolean | null = null,
  writeResult?: SessionSnapshotWriteResult,
): void {
  const diagnostics = store.getDiagnostics();
  latestPlatformSessionState = {
    ...latestPlatformSessionState,
    encryptionAvailable: diagnostics.encryptionAvailable,
    snapshotLoaded: diagnostics.loaded,
    snapshotPresent: diagnostics.snapshotPresent,
    pageOriginAllowed,
    captureStatus: writeResult === "SAVED" ? "SAVED" :
      writeResult === "UNCHANGED" ? "UNCHANGED" :
      writeResult === "ENCRYPTION_UNAVAILABLE" ? "SKIPPED" :
      latestPlatformSessionState.captureStatus,
    errorCode: diagnostics.errorCode,
  };
}
