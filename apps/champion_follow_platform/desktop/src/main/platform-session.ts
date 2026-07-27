import {
  session,
  type Session,
  type WebContents,
  type WebPreferences,
} from "electron";

const configuredSessions = new WeakSet<Session>();

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
  if (configuredSessions.has(platformSession)) return;
  configuredSessions.add(platformSession);
  platformSession.setUserAgent(
    platformUserAgent(process.versions.chrome),
    "zh-CN,zh;q=0.9,en;q=0.8",
  );
  platformSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });
  platformSession.setPermissionCheckHandler(() => false);
  platformSession.on("will-download", (event) => event.preventDefault());
}

export function platformUserAgent(chromiumVersion: string): string {
  if (!/^[0-9]+(?:\.[0-9]+){1,3}$/.test(chromiumVersion)) {
    throw new Error("platform_chromium_version_invalid");
  }
  return [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "AppleWebKit/537.36 (KHTML, like Gecko)",
    `Chrome/${chromiumVersion}`,
    "Safari/537.36",
  ].join(" ");
}

export function applyPlatformNavigationPolicy(
  webContents: WebContents,
  allowedOrigins: string | readonly string[],
): void {
  const origins = normalizeOrigins(allowedOrigins);
  webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  const guard = (event: Electron.Event, target: string) => {
    if (!isAllowedPlatformNavigation(target, origins)) event.preventDefault();
  };
  webContents.on("will-navigate", guard);
  webContents.on("will-redirect", guard);
}

export function isAllowedPlatformNavigation(
  target: string,
  allowedOrigins: string | readonly string[],
): boolean {
  try {
    const origins = normalizeOrigins(allowedOrigins);
    const url = new URL(target);
    return url.protocol === "https:" && origins.includes(url.origin);
  } catch {
    return false;
  }
}

function normalizeOrigins(origins: string | readonly string[]): readonly string[] {
  const values = typeof origins === "string" ? [origins] : [...origins];
  if (values.length === 0 || new Set(values).size !== values.length) {
    throw new Error("platform_origin_invalid");
  }
  for (const origin of values) validateAllowedOrigin(origin);
  return values;
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
