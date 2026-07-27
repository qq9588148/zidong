import { describe, expect, it, vi } from "vitest";
import type { Session } from "electron";

import {
  clearPlatformSession,
  configurePlatformSession,
  isAllowedPlatformNavigation,
  platformPartition,
  platformUserAgent,
  platformWebPreferences,
} from "../../src/main/platform-session";

describe("platform session", () => {
  it("uses a dedicated persistent partition with no Node capability", () => {
    expect(platformPartition("device-A"))
      .toBe("persist:champion-platform-device-A");
    expect(platformWebPreferences("device-A")).toMatchObject({
      partition: "persist:champion-platform-device-A",
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    });
  });

  it("uses the bundled Chromium version without exposing Electron", () => {
    const userAgent = platformUserAgent("142.0.7444.175");
    expect(userAgent).toContain("Chrome/142.0.7444.175");
    expect(userAgent).not.toContain("Electron");
  });

  it("allows only the configured HTTPS origin for top-level navigation", () => {
    expect(isAllowedPlatformNavigation(
      "https://platform.invalid/game",
      "https://platform.invalid",
    )).toBe(true);
    expect(isAllowedPlatformNavigation(
      "https://other.invalid/game",
      "https://platform.invalid",
    )).toBe(false);
    expect(isAllowedPlatformNavigation(
      "http://platform.invalid/game",
      "https://platform.invalid",
    )).toBe(false);
  });

  it("forces the isolated Chromium session to connect directly", async () => {
    const platformSession = {
      setUserAgent: vi.fn(),
      setPermissionRequestHandler: vi.fn(),
      setPermissionCheckHandler: vi.fn(),
      on: vi.fn(),
      setProxy: vi.fn(async () => undefined),
    } as unknown as Session;

    await configurePlatformSession(platformSession, "142.0.7444.175");
    await configurePlatformSession(platformSession, "142.0.7444.175");

    expect(platformSession.setProxy).toHaveBeenCalledOnce();
    expect(platformSession.setProxy).toHaveBeenCalledWith({ mode: "direct" });
  });

  it("clears cookies, auth cache and cache when the platform logs out", async () => {
    const platformSession = {
      clearStorageData: vi.fn(async () => undefined),
      clearAuthCache: vi.fn(async () => undefined),
      clearCache: vi.fn(async () => undefined),
    };
    await clearPlatformSession(platformSession);
    expect(platformSession.clearStorageData).toHaveBeenCalledOnce();
    expect(platformSession.clearAuthCache).toHaveBeenCalledOnce();
    expect(platformSession.clearCache).toHaveBeenCalledOnce();
  });
});
