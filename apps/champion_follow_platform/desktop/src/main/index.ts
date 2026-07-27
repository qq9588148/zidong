import { app, BrowserWindow, ipcMain } from "electron";
import { randomUUID } from "node:crypto";
import { join } from "node:path";

import {
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
}

if (process.env.VITEST !== "true" && app) {
  app.whenReady().then(() => {
    registerOfflineIpc();
    const window = createMainWindow();
    window.once("ready-to-show", () => window.show());
    void window.loadFile(
      join(__dirname, "../../dist-renderer/index.html"),
    );

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        const reopened = createMainWindow();
        reopened.once("ready-to-show", () => reopened.show());
        void reopened.loadFile(
          join(__dirname, "../../dist-renderer/index.html"),
        );
      }
    });
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") {
      app.quit();
    }
  });
}
