import type { Session, WebContents } from "electron";


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

export function navigationGuard(platformOrigin: string) {
  return (event: { preventDefault(): void }, target: string): void => {
    if (!sameOriginNavigation(target, platformOrigin)) event.preventDefault();
  };
}

export function installCollectorWindowPolicy(
  session: Session,
  webContents: WebContents,
  platformOrigin: string,
): void {
  session.setPermissionRequestHandler(denyPermissionRequest);
  webContents.setWindowOpenHandler(denyWindowOpen);
  webContents.on("will-navigate", navigationGuard(platformOrigin));
  webContents.on("will-redirect", navigationGuard(platformOrigin));
}
