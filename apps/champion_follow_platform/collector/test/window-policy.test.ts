import { describe, expect, it, vi } from "vitest";

import {
  COLLECTOR_PARTITION,
  collectorWebPreferences,
  denyPermissionRequest,
  denyWindowOpen,
  installCollectorWindowPolicy,
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
});
