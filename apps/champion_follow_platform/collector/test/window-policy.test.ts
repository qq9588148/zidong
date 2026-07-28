import { describe, expect, it, vi } from "vitest";

import {
  COLLECTOR_PARTITION,
  collectorWebPreferences,
  configureCollectorSession,
  denyPermissionRequest,
  denyWindowOpen,
  installCollectorWindowPolicy,
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

  it("allows only navigation within the configured platform origin", () => {
    const origin = "https://platform.example";
    expect(sameOriginNavigation(`${origin}/game28`, origin)).toBe(true);
    expect(sameOriginNavigation("https://other.example/game28", origin)).toBe(
      false,
    );
    expect(sameOriginNavigation("not a url", origin)).toBe(false);

    const allowed = { preventDefault: vi.fn() };
    const blocked = { preventDefault: vi.fn() };
    const guard = navigationGuard(origin);
    guard(allowed, `${origin}/login`);
    guard(blocked, "https://other.example/phishing");

    expect(allowed.preventDefault).not.toHaveBeenCalled();
    expect(blocked.preventDefault).toHaveBeenCalledOnce();
  });

  it("installs the same origin guard for navigations and redirects", () => {
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

  it("forces the persistent collector session to connect directly", async () => {
    const fakeSession = {
      setUserAgent: vi.fn(),
      setPermissionCheckHandler: vi.fn(),
      on: vi.fn(),
      setProxy: vi.fn(async () => undefined),
    };

    await configureCollectorSession(fakeSession as never, "142.0.7444.175");

    expect(fakeSession.setProxy).toHaveBeenCalledWith({ mode: "direct" });
    expect(fakeSession.setUserAgent).toHaveBeenCalledWith(
      expect.stringContaining("Chrome/142.0.7444.175"),
      "zh-CN,zh;q=0.9,en;q=0.8",
    );
  });

  it("keeps retrying a transient initial page failure without exiting", async () => {
    let attempts = 0;
    let waits = 0;
    const loaded = await loadPlatformUntilAccepted(
      async () => {
        attempts += 1;
        if (attempts < 3) throw new Error("ERR_CONNECTION_RESET");
      },
      () => true,
      async () => {
        waits += 1;
      },
    );

    expect(loaded).toBe(true);
    expect(attempts).toBe(3);
    expect(waits).toBe(2);
  });
});
