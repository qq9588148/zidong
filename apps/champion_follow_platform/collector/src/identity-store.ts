import { randomBytes } from "node:crypto";
import {
  chmod,
  mkdir,
  open,
  readFile,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import { dirname } from "node:path";

import {
  decryptRecoveryKey,
  encryptRecoveryKey,
  type RecoveryEnvelope,
} from "./recovery-envelope.js";

export interface EncryptionPort {
  isEncryptionAvailable(): boolean;
  encryptString(value: string): Buffer;
  decryptString(value: Buffer): string;
  getSelectedStorageBackend?(): string;
}

export class IdentityStore {
  constructor(
    private readonly path: string,
    private readonly encryption: EncryptionPort,
  ) {}

  async loadOrCreate(): Promise<Buffer> {
    this.requireEncryption();
    await mkdir(dirname(this.path), { recursive: true, mode: 0o700 });
    try {
      const key = Buffer.from(
        this.encryption.decryptString(await readFile(this.path)),
        "base64",
      );
      if (key.length !== 32) {
        throw new Error("identity_key_invalid");
      }
      return key;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
        throw error;
      }
    }

    const key = randomBytes(32);
    await this.writeEncrypted(key);
    return key;
  }

  async exportRecoveryEnvelope(publicKeyPem: string): Promise<RecoveryEnvelope> {
    const key = await this.loadOrCreate();
    try {
      return encryptRecoveryKey(key, publicKeyPem);
    } finally {
      key.fill(0);
    }
  }

  async restoreRecoveryEnvelope(
    envelope: unknown,
    privateKeyPem: string,
  ): Promise<void> {
    const key = decryptRecoveryKey(envelope, privateKeyPem);
    try {
      await this.writeEncrypted(key);
    } finally {
      key.fill(0);
    }
  }

  private requireEncryption(): void {
    if (
      !this.encryption.isEncryptionAvailable() ||
      this.encryption.getSelectedStorageBackend?.() === "basic_text"
    ) {
      throw new Error("identity_encryption_unavailable");
    }
  }

  private async writeEncrypted(key: Buffer): Promise<void> {
    this.requireEncryption();
    await mkdir(dirname(this.path), { recursive: true, mode: 0o700 });
    const temporary = `${this.path}.new`;
    await rm(temporary, { force: true });
    const encrypted = this.encryption.encryptString(key.toString("base64"));
    await writeFile(temporary, encrypted, { mode: 0o600, flag: "wx" });
    await chmod(temporary, 0o600);
    const handle = await open(temporary, "r+");
    try {
      await handle.sync();
    } finally {
      await handle.close();
    }
    await rename(temporary, this.path);
  }
}
