import {
  createHash,
  createPrivateKey,
  createPublicKey,
  sign,
} from "node:crypto";

import {
  canonicalTaskBytes,
  type DeviceTaskEnvelope,
  type TaskSigningKeysResponse,
} from "../../src/main/task-contract";

const privateKey = createPrivateKey({
  key: Buffer.concat([
    Buffer.from("302e020100300506032b657004220420", "hex"),
    Buffer.from(Array.from({ length: 32 }, (_, index) => index)),
  ]),
  format: "der",
  type: "pkcs8",
});

const privateKeyPem = privateKey.export({ format: "pem", type: "pkcs8" });
const publicKeyDer = createPublicKey(privateKeyPem).export({
  format: "der",
  type: "spki",
});

export const DEVICE_A = "00000000-0000-0000-0000-000000000001";
export const DEVICE_B = "00000000-0000-0000-0000-000000000002";

export const signingKeysResponse: TaskSigningKeysResponse = {
  keys: [{
    version: "test-v1",
    public_key_spki_der_b64: publicKeyDer.toString("base64"),
    sha256: createHash("sha256").update(publicKeyDer).digest("hex"),
  }],
};

export function signCanonicalValue(value: unknown): string {
  return sign(null, canonicalTaskBytes(value), privateKey)
    .toString("base64")
    .replaceAll("+", "-")
    .replaceAll("/", "_");
}

const baseTask = {
  task_id: "00000000-0000-0000-0000-000000000010",
  device_id: DEVICE_A,
  period_id: "2607270001",
  revision: 1,
  action: "BET",
  issued_at: "2026-07-27T04:00:00.000Z",
  expires_at: "2026-07-27T04:10:00.000Z",
  signing_key_version: "test-v1",
  payload: {
    signal_id: "00000000-0000-0000-0000-000000000100",
    signal_version: 1,
    actor_ref: "A000007",
    ball: 2,
    direction: "ODD",
    threshold_version: 8,
    odds_micros: 1_960_000,
    user_level: "CORE",
    sample_count: 618,
    conservative_win_rate: "0.5431000000",
    conservative_unit_return: "0.0645000000",
    followable_rate: "0.8120000000",
  },
};

export function signedTask(
  overrides: Record<string, unknown> = {},
): DeviceTaskEnvelope {
  const draft = structuredClone(baseTask) as Record<string, unknown>;
  Object.assign(draft, overrides);
  if (overrides.payload && typeof overrides.payload === "object") {
    draft.payload = {
      ...(baseTask.payload as Record<string, unknown>),
      ...(overrides.payload as Record<string, unknown>),
    };
  }
  if (draft.action === "CANCEL" && !overrides.payload) {
    draft.payload = { reason: "global_stop" };
  }
  const signature = signCanonicalValue(draft);
  return { ...draft, signature } as DeviceTaskEnvelope;
}
