import { app, BrowserWindow, ipcMain } from "electron";
import { rmSync } from "node:fs";
import { join } from "node:path";

import { AppController, initialRuntimeState } from "./app-controller";
import { DeviceAuthClient, JsonDeviceIdentityStore } from "./auth-client";
import { registerClientIpc } from "./ipc-handlers";
import { createNativeHelper } from "./native-helper-runtime";
import { desktopPaths } from "./paths";
import {
  allowPlatformWindowCloseForExit,
  getLatestPlatformPageProbe,
  isPlatformWindowOpen,
  openNgPlatformWindow,
} from "./platform-window";

export { initialRuntimeState };

const DEFAULT_SERVER_BASE_URL = "https://101.37.172.66:8443";
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

function registerAppIpc(controller: AppController): void {
  registerClientIpc(ipcMain, controller);
  ipcMain.handle("champion:get-platform-window-state", () => ({
    open: isPlatformWindowOpen(),
    probe: getLatestPlatformPageProbe(),
  }));
  ipcMain.handle("champion:open-platform-login", () => {
    openNgPlatformWindow();
    return { ok: true, open: true };
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
      const authClient = new DeviceAuthClient({
        baseUrl: DEFAULT_SERVER_BASE_URL,
        helper: createNativeHelper(),
        store: new JsonDeviceIdentityStore(
          join(desktopPaths().profile, "device-identity.json"),
        ),
      });
      const controller = new AppController(authClient);
      registerAppIpc(controller);
      openMainWindow();
      void controller.initialize();
      app.on("activate", () => openMainWindow());
    });
  }
}
