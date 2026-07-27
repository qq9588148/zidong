import { generateKeyPairSync } from "node:crypto";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { IdentityStore } from "../src/identity-store.js";

const encryption = {
  isEncryptionAvailable: () => true,
  encryptString: (value: string) => Buffer.from(`sealed:${value}`, "utf8"),
  decryptString: (value: Buffer) => value.toString("utf8").slice(7),
};

describe("IdentityStore", () => {
  it("keeps one stable key without writing plaintext", async () => {
    const root = await mkdtemp(join(tmpdir(), "collector-id-"));
    const store = new IdentityStore(join(root, "identity-key.enc"), encryption);
    const first = await store.loadOrCreate();
    expect(await store.loadOrCreate()).toEqual(first);
    expect((await readFile(join(root, "identity-key.enc"))).includes(first)).toBe(
      false,
    );
    await rm(root, { recursive: true });
  });

  it("restores the same namespace from RSA-OAEP", async () => {
    const root = await mkdtemp(join(tmpdir(), "collector-restore-"));
    const pair = generateKeyPairSync("rsa", { modulusLength: 2048 });
    const first = new IdentityStore(join(root, "first.enc"), encryption);
    const key = await first.loadOrCreate();
    const envelope = await first.exportRecoveryEnvelope(
      pair.publicKey.export({ type: "spki", format: "pem" }).toString(),
    );
    const restored = new IdentityStore(join(root, "restored.enc"), encryption);
    await restored.restoreRecoveryEnvelope(
      envelope,
      pair.privateKey.export({ type: "pkcs8", format: "pem" }).toString(),
    );
    expect(await restored.loadOrCreate()).toEqual(key);
    expect(JSON.stringify(envelope)).not.toContain(key.toString("base64"));
    await rm(root, { recursive: true });
  });

  it("fails closed without OS encryption", async () => {
    const root = await mkdtemp(join(tmpdir(), "collector-no-crypto-"));
    const store = new IdentityStore(join(root, "identity.enc"), {
      ...encryption,
      isEncryptionAvailable: () => false,
    });
    await expect(store.loadOrCreate()).rejects.toThrow(
      "identity_encryption_unavailable",
    );
    await rm(root, { recursive: true });
  });
});
