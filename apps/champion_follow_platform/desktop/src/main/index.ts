import { app, BrowserWindow, ipcMain } from "electron";
import { randomUUID } from "node:crypto";
import { rmSync } from "node:fs";
import { join } from "node:path";

import {
  allowPlatformWindowCloseForExit,
  getLatestPlatformPageProbe,
  isPlatformWindowOpen,
  openNgPlatformWindow,
} from "./platform-window";

export type RuntimeState = {
  generation: string;
  autoBet: "OFF" | "ON";
  executionBlock: "STARTUP_SYNC_REQUIRED" | null;
  highestTask: null;
};

export const initialRuntimeState = (): RuntimeState => ({
  generation: randomUUID(),
  autoBet: "OFF",
  executionBlock: "STARTUP_SYNC_REQUIRED",
  highestTask: null,
});

let runtimeState = initialRuntimeState();
let mainWindow: BrowserWindow | null = null;
let appIsQuitting = false;

export function createMainWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1180,
    height: 760,
    minWidth: 900,
    minHeight: 620,
    show: false,
    backgroundColor: "#07111f",
    title: "Champion Follow",
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      spellcheck: false,
    },
  });

  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  return window;
}

function registerOfflineIpc(): void {
  ipcMain.handle("champion:get-state", () => runtimeState);
  ipcMain.handle("champion:get-platform-window-state", () => ({
    open: isPlatformWindowOpen(),
    probe: getLatestPlatformPageProbe(),
  }));
  ipcMain.handle("champion:open-platform-login", () => {
    openNgPlatformWindow();
    return { ok: true, open: true };
  });
  ipcMain.handle("champion:set-auto-bet", (_event, enabled: unknown) => {
    // Offline shell cannot arm execution. It may only reaffirm the safe OFF state.
    if (enabled !== false) {
      return runtimeState;
    }
    runtimeState = { ...runtimeState, autoBet: "OFF" };
    return runtimeState;
  });
  ipcMain.handle("champion:quit-app", () => {
    appIsQuitting = true;
    allowPlatformWindowCloseForExit();
    app.quit();
    return { ok: true };
  });
}

function openMainWindow(): BrowserWindow {
  if (mainWindow !== null && !mainWindow.isDestroyed()) {
    mainWindow.show();
    mainWindow.focus();
    return mainWindow;
  }
  const window = createMainWindow();
  mainWindow = window;
  window.on("close", (event) => {
    if (!appIsQuitting) {
      event.preventDefault();
      window.hide();
    }
  });
  window.on("closed", () => {
    if (mainWindow === window) mainWindow = null;
  });
  window.once("ready-to-show", () => window.show());
  void window.loadFile(join(__dirname, "../../dist-renderer/index.html"));
  return window;
}

function removeObsoleteProtectedSessionSnapshots(): void {
  const directory = join(
    app.getPath("userData"),
    "Partitions",
    "champion-platform-local-desktop",
    "Protected Session",
  );
  for (const name of ["cookies.enc", "session-storage.enc"]) {
    rmSync(join(directory, name), { force: true });
  }
}

if (process.env.VITEST !== "true" && app) {
  if (!app.requestSingleInstanceLock()) {
    app.quit();
  } else {
    app.commandLine.appendSwitch("force-renderer-accessibility");
    app.on("before-quit", () => {
      appIsQuitting = true;
      allowPlatformWindowCloseForExit();
    });
    app.on("second-instance", () => openMainWindow());
    app.whenReady().then(() => {
      removeObsoleteProtectedSessionSnapshots();
      registerOfflineIpc();
      openMainWindow();
      app.on("activate", () => openMainWindow());
    });
  }
}
