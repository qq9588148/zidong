import { describe, expect, it } from "vitest";

import {
  HighestRevisionTasks,
  TrustedTaskSigningKeys,
} from "../../src/main/task-contract";
import {
  DEVICE_A,
  DEVICE_B,
  signedTask,
  signingKeysResponse,
} from "../helpers/signed-task";

const now = () => Date.parse("2026-07-27T04:05:00.000Z");

describe("HighestRevisionTasks", () => {
  it("never lets an older BET revive a newer CANCEL", () => {
    const reducer = new HighestRevisionTasks(
      DEVICE_A,
      TrustedTaskSigningKeys.fromResponse(signingKeysResponse),
      now,
    );

    expect(reducer.accept(signedTask({ revision: 7 }))).toBe("accepted");
    expect(reducer.accept(signedTask({ revision: 8, action: "CANCEL" })))
      .toBe("accepted");
    expect(reducer.accept(signedTask({ revision: 7 }))).toBe("stale");
    expect(reducer.current("2607270001")?.action).toBe("CANCEL");
  });

  it("rejects wrong device, expiry, bad signatures and unknown keys", () => {
    const reducer = new HighestRevisionTasks(
      DEVICE_A,
      TrustedTaskSigningKeys.fromResponse(signingKeysResponse),
      now,
    );

    expect(reducer.accept(signedTask({ device_id: DEVICE_B })))
      .toBe("wrong_device");
    expect(reducer.accept(signedTask({ expires_at: "2026-07-27T04:04:59.000Z" })))
      .toBe("expired");
    expect(reducer.accept({ ...signedTask(), signature: "A".repeat(86) + "==" }))
      .toBe("bad_signature");
    expect(reducer.accept({ ...signedTask(), signing_key_version: "unknown-v1" }))
      .toBe("unknown_signing_key");
  });

  it("rejects schema extras and conflicting bodies at the same revision", () => {
    const reducer = new HighestRevisionTasks(
      DEVICE_A,
      TrustedTaskSigningKeys.fromResponse(signingKeysResponse),
      now,
    );
    const accepted = signedTask({ revision: 4 });
    expect(reducer.accept(accepted)).toBe("accepted");
    expect(reducer.accept(accepted)).toBe("duplicate");
    expect(reducer.accept(signedTask({ revision: 4, task_id: "00000000-0000-0000-0000-000000000099" })))
      .toBe("revision_conflict");
    expect(reducer.accept({ ...signedTask({ revision: 5 }), unexpected: true }))
      .toBe("invalid_schema");
  });

  it("validates HTTPS-delivered Ed25519 signing keys", () => {
    const keys = TrustedTaskSigningKeys.fromResponse(signingKeysResponse);
    expect(keys.has("test-v1")).toBe(true);
    expect(() => TrustedTaskSigningKeys.fromResponse({
      keys: [{ ...signingKeysResponse.keys[0]!, sha256: "0".repeat(64) }],
    })).toThrow("task_signing_key_digest_mismatch");
  });
});
