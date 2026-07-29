import { describe, expect, it } from "vitest";

import {
  chromeProfileDirectory,
  normalizePlatformAddress,
  platformEndpointRegistry,
} from "../../src/main/platform-window";
import { isAllowedPlatformNavigation } from "../../src/main/platform-session";

describe("NG platform window", () => {
  it("normalizes only credential-free HTTPS addresses entered by the customer", () => {
    expect(normalizePlatformAddress("ng888.com")).toBe("https://ng888.com/");
    expect(normalizePlatformAddress(" https://random.example/home "))
      .toBe("https://random.example/home");
    expect(() => normalizePlatformAddress("http://ng888.com"))
      .toThrow("platform_address_invalid");
    expect(() => normalizePlatformAddress("https://user:pass@ng888.com"))
      .toThrow("platform_address_invalid");
  });

  it("uses the selected ng888 entry and preserves strict endpoint metadata", () => {
    const endpoint = platformEndpointRegistry.current();
    expect(endpoint.entryUrl).toBe("https://ng888.com/");
    expect(endpoint.allowedOrigins).toEqual(["https://ng888.com"]);
    expect(isAllowedPlatformNavigation(
      "https://ng888.com/home",
      endpoint.allowedOrigins,
    )).toBe(true);
    expect(isAllowedPlatformNavigation(
      "https://teqs3ws.szjiemeng.com/ng/h5_static/js/index.js",
      endpoint.allowedOrigins,
    )).toBe(false);
  });

  it("uses a dedicated Chrome profile instead of the customer's normal profile", () => {
    expect(chromeProfileDirectory("C:/client-data"))
      .toMatch(/client-data[\\/]chrome-client-profile$/);
  });
});
