import {
  BrowserWindow,
  type BrowserWindowConstructorOptions,
} from "electron";

import {
  applyPlatformNavigationPolicy,
  configurePlatformSession,
  platformWebPreferences,
} from "./platform-session";
import { PlatformEndpointRegistry } from "./platform-endpoint-config";
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
  const endpoint = platformEndpointRegistry.current();
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
    latestPlatformProbe = null;
    if (platformWindow === window) platformWindow = null;
  });
  void configurePlatformSession(window.webContents.session)
    .then(() => {
      if (!window.isDestroyed()) {
        startPlatformPageProbe(window);
        return window.loadURL(endpoint.entryUrl);
      }
      return undefined;
    })
    .catch(() => {
      if (!window.isDestroyed()) window.destroy();
    });
  return window;
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
