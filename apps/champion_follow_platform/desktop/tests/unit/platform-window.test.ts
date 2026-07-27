import { describe, expect, it } from "vitest";

import {
  platformEndpointRegistry,
  platformWindowOptions,
} from "../../src/main/platform-window";
import { isAllowedPlatformNavigation } from "../../src/main/platform-session";

describe("NG platform window", () => {
  it("uses the inspected ng1z entry and same-origin navigation policy", () => {
    const endpoint = platformEndpointRegistry.current();
    expect(endpoint.entryUrl).toBe("https://ng1z.com/");
    expect(endpoint.allowedOrigins).toEqual(["https://ng1z.com"]);
    expect(isAllowedPlatformNavigation(
      "https://ng1z.com/home",
      endpoint.allowedOrigins,
    )).toBe(true);
    expect(isAllowedPlatformNavigation(
      "https://teqs3ws.szjiemeng.com/ng/h5_static/js/index.js",
      endpoint.allowedOrigins,
    )).toBe(false);
  });

  it("creates a narrow secure built-in Chromium window", () => {
    expect(platformWindowOptions()).toMatchObject({
      width: 460,
      height: 820,
      show: false,
      webPreferences: {
        partition: "persist:champion-platform-local-desktop",
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        webSecurity: true,
      },
    });
  });
});
