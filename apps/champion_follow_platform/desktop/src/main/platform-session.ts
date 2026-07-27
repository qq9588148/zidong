import {
  session,
  type Session,
  type WebContents,
  type WebPreferences,
} from "electron";

export function platformPartition(deviceId: string): string {
  if (!/^[A-Za-z0-9._-]{1,128}$/.test(deviceId)) {
    throw new Error("platform_device_identifier_invalid");
  }
  return `persist:champion-platform-${deviceId}`;
}

export function platformWebPreferences(deviceId: string): WebPreferences {
  return {
    partition: platformPartition(deviceId),
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    webSecurity: true,
    allowRunningInsecureContent: false,
    spellcheck: false,
  };
}

export function getPlatformSession(deviceId: string): Session {
  const platformSession = session.fromPartition(platformPartition(deviceId));
  configurePlatformSession(platformSession);
  return platformSession;
}

export function configurePlatformSession(platformSession: Session): void {
  platformSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });
  platformSession.setPermissionCheckHandler(() => false);
  platformSession.on("will-download", (event) => event.preventDefault());
}

export function applyPlatformNavigationPolicy(
  webContents: WebContents,
  allowedOrigin: string,
): void {
  validateAllowedOrigin(allowedOrigin);
  webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  const guard = (event: Electron.Event, target: string) => {
    if (!isAllowedPlatformNavigation(target, allowedOrigin)) event.preventDefault();
  };
  webContents.on("will-navigate", guard);
  webContents.on("will-redirect", guard);
}

export function isAllowedPlatformNavigation(
  target: string,
  allowedOrigin: string,
): boolean {
  try {
    validateAllowedOrigin(allowedOrigin);
    const url = new URL(target);
    return url.protocol === "https:" && url.origin === allowedOrigin;
  } catch {
    return false;
  }
}

export async function clearPlatformSession(platformSession: Pick<
  Session,
  "clearStorageData" | "clearAuthCache" | "clearCache"
>): Promise<void> {
  await platformSession.clearStorageData();
  await platformSession.clearAuthCache();
  await platformSession.clearCache();
}

function validateAllowedOrigin(origin: string): void {
  const url = new URL(origin);
  if (url.protocol !== "https:" || url.origin !== origin ||
      url.username || url.password) {
    throw new Error("platform_origin_invalid");
  }
}
