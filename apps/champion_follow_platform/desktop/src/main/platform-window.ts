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

export const platformEndpointRegistry = new PlatformEndpointRegistry();

let platformWindow: BrowserWindow | null = null;

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

export function openNgPlatformWindow(): BrowserWindow {
  if (isPlatformWindowOpen()) {
    platformWindow!.show();
    platformWindow!.focus();
    return platformWindow!;
  }

  const window = new BrowserWindow(platformWindowOptions());
  platformWindow = window;
  const endpoint = platformEndpointRegistry.current();
  configurePlatformSession(window.webContents.session);
  applyPlatformNavigationPolicy(window.webContents, endpoint.allowedOrigins);
  window.once("ready-to-show", () => window.show());
  window.on("closed", () => {
    if (platformWindow === window) platformWindow = null;
  });
  void window.loadURL(endpoint.entryUrl);
  return window;
}
