import { app, BrowserWindow, ipcMain } from "electron";
import { join } from "node:path";

import { AppController, initialRuntimeState } from "./app-controller";
import { DeviceAuthClient, JsonDeviceIdentityStore } from "./auth-client";
import { registerClientIpc } from "./ipc-handlers";
import { createNativeHelper } from "./native-helper-runtime";
import { desktopPaths } from "./paths";
import { DesktopExecutionRuntime } from "./execution-runtime";
import { ReadonlySignalFeed } from "./signal-feed";
import { TrustedTaskSigningKeys } from "./task-contract";
import {
  allowPlatformWindowCloseForExit,
  getLatestPlatformPageProbe,
  getPlatformSessionPersistenceState,
  isPlatformWindowOpen,
  openNgPlatformWindow,
  platformEndpointRegistry,
} from "./platform-window";

export { initialRuntimeState };

const DEFAULT_SERVER_BASE_URL = "https://101.37.172.66:8443";
let mainWindow: BrowserWindow | null = null;
let appIsQuitting = false;
let signalFeed: ReadonlySignalFeed | null = null;
let executionRuntime: DesktopExecutionRuntime | null = null;

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
    session: getPlatformSessionPersistenceState(),
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

if (process.env.VITEST !== "true" && app) {
  if (!app.requestSingleInstanceLock()) {
    app.quit();
  } else {
    app.commandLine.appendSwitch("force-renderer-accessibility");
    app.on("before-quit", () => {
      appIsQuitting = true;
      signalFeed?.stop();
      executionRuntime?.stop();
      allowPlatformWindowCloseForExit();
    });
    app.on("second-instance", () => openMainWindow());
    app.whenReady().then(() => {
      const helper = createNativeHelper();
      const authClient = new DeviceAuthClient({
        baseUrl: DEFAULT_SERVER_BASE_URL,
        helper,
        store: new JsonDeviceIdentityStore(
          join(desktopPaths().profile, "device-identity.json"),
        ),
      });
      signalFeed = new ReadonlySignalFeed({
        serverBaseUrl: DEFAULT_SERVER_BASE_URL,
        auth: authClient,
        periodId: () => getLatestPlatformPageProbe()?.currentPeriodId ?? null,
      });
      let controller: AppController;
      executionRuntime = new DesktopExecutionRuntime({
        auth: authClient,
        signals: signalFeed,
        helper,
        journalDirectory: desktopPaths().journal,
        generation: () => controller.getState().generation,
      });
      controller = new AppController(authClient, signalFeed, executionRuntime);
      registerAppIpc(controller);
      openMainWindow();
      signalFeed.start();
      void controller.initialize().then(async () => {
        try {
          const keys = TrustedTaskSigningKeys.fromResponse(
            await authClient.taskSigningKeys(),
          );
          platformEndpointRegistry.applySigned(
            await authClient.platformEndpointConfig(),
            keys,
          );
        } catch {
          // A missing backend override keeps the built-in HTTPS endpoint.
        }
        openNgPlatformWindow();
      });
      app.on("activate", () => openMainWindow());
    });
  }
}
