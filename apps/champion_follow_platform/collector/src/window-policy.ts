import type { Session, WebContents } from "electron";

const configuredSessions = new WeakMap<Session, Promise<void>>();

export const COLLECTOR_PARTITION =
  "persist:champion-follow-main-collector-v1" as const;

export function collectorWebPreferences(preload: string) {
  return {
    preload,
    partition: COLLECTOR_PARTITION,
    nodeIntegration: false,
    contextIsolation: true,
    sandbox: true,
    webSecurity: true,
    allowRunningInsecureContent: false,
  } as const;
}

export function collectorUserAgent(chromiumVersion: string): string {
  if (!/^[0-9]+(?:\.[0-9]+){1,3}$/.test(chromiumVersion)) {
    throw new Error("collector_chromium_version_invalid");
  }
  return [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "AppleWebKit/537.36 (KHTML, like Gecko)",
    `Chrome/${chromiumVersion}`,
    "Safari/537.36",
  ].join(" ");
}

export function configureCollectorSession(
  collectorSession: Session,
  chromiumVersion: string = process.versions.chrome,
): Promise<void> {
  const existing = configuredSessions.get(collectorSession);
  if (existing) return existing;
  collectorSession.setUserAgent(
    collectorUserAgent(chromiumVersion),
    "zh-CN,zh;q=0.9,en;q=0.8",
  );
  collectorSession.setPermissionCheckHandler(() => false);
  collectorSession.on("will-download", (event) => event.preventDefault());
  const configured = collectorSession.setProxy({ mode: "direct" });
  configuredSessions.set(collectorSession, configured);
  return configured;
}

export async function loadPlatformUntilAccepted(
  load: () => Promise<void>,
  shouldContinue: () => boolean,
  waitForRetry: (retryCount: number) => Promise<void>,
): Promise<boolean> {
  let retryCount = 0;
  while (shouldContinue()) {
    try {
      await load();
      return true;
    } catch {
      if (!shouldContinue()) return false;
      retryCount += 1;
      await waitForRetry(retryCount);
    }
  }
  return false;
}

export function sameOriginNavigation(
  target: string,
  platformOrigin: string,
): boolean {
  try {
    return new URL(target).origin === platformOrigin;
  } catch {
    return false;
  }
}

export function isSecurePlatformNavigation(target: string): boolean {
  try {
    const url = new URL(target);
    return url.protocol === "https:" && !url.username && !url.password;
  } catch {
    return false;
  }
}

export function denyPermissionRequest(
  _webContents: unknown,
  _permission: string,
  callback: (allowed: boolean) => void,
): void {
  callback(false);
}

export function denyWindowOpen(_details?: unknown): { action: "deny" } {
  return { action: "deny" };
}

export function navigationGuard(_platformOrigin?: string) {
  return (event: { preventDefault(): void }, target: string): void => {
    if (!isSecurePlatformNavigation(target)) event.preventDefault();
  };
}

export function installCollectorWindowPolicy(
  session: Session,
  webContents: WebContents,
  _platformOrigin?: string,
): void {
  session.setPermissionRequestHandler(denyPermissionRequest);
  webContents.setWindowOpenHandler(denyWindowOpen);
  webContents.on("will-navigate", navigationGuard());
  webContents.on("will-redirect", navigationGuard());
}
