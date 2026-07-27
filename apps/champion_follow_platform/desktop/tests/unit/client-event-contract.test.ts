import { generateKeyPairSync, sign, verify } from "node:crypto";
import { describe, expect, it } from "vitest";

import {
  ClientEventContract,
  canonicalClientEventBytes,
} from "../../src/main/client-event-contract";
import type { NativeHelper } from "../../src/main/native-helper";

const { privateKey, publicKey } = generateKeyPairSync("ec", {
  namedCurve: "prime256v1",
});

const helper: NativeHelper = {
  publicKeySpkiDerBase64: async () => "unused",
  signEcdsaSha256DerBase64: async (_keyName, payload) =>
    sign("sha256", payload, { key: privateKey, dsaEncoding: "der" }).toString("base64"),
  writeCredential: async () => undefined,
  readCredential: async () => null,
  deleteCredential: async () => undefined,
};

describe("ClientEventContract", () => {
  it("creates a strict ECDSA P-256 DER signed event", async () => {
    const contract = new ClientEventContract({
      deviceId: "00000000-0000-0000-0000-000000000001",
      bindingEpoch: 1,
      helper,
      keyName: "ChampionFollow/Device/test",
      now: () => new Date("2026-07-27T04:00:00.000Z"),
      uuid: () => "00000000-0000-0000-0000-000000000402",
    });
    const built = await contract.build(2, "EXECUTION_STATE", {
      task_id: "00000000-0000-0000-0000-000000000010",
      period_id: "2607270001",
      revision: 1,
      state: "SUBMITTING",
    });
    const { signature, ...unsigned } = built.envelope;
    expect(verify(
      "sha256",
      canonicalClientEventBytes(unsigned),
      publicKey,
      Buffer.from(signature, "base64"),
    )).toBe(true);
    expect(built.bytes.equals(Buffer.from(JSON.stringify(built.envelope), "utf8")))
      .toBe(true);
  });

  it("rejects payload aliases and extra fields", async () => {
    const contract = new ClientEventContract({
      deviceId: "00000000-0000-0000-0000-000000000001",
      bindingEpoch: 1,
      helper,
      keyName: "ChampionFollow/Device/test",
    });
    await expect(contract.build(1, "EXECUTION_STATE", {
      task_id: "00000000-0000-0000-0000-000000000010",
      period_id: "2607270001",
      revision: 1,
      state: "SUBMITTING",
      taskRevision: 1,
    })).rejects.toThrow("client_event_payload_invalid");
  });
});
