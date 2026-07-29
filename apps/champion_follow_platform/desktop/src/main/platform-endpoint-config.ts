import { TrustedTaskSigningKeys } from "./task-contract";

export type PlatformEndpointConfig = Readonly<{
  configVersion: number;
  entryUrl: string;
  allowedOrigins: readonly string[];
}>;

type SignedPlatformEndpointConfig = {
  config_version: number;
  issued_at: string;
  expires_at: string;
  entry_url: string;
  allowed_origins: string[];
  signing_key_version: string;
  signature: string;
};

export type PlatformEndpointUpdateResult =
  | "accepted"
  | "invalid_config"
  | "expired"
  | "unknown_signing_key"
  | "bad_signature"
  | "stale";

export const DEFAULT_PLATFORM_ENDPOINT_CONFIG: PlatformEndpointConfig =
  freezeConfig({
    configVersion: 1,
    entryUrl: "https://ng888.com/",
    allowedOrigins: ["https://ng888.com"],
  });

export class PlatformEndpointRegistry {
  private config = DEFAULT_PLATFORM_ENDPOINT_CONFIG;

  current(): PlatformEndpointConfig {
    return this.config;
  }

  applySigned(
    value: unknown,
    signingKeys: TrustedTaskSigningKeys,
    now: () => number = Date.now,
  ): PlatformEndpointUpdateResult {
    const envelope = parseSignedConfig(value);
    if (!envelope) return "invalid_config";

    const expiresAt = Date.parse(envelope.expires_at);
    if (expiresAt <= now()) return "expired";
    if (!signingKeys.has(envelope.signing_key_version)) {
      return "unknown_signing_key";
    }

    const { signature, ...unsigned } = envelope;
    if (!signingKeys.verifyDetached(
      envelope.signing_key_version,
      unsigned,
      signature,
    )) {
      return "bad_signature";
    }
    if (envelope.config_version <= this.config.configVersion) return "stale";

    this.config = freezeConfig({
      configVersion: envelope.config_version,
      entryUrl: envelope.entry_url,
      allowedOrigins: envelope.allowed_origins,
    });
    return "accepted";
  }
}

function parseSignedConfig(value: unknown): SignedPlatformEndpointConfig | null {
  if (!isPlainObject(value) || !hasOnlyKeys(value, [
    "allowed_origins",
    "config_version",
    "entry_url",
    "expires_at",
    "issued_at",
    "signature",
    "signing_key_version",
  ])) return null;

  if (!Number.isSafeInteger(value.config_version) ||
      (value.config_version as number) < 1 ||
      typeof value.issued_at !== "string" ||
      typeof value.expires_at !== "string" ||
      !isCanonicalTimestamp(value.issued_at) ||
      !isCanonicalTimestamp(value.expires_at) ||
      Date.parse(value.expires_at) <= Date.parse(value.issued_at) ||
      typeof value.entry_url !== "string" ||
      !Array.isArray(value.allowed_origins) ||
      typeof value.signing_key_version !== "string" ||
      !/^[a-z0-9-]{1,32}$/.test(value.signing_key_version) ||
      typeof value.signature !== "string") {
    return null;
  }

  const origins = value.allowed_origins;
  if (origins.length < 1 || origins.length > 8 ||
      !origins.every((origin): origin is string =>
        typeof origin === "string" && isAllowedOrigin(origin)) ||
      new Set(origins).size !== origins.length ||
      !isEntryUrl(value.entry_url, origins)) {
    return null;
  }

  return value as SignedPlatformEndpointConfig;
}

function isEntryUrl(value: string, allowedOrigins: readonly string[]): boolean {
  if (value.length > 2_048) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      url.href === value &&
      allowedOrigins.includes(url.origin);
  } catch {
    return false;
  }
}

function isAllowedOrigin(value: string): boolean {
  if (value.length > 256) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" &&
      !url.username &&
      !url.password &&
      url.origin === value;
  } catch {
    return false;
  }
}

function isCanonicalTimestamp(value: string): boolean {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) && new Date(timestamp).toISOString() === value;
}

function freezeConfig(value: {
  configVersion: number;
  entryUrl: string;
  allowedOrigins: readonly string[];
}): PlatformEndpointConfig {
  return Object.freeze({
    ...value,
    allowedOrigins: Object.freeze([...value.allowedOrigins]),
  });
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, keys: string[]): boolean {
  const expected = [...keys].sort();
  const actual = Object.keys(value).sort();
  return actual.length === expected.length &&
    actual.every((key, index) => key === expected[index]);
}
