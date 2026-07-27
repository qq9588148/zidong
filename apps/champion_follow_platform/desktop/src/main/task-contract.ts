import { createHash, createPublicKey, type KeyObject, verify } from "node:crypto";
import { readFileSync } from "node:fs";
import Ajv2020, { type ValidateFunction } from "ajv/dist/2020";
import addFormats from "ajv-formats";

import { contractPath } from "./paths";

export type Direction =
  | "BIG"
  | "SMALL"
  | "ODD"
  | "EVEN"
  | "PRIME"
  | "COMPOSITE";

export type BetPayload = {
  signal_id: string;
  signal_version: number;
  actor_ref: string;
  ball: 1 | 2 | 3 | 4 | 5;
  direction: Direction;
  threshold_version: number;
  odds_micros: 1_960_000;
  user_level: "CANDIDATE" | "FORMAL" | "CORE";
  sample_count: number;
  conservative_win_rate: string;
  conservative_unit_return: string;
  followable_rate: string;
};

export type CancelPayload = {
  reason:
    | "champion_withdrew"
    | "profile_downgraded"
    | "threshold_changed"
    | "collector_stale"
    | "data_gap"
    | "device_reassigned"
    | "account_disabled"
    | "device_unbound"
    | "global_stop";
};

type TaskBase = {
  task_id: string;
  device_id: string;
  period_id: string;
  revision: number;
  issued_at: string;
  expires_at: string;
  signing_key_version: string;
  signature: string;
};

export type DeviceTaskEnvelope = TaskBase & (
  | { action: "BET"; payload: BetPayload }
  | { action: "CANCEL"; payload: CancelPayload }
);

export type TaskSigningKeysResponse = {
  keys: Array<{
    version: string;
    public_key_spki_der_b64: string;
    sha256: string;
  }>;
};

export type TaskAcceptance =
  | "accepted"
  | "duplicate"
  | "invalid_schema"
  | "wrong_device"
  | "expired"
  | "unknown_signing_key"
  | "bad_signature"
  | "stale"
  | "revision_conflict";

let taskValidator: ValidateFunction | undefined;

function validator(): ValidateFunction {
  if (taskValidator) return taskValidator;
  const schemaPath = contractPath("device-task-v1.schema.json", process.env.VITEST === "true"
    ? {
        packaged: false,
        appPath: process.cwd(),
        resourcesPath: process.cwd(),
      }
    : undefined);
  const schema = JSON.parse(readFileSync(schemaPath, "utf8")) as object;
  const ajv = new Ajv2020({ strict: true, allErrors: true });
  addFormats(ajv);
  taskValidator = ajv.compile(schema);
  return taskValidator;
}

export function parseDeviceTask(value: unknown): DeviceTaskEnvelope | null {
  return validator()(value) ? value as DeviceTaskEnvelope : null;
}

export function canonicalTaskBytes(value: unknown): Buffer {
  return Buffer.from(canonicalJson(value), "utf8");
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value) || Object.is(value, -0)) {
      throw new Error("canonical_json_invalid_number");
    }
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
  if (typeof value === "object") {
    const object = value as Record<string, unknown>;
    return `{${Object.keys(object)
      .sort()
      .map((key) => {
        const item = object[key];
        if (item === undefined) throw new Error("canonical_json_undefined");
        return `${JSON.stringify(key)}:${canonicalJson(item)}`;
      })
      .join(",")}}`;
  }
  throw new Error("canonical_json_unsupported");
}

export class TrustedTaskSigningKeys {
  private constructor(private readonly keys: ReadonlyMap<string, KeyObject>) {}

  static fromResponse(value: unknown): TrustedTaskSigningKeys {
    if (!isPlainObject(value) || !hasOnlyKeys(value, ["keys"]) ||
        !Array.isArray(value.keys) || value.keys.length === 0) {
      throw new Error("task_signing_keys_invalid");
    }
    const keys = new Map<string, KeyObject>();
    for (const candidate of value.keys) {
      if (!isPlainObject(candidate) ||
          !hasOnlyKeys(candidate, ["version", "public_key_spki_der_b64", "sha256"]) ||
          typeof candidate.version !== "string" ||
          !/^[a-z0-9-]{1,32}$/.test(candidate.version) ||
          typeof candidate.public_key_spki_der_b64 !== "string" ||
          typeof candidate.sha256 !== "string" ||
          !/^[a-f0-9]{64}$/.test(candidate.sha256) ||
          keys.has(candidate.version)) {
        throw new Error("task_signing_keys_invalid");
      }
      const der = Buffer.from(candidate.public_key_spki_der_b64, "base64");
      if (der.toString("base64") !== candidate.public_key_spki_der_b64) {
        throw new Error("task_signing_key_base64_invalid");
      }
      const digest = createHash("sha256").update(der).digest("hex");
      if (digest !== candidate.sha256) {
        throw new Error("task_signing_key_digest_mismatch");
      }
      let publicKey: KeyObject;
      try {
        publicKey = createPublicKey({ key: der, format: "der", type: "spki" });
      } catch {
        throw new Error("task_signing_key_invalid");
      }
      if (publicKey.asymmetricKeyType !== "ed25519" ||
          !publicKey.export({ format: "der", type: "spki" }).equals(der)) {
        throw new Error("task_signing_key_invalid");
      }
      keys.set(candidate.version, publicKey);
    }
    return new TrustedTaskSigningKeys(keys);
  }

  has(version: string): boolean {
    return this.keys.has(version);
  }

  verify(task: DeviceTaskEnvelope): boolean {
    const key = this.keys.get(task.signing_key_version);
    if (!key) return false;
    const signature = decodeTaskSignature(task.signature);
    if (!signature) return false;
    const { signature: _signature, ...unsigned } = task;
    return verify(null, canonicalTaskBytes(unsigned), key, signature);
  }
}

export class HighestRevisionTasks {
  private readonly tasks = new Map<string, {
    task: DeviceTaskEnvelope;
    canonical: string;
  }>();

  constructor(
    private readonly deviceId: string,
    private readonly signingKeys: TrustedTaskSigningKeys,
    private readonly now: () => number = Date.now,
  ) {}

  accept(value: unknown): TaskAcceptance {
    const task = parseDeviceTask(value);
    if (!task) return "invalid_schema";
    if (task.device_id !== this.deviceId) return "wrong_device";
    const expiresAt = Date.parse(task.expires_at);
    if (!Number.isFinite(expiresAt) || expiresAt <= this.now()) return "expired";
    if (!this.signingKeys.has(task.signing_key_version)) {
      return "unknown_signing_key";
    }
    if (!this.signingKeys.verify(task)) return "bad_signature";

    const canonical = canonicalTaskBytes(task).toString("utf8");
    const current = this.tasks.get(task.period_id);
    if (current) {
      if (task.revision < current.task.revision) return "stale";
      if (task.revision === current.task.revision) {
        return canonical === current.canonical ? "duplicate" : "revision_conflict";
      }
    }
    this.tasks.set(task.period_id, { task, canonical });
    return "accepted";
  }

  current(periodId: string): DeviceTaskEnvelope | null {
    return this.tasks.get(periodId)?.task ?? null;
  }
}

function decodeTaskSignature(value: string): Buffer | null {
  if (!/^[A-Za-z0-9_-]{86}==$/.test(value)) return null;
  const signature = Buffer.from(
    value.replaceAll("-", "+").replaceAll("_", "/"),
    "base64",
  );
  return signature.length === 64 ? signature : null;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, keys: string[]): boolean {
  const actual = Object.keys(value).sort();
  return actual.length === keys.length &&
    actual.every((key, index) => key === [...keys].sort()[index]);
}
