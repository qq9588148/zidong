import { createHash } from "node:crypto";
import { describe, expect, it, vi } from "vitest";

import { ProcessNativeHelper } from "../../src/main/native-helper";

const helperBytes = Buffer.from("trusted-helper-fixture");
const helperHash = createHash("sha256").update(helperBytes).digest("hex");

describe("ProcessNativeHelper", () => {
  it("checks the helper hash before sending a command", async () => {
    const spawnJson = vi.fn();
    const helper = new ProcessNativeHelper("fixture.exe", "0".repeat(64), {
      readBytes: async () => helperBytes,
      spawnJson,
    });

    await expect(helper.publicKeySpkiDerBase64("ChampionFollow/Device/test"))
      .rejects.toThrow("native_helper_integrity_mismatch");
    expect(spawnJson).not.toHaveBeenCalled();
  });

  it("maps credentials and signatures through the narrow JSON protocol", async () => {
    const calls: unknown[] = [];
    const spawnJson = vi.fn(async (_path: string, command: unknown) => {
      calls.push(command);
      const name = (command as { command: string }).command;
      if (name === "credential_read") return { ok: true, value: "fixture-value" };
      if (name === "sign_ecdsa_sha256_der") {
        return { ok: true, signatureDerBase64: "MEU=" };
      }
      return { ok: true };
    });
    const helper = new ProcessNativeHelper("fixture.exe", helperHash, {
      readBytes: async () => helperBytes,
      spawnJson,
    });

    await helper.writeCredential("ChampionFollow/AppRefresh/test", "fixture-value");
    expect(await helper.readCredential("ChampionFollow/AppRefresh/test"))
      .toBe("fixture-value");
    expect(await helper.signEcdsaSha256DerBase64(
      "ChampionFollow/Device/test",
      new Uint8Array([1, 2, 3]),
    )).toBe("MEU=");

    expect(calls).toContainEqual({
      command: "credential_write",
      target: "ChampionFollow/AppRefresh/test",
      value: "fixture-value",
    });
    expect(calls).toContainEqual({
      command: "sign_ecdsa_sha256_der",
      keyName: "ChampionFollow/Device/test",
      payloadBase64: "AQID",
    });
  });
});
