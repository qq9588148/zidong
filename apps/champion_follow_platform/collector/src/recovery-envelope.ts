import { privateDecrypt, publicEncrypt } from "node:crypto";

import { z } from "zod";

const schema = z
  .object({
    format: z.literal("champion-collector-identity-backup-v1"),
    namespaceVersion: z.literal("actor-hmac-v1"),
    ciphertext: z.string().regex(/^[A-Za-z0-9+/]+={0,2}$/),
  })
  .strict();

export type RecoveryEnvelope = z.infer<typeof schema>;

export function encryptRecoveryKey(
  key: Buffer,
  publicKeyPem: string,
): RecoveryEnvelope {
  const plaintext = Buffer.from(
    JSON.stringify({
      namespaceVersion: "actor-hmac-v1",
      key: key.toString("base64"),
    }),
  );
  try {
    const ciphertext = publicEncrypt(
      { key: publicKeyPem, oaepHash: "sha256" },
      plaintext,
    );
    return {
      format: "champion-collector-identity-backup-v1",
      namespaceVersion: "actor-hmac-v1",
      ciphertext: ciphertext.toString("base64"),
    };
  } finally {
    plaintext.fill(0);
  }
}

export function decryptRecoveryKey(
  value: unknown,
  privateKeyPem: string,
): Buffer {
  const envelope = schema.parse(value);
  const plaintext = privateDecrypt(
    { key: privateKeyPem, oaepHash: "sha256" },
    Buffer.from(envelope.ciphertext, "base64"),
  );
  try {
    const parsed = JSON.parse(plaintext.toString()) as {
      namespaceVersion?: unknown;
      key?: unknown;
    };
    if (
      parsed.namespaceVersion !== "actor-hmac-v1" ||
      typeof parsed.key !== "string"
    ) {
      throw new Error("identity_recovery_invalid");
    }
    const key = Buffer.from(parsed.key, "base64");
    if (key.length !== 32) {
      throw new Error("identity_recovery_invalid");
    }
    return key;
  } finally {
    plaintext.fill(0);
  }
}
