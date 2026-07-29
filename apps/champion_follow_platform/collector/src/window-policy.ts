import type {
  Cookie,
  Cookies,
  CookiesSetDetails,
  Session,
  WebContents,
} from "electron";

const configuredSessions = new WeakMap<Session, Promise<void>>();
const configuredCookieStores = new WeakSet<Cookies>();
const COOKIE_RETENTION_SECONDS = 30 * 24 * 60 * 60;

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

export function cookiePersistenceDetails(
  cookie: Cookie,
  nowSeconds = Date.now() / 1_000,
): CookiesSetDetails | null {
  if (!cookie.session || !cookie.domain) return null;
  const hostname = cookie.domain.replace(/^\./, "");
  const path = cookie.path?.startsWith("/") ? cookie.path : "/";
  try {
    const url = new URL(`https://${hostname}${path}`);
    if (url.hostname !== hostname) return null;
    return {
      url: url.toString(),
      name: cookie.name,
      value: cookie.value,
      ...(cookie.hostOnly ? {} : { domain: cookie.domain }),
      path,
      ...(cookie.secure === undefined ? {} : { secure: cookie.secure }),
      ...(cookie.httpOnly === undefined ? {} : { httpOnly: cookie.httpOnly }),
      sameSite: cookie.sameSite,
      expirationDate: Math.floor(nowSeconds) + COOKIE_RETENTION_SECONDS,
    };
  } catch {
    return null;
  }
}

export function installSessionCookiePersistence(cookies: Cookies): void {
  if (configuredCookieStores.has(cookies)) return;
  configuredCookieStores.add(cookies);
  cookies.on("changed", (_event, cookie, _cause, removed) => {
    if (removed) return;
    const details = cookiePersistenceDetails(cookie);
    if (details === null) return;
    void cookies.set(details)
      .then(() => cookies.flushStore())
      .catch(() => undefined);
  });
}

export async function configureCollectorSession(
  collectorSession: Session,
  chromiumVersion: string = process.versions.chrome,
  proxyUrl?: string,
): Promise<void> {
  const existing = configuredSessions.get(collectorSession);
  if (existing) return await existing;
  collectorSession.setUserAgent(
    collectorUserAgent(chromiumVersion),
    "zh-CN,zh;q=0.9,en;q=0.8",
  );
  collectorSession.setPermissionCheckHandler(() => false);
  collectorSession.on("will-download", (event) => event.preventDefault());
  installSessionCookiePersistence(collectorSession.cookies);
  const selectedProxy = proxyUrl?.trim() || undefined;
  const configured = collectorSession.setProxy(
    platformProxyConfiguration(selectedProxy, "collector_proxy_invalid"),
  );
  configuredSessions.set(collectorSession, configured);
  await configured;
}

function platformProxyConfiguration(
  value: string | undefined,
  errorCode: string,
): { mode: "direct" } | { mode: "fixed_servers"; proxyRules: string } {
  if (value === undefined || value.trim() === "") return { mode: "direct" };
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
    throw new Error(errorCode);
  }
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
