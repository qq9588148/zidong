import { describe, expect, it, vi } from "vitest";

import {
  COLLECTOR_PARTITION,
  collectorWebPreferences,
  configureCollectorSession,
  cookiePersistenceDetails,
  denyPermissionRequest,
  denyWindowOpen,
  installCollectorWindowPolicy,
  isSecurePlatformNavigation,
  loadPlatformUntilAccepted,
  navigationGuard,
  sameOriginNavigation,
} from "../src/window-policy.js";

describe("collector Electron window policy", () => {
  it("uses the fixed isolated partition and immutable secure preferences", () => {
    expect(COLLECTOR_PARTITION).toBe(
      "persist:champion-follow-main-collector-v1",
    );
    expect(collectorWebPreferences("/runtime/preload.cjs")).toEqual({
      preload: "/runtime/preload.cjs",
      partition: "persist:champion-follow-main-collector-v1",
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
    });
  });

  it("denies every permission request and popup", () => {
    const callback = vi.fn();

    denyPermissionRequest({}, "camera", callback);

    expect(callback).toHaveBeenCalledOnce();
    expect(callback).toHaveBeenCalledWith(false);
    expect(denyWindowOpen()).toEqual({ action: "deny" });
  });

  it("keeps same-origin checks available for strict call sites", () => {
    const origin = "https://platform.example";
    expect(sameOriginNavigation(`${origin}/game28`, origin)).toBe(true);
    expect(sameOriginNavigation("https://other.example/game28", origin)).toBe(
      false,
    );
    expect(sameOriginNavigation("not a url", origin)).toBe(false);

  });

  it("allows random HTTPS redirects but blocks unsafe schemes", () => {
    const allowed = { preventDefault: vi.fn() };
    const blocked = { preventDefault: vi.fn() };
    const guard = navigationGuard();
    guard(allowed, "https://random-entry.example/login");
    guard(blocked, "http://ng888.com/login");

    expect(allowed.preventDefault).not.toHaveBeenCalled();
    expect(blocked.preventDefault).toHaveBeenCalledOnce();
  });

  it("installs the secure navigation guard for navigations and redirects", () => {
    const events: string[] = [];
    const fakeSession = {
      setPermissionRequestHandler: vi.fn(),
    };
    const fakeWebContents = {
      setWindowOpenHandler: vi.fn(),
      on(name: string) {
        events.push(name);
        return this;
      },
    };

    installCollectorWindowPolicy(
      fakeSession as never,
      fakeWebContents as never,
      "https://platform.example",
    );

    expect(events).toEqual(["will-navigate", "will-redirect"]);
  });

  it("allows ng888 to redirect to a random secure landing domain", () => {
    expect(isSecurePlatformNavigation("https://ng888.com/")).toBe(true);
    expect(
      isSecurePlatformNavigation("https://random-entry.example/login"),
    ).toBe(true);
    expect(isSecurePlatformNavigation("http://ng888.com/")).toBe(false);
    expect(isSecurePlatformNavigation("file:///C:/private.txt")).toBe(false);
  });

  it("uses a direct connection by default and accepts only an explicit loopback proxy", async () => {
    const fakeSession = {
      cookies: {
        on: vi.fn(),
        set: vi.fn(async () => undefined),
        flushStore: vi.fn(async () => undefined),
      },
      setUserAgent: vi.fn(),
      setPermissionCheckHandler: vi.fn(),
      on: vi.fn(),
      setProxy: vi.fn(async () => undefined),
    };

    await configureCollectorSession(
      fakeSession as never,
      "142.0.7444.175",
      undefined,
    );

    expect(fakeSession.setProxy).toHaveBeenCalledWith({ mode: "direct" });
    expect(fakeSession.setUserAgent).toHaveBeenCalledWith(
      expect.stringContaining("Chrome/142.0.7444.175"),
      "zh-CN,zh;q=0.9,en;q=0.8",
    );

    const proxiedSession = {
      cookies: {
        on: vi.fn(),
        set: vi.fn(async () => undefined),
        flushStore: vi.fn(async () => undefined),
      },
      setUserAgent: vi.fn(),
      setPermissionCheckHandler: vi.fn(),
      on: vi.fn(),
      setProxy: vi.fn(async () => undefined),
    };
    await configureCollectorSession(
      proxiedSession as never,
      "142.0.7444.175",
      "http://127.0.0.1:25378",
    );
    expect(proxiedSession.setProxy).toHaveBeenCalledWith({
      mode: "fixed_servers",
      proxyRules: "http=127.0.0.1:25378;https=127.0.0.1:25378",
    });
    expect(proxiedSession.setProxy).not.toHaveBeenCalledWith(
      expect.objectContaining({ proxyBypassRules: expect.anything() }),
    );
    await expect(configureCollectorSession(
      { ...proxiedSession, setProxy: vi.fn(async () => undefined) } as never,
      "142.0.7444.175",
      "http://proxy.example:25378",
    )).rejects.toThrow("collector_proxy_invalid");
  });

  it("converts secure session cookies into Chromium-managed persistent cookies", () => {
    expect(cookiePersistenceDetails({
      name: "session",
      value: "synthetic-value",
      domain: ".random-entry.example",
      path: "/",
      secure: true,
      httpOnly: true,
      sameSite: "no_restriction",
      session: true,
    }, 1_000)).toEqual({
      url: "https://random-entry.example/",
      name: "session",
      value: "synthetic-value",
      domain: ".random-entry.example",
      path: "/",
      secure: true,
      httpOnly: true,
      sameSite: "no_restriction",
      expirationDate: 2_593_000,
    });
    expect(cookiePersistenceDetails({
      name: "persistent",
      value: "synthetic-value",
      domain: "random-entry.example",
      sameSite: "lax",
      session: false,
    }, 1_000)).toBeNull();
  });

  it("keeps retrying a transient initial page failure without exiting", async () => {
    let attempts = 0;
    let waits = 0;
    const retryNumbers: number[] = [];
    const loaded = await loadPlatformUntilAccepted(
      async () => {
        attempts += 1;
        if (attempts < 3) throw new Error("ERR_CONNECTION_RESET");
      },
      () => true,
      async (retryNumber) => {
        waits += 1;
        retryNumbers.push(retryNumber);
      },
    );

    expect(loaded).toBe(true);
    expect(attempts).toBe(3);
    expect(waits).toBe(2);
    expect(retryNumbers).toEqual([1, 2]);
  });
});
