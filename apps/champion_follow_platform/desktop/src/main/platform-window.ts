import { join } from "node:path";

import { ChromeBrowserController, findChromeExecutable } from "./chrome-controller";
import { PlatformEndpointRegistry } from "./platform-endpoint-config";
import { desktopPaths } from "./paths";
import {
  parsePlatformPageProbe,
  platformPageProbeScript,
  type PlatformPageProbe,
} from "./platform-page-probe";

export const platformEndpointRegistry = new PlatformEndpointRegistry();

let chromeController: ChromeBrowserController | null = null;
let chromeControllerPromise: Promise<ChromeBrowserController> | null = null;
let latestPlatformProbe: PlatformPageProbe | null = null;
let platformProbeTimer: ReturnType<typeof setInterval> | null = null;
let platformProbeInFlight = false;

export type PlatformSessionPersistenceState = Readonly<{
  encryptionAvailable: boolean | null;
  snapshotLoaded: boolean;
  snapshotPresent: boolean;
  pageOriginAllowed: boolean | null;
  captureStatus: "IDLE" | "SAVED" | "UNCHANGED" | "SKIPPED" | "FAILED";
  restoreStatus: "IDLE" | "RESTORED" | "NOT_FOUND" | "SKIPPED" | "FAILED";
  errorCode: string | null;
}>;

let latestPlatformSessionState: PlatformSessionPersistenceState = {
  encryptionAvailable: null,
  snapshotLoaded: false,
  snapshotPresent: false,
  pageOriginAllowed: null,
  captureStatus: "IDLE",
  restoreStatus: "IDLE",
  errorCode: null,
};

export function chromeProfileDirectory(profileRoot = desktopPaths().profile): string {
  return join(profileRoot, "chrome-client-profile");
}

export function isPlatformWindowOpen(): boolean {
  return chromeController?.isReady() === true;
}

export function getPlatformPageController(): ChromeBrowserController | null {
  return isPlatformWindowOpen() ? chromeController : null;
}

export function getLatestPlatformPageProbe(): PlatformPageProbe | null {
  return latestPlatformProbe;
}

export function getPlatformSessionPersistenceState(): PlatformSessionPersistenceState {
  return { ...latestPlatformSessionState };
}

export function normalizePlatformAddress(value: string): string {
  const trimmed = value.trim();
  const candidate = /^[A-Za-z][A-Za-z0-9+.-]*:\/\//.test(trimmed)
    ? trimmed
    : `https://${trimmed}`;
  try {
    const url = new URL(candidate);
    if (url.protocol !== "https:" || !url.hostname || url.username || url.password) {
      throw new Error();
    }
    return url.toString();
  } catch {
    throw new Error("platform_address_invalid");
  }
}

export function resolvePlatformProxyUrl(value?: string): string | undefined {
  const explicit = value?.trim();
  return explicit || undefined;
}

export async function openNgPlatformAddress(value: string): Promise<void> {
  const url = normalizePlatformAddress(value);
  const controller = await ensureChromeController();
  try {
    await controller.open(url);
    latestPlatformSessionState = {
      encryptionAvailable: true,
      snapshotLoaded: true,
      snapshotPresent: true,
      pageOriginAllowed: true,
      captureStatus: "UNCHANGED",
      restoreStatus: "RESTORED",
      errorCode: null,
    };
    startPlatformPageProbe(controller);
  } catch {
    latestPlatformProbe = null;
    latestPlatformSessionState = {
      ...latestPlatformSessionState,
      pageOriginAllowed: null,
      captureStatus: "FAILED",
      errorCode: "CHROME_NAVIGATION_FAILED",
    };
    throw new Error("platform_window_unavailable");
  }
}

export async function openNgPlatformWindow(): Promise<void> {
  const controller = await ensureChromeController();
  if (controller.isReady()) {
    await controller.bringToFront();
    return;
  }
  await openNgPlatformAddress(platformEndpointRegistry.current().entryUrl);
}

export async function reopenNgPlatformWindow(): Promise<void> {
  await openNgPlatformAddress(platformEndpointRegistry.current().entryUrl);
}

export function allowPlatformWindowCloseForExit(): void {
  stopPlatformPageProbe();
  void chromeController?.close();
  chromeController = null;
  chromeControllerPromise = null;
  latestPlatformProbe = null;
}

async function ensureChromeController(): Promise<ChromeBrowserController> {
  if (chromeController) return chromeController;
  if (chromeControllerPromise) return await chromeControllerPromise;
  const creating = createChromeController();
  chromeControllerPromise = creating;
  try {
    chromeController = await creating;
    return chromeController;
  } finally {
    if (chromeControllerPromise === creating) chromeControllerPromise = null;
  }
}

async function createChromeController(): Promise<ChromeBrowserController> {
  const localAppData = process.env.LOCALAPPDATA ?? "";
  const programFiles = process.env.ProgramFiles ?? "C:\\Program Files";
  const programFilesX86 = process.env["ProgramFiles(x86)"] ??
    "C:\\Program Files (x86)";
  const bundled = process.resourcesPath
    ? join(process.resourcesPath, "chrome", "chrome.exe")
    : "";
  const executable = await findChromeExecutable([
    process.env.CHAMPION_CHROME_PATH ?? "",
    join(localAppData, "Google", "Chrome", "Application", "chrome.exe"),
    join(localAppData, "Google", "Chrome", "Bin", "chrome.exe"),
    join(programFiles, "Google", "Chrome", "Application", "chrome.exe"),
    join(programFilesX86, "Google", "Chrome", "Application", "chrome.exe"),
    bundled,
  ].filter(Boolean));
  const proxyUrl = resolvePlatformProxyUrl(
    process.env.CHAMPION_PLATFORM_PROXY_URL,
  );
  return new ChromeBrowserController({
    executable,
    profileDirectory: chromeProfileDirectory(),
    initialUrl: platformEndpointRegistry.current().entryUrl,
    ...(proxyUrl ? { proxyUrl } : {}),
  });
}

function startPlatformPageProbe(controller: ChromeBrowserController): void {
  stopPlatformPageProbe();
  const run = async () => {
    if (platformProbeInFlight || !controller.isReady()) return;
    platformProbeInFlight = true;
    try {
      latestPlatformProbe = parsePlatformPageProbe(
        await controller.evaluate<unknown>(platformPageProbeScript()),
      );
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
