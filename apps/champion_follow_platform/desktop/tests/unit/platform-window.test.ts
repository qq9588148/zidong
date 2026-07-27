import { describe, expect, it } from "vitest";

import {
  NG_ALLOWED_ORIGINS,
  NG_ENTRY_URL,
  NG_LOGIN_URL,
  platformWindowOptions,
} from "../../src/main/platform-window";
import { isAllowedPlatformNavigation } from "../../src/main/platform-session";

describe("NG platform window", () => {
  it("pins the inspected entry and lobby origins", () => {
    expect(NG_ENTRY_URL).toBe("https://ng888.com/");
    expect(NG_LOGIN_URL).toBe("https://jtyo.ngk14.com/login");
    expect(NG_ALLOWED_ORIGINS).toEqual([
      "https://ng888.com",
      "https://jtyo.ngk14.com",
    ]);
    expect(isAllowedPlatformNavigation(
      "https://jtyo.ngk14.com/login",
      NG_ALLOWED_ORIGINS,
    )).toBe(true);
    expect(isAllowedPlatformNavigation(
      "https://teqs3ws.szjiemeng.com/ng/h5_static/js/index.js",
      NG_ALLOWED_ORIGINS,
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
