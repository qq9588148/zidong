import {
  session,
  type Session,
  type WebContents,
  type WebPreferences,
} from "electron";

const configuredSessions = new WeakMap<Session, Promise<void>>();

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

export async function getPlatformSession(deviceId: string): Promise<Session> {
  const platformSession = session.fromPartition(platformPartition(deviceId));
  await configurePlatformSession(platformSession);
  return platformSession;
}

export async function configurePlatformSession(
  platformSession: Session,
  chromiumVersion: string = process.versions.chrome,
  proxyUrl?: string,
): Promise<void> {
  const existing = configuredSessions.get(platformSession);
  if (existing) return await existing;
  platformSession.setUserAgent(
    platformUserAgent(chromiumVersion),
    "zh-CN,zh;q=0.9,en;q=0.8",
  );
  platformSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });
  platformSession.setPermissionCheckHandler(() => false);
  platformSession.on("will-download", (event) => event.preventDefault());
  const configured = platformSession.setProxy(
    platformProxyConfiguration(proxyUrl),
  );
  configuredSessions.set(platformSession, configured);
  await configured;
}

function platformProxyConfiguration(
  value: string | undefined,
): { mode: "system" } | { mode: "fixed_servers"; proxyRules: string } {
  if (value === undefined || value.trim() === "") return { mode: "system" };
  try {
    const url = new URL(value);
    const loopback = url.hostname === "127.0.0.1" ||
      url.hostname === "localhost" || url.hostname === "[::1]";
    const port = Number(url.port);
    if (url.protocol !== "http:" || !loopback || url.username || url.password ||
        url.pathname !== "/" || url.search || url.hash ||
        !Number.isInteger(port) || port < 1 || port > 65_535) {
      throw new Error();
    }
    const endpoint = `${url.hostname}:${port}`;
    return {
      mode: "fixed_servers",
      proxyRules: `http=${endpoint};https=${endpoint}`,
    };
  } catch {
    throw new Error("platform_proxy_invalid");
  }
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

export function applyManualPlatformNavigationPolicy(
  webContents: WebContents,
): void {
  webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  const guard = (event: Electron.Event, target: string) => {
    if (!isSecureManualPlatformNavigation(target)) event.preventDefault();
  };
  webContents.on("will-navigate", guard);
  webContents.on("will-redirect", guard);
}

export function isSecureManualPlatformNavigation(target: string): boolean {
  try {
    const url = new URL(target);
    return url.protocol === "https:" && !url.username && !url.password;
  } catch {
    return false;
  }
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
