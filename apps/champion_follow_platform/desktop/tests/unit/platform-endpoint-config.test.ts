import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  DEFAULT_PLATFORM_ENDPOINT_CONFIG,
  PlatformEndpointRegistry,
} from "../../src/main/platform-endpoint-config";
import { TrustedTaskSigningKeys } from "../../src/main/task-contract";
import {
  signCanonicalValue,
  signingKeysResponse,
} from "../helpers/signed-task";

const NOW = Date.parse("2026-07-28T06:00:00.000Z");

function signedConfig(overrides: Record<string, unknown> = {}): unknown {
  const unsigned = {
    config_version: 2,
    issued_at: "2026-07-28T05:59:00.000Z",
    expires_at: "2026-07-29T06:00:00.000Z",
    entry_url: "https://next.example.com/app",
    allowed_origins: ["https://next.example.com"],
    signing_key_version: "test-v1",
    ...overrides,
  };
  return { ...unsigned, signature: signCanonicalValue(unsigned) };
}

describe("platform endpoint configuration", () => {
  const signingKeys = TrustedTaskSigningKeys.fromResponse(signingKeysResponse);

  it("defaults to the user-selected ng888 HTTPS entry", () => {
    expect(DEFAULT_PLATFORM_ENDPOINT_CONFIG).toEqual({
      configVersion: 1,
      entryUrl: "https://ng888.com/",
      allowedOrigins: ["https://ng888.com"],
    });
  });

  it("accepts a higher signed version from the future authenticated backend", () => {
    const registry = new PlatformEndpointRegistry();

    expect(registry.applySigned(signedConfig(), signingKeys, () => NOW))
      .toBe("accepted");
    expect(registry.current()).toEqual({
      configVersion: 2,
      entryUrl: "https://next.example.com/app",
      allowedOrigins: ["https://next.example.com"],
    });
  });

  it("rejects stale, unsigned, and incorrectly signed updates", () => {
    const registry = new PlatformEndpointRegistry();
    expect(registry.applySigned(signedConfig(), signingKeys, () => NOW))
      .toBe("accepted");
    expect(registry.applySigned(signedConfig(), signingKeys, () => NOW))
      .toBe("stale");

    const unsigned = signedConfig() as Record<string, unknown>;
    delete unsigned.signature;
    expect(new PlatformEndpointRegistry().applySigned(unsigned, signingKeys, () => NOW))
      .toBe("invalid_config");

    const tampered = signedConfig() as Record<string, unknown>;
    tampered.entry_url = "https://next.example.com/tampered";
    expect(new PlatformEndpointRegistry().applySigned(tampered, signingKeys, () => NOW))
      .toBe("bad_signature");
  });

  it.each([
    { entry_url: "http://next.example.com/app" },
    { entry_url: "https://user:pass@next.example.com/app" },
    { entry_url: "https://next.example.com/app?tenant=1" },
    { entry_url: "https://next.example.com/app#login" },
    { allowed_origins: ["https://other.example.com"] },
    { allowed_origins: ["https://next.example.com", "https://next.example.com"] },
  ])("rejects an unsafe endpoint: %j", (override) => {
    const registry = new PlatformEndpointRegistry();
    expect(registry.applySigned(signedConfig(override), signingKeys, () => NOW))
      .toBe("invalid_config");
    expect(registry.current()).toEqual(DEFAULT_PLATFORM_ENDPOINT_CONFIG);
  });

  it("does not expose any endpoint mutation channel to renderer or platform pages", () => {
    const preload = readFileSync(join(process.cwd(), "src/preload/index.ts"), "utf8");
    expect(preload).not.toMatch(/setPlatformEndpoint|set-platform-endpoint/i);
  });
});
