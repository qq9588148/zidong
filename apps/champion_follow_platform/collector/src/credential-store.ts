import { constants } from "node:fs";
import {
  chmod,
  mkdir,
  open,
  readFile,
  rename,
  rm,
} from "node:fs/promises";
import type { FileHandle } from "node:fs/promises";
import { dirname } from "node:path";

import { z } from "zod";

import type { EncryptionPort } from "./identity-store.js";

const MAX_CREDENTIAL_BYTES = 4096;
const schema = z
  .object({
    format: z.literal("champion-collector-credential-v1"),
    collector_id: z.string().regex(/^collector-[a-z0-9-]{3,64}$/),
    bearer: z.string().min(48).max(256).regex(/^[A-Za-z0-9_-]+$/),
  })
  .strict();

export type CollectorCredential = z.infer<typeof schema>;
export type CredentialImportMode =
  | { kind: "stored" }
  | { kind: "file"; path: string }
  | { kind: "stdin" };

function safeError(code: string): Error {
  return new Error(code);
}

function hasSystemProtection(encryption: EncryptionPort): boolean {
  return (
    encryption.isEncryptionAvailable() &&
    encryption.getSelectedStorageBackend?.() !== "basic_text"
  );
}

export function parseCredentialImportArgs(argv: string[]): CredentialImportMode {
  if (argv.some((value) => value === "--bearer" || value.startsWith("--bearer="))) {
    throw safeError("collector_credential_argument_forbidden");
  }
  const fileAt = argv.indexOf("--credential-handoff");
  const stdinAt = argv.indexOf("--credential-stdin");
  if (
    (fileAt >= 0 && stdinAt >= 0) ||
    argv.lastIndexOf("--credential-handoff") !== fileAt ||
    argv.lastIndexOf("--credential-stdin") !== stdinAt
  ) {
    throw safeError("collector_credential_argument_invalid");
  }
  if (fileAt >= 0) {
    const path = argv[fileAt + 1];
    if (!path || path.startsWith("--") || argv.length !== 2) {
      throw safeError("collector_credential_argument_invalid");
    }
    return { kind: "file", path };
  }
  if (stdinAt >= 0) {
    if (argv.length !== 1) {
      throw safeError("collector_credential_argument_invalid");
    }
    return { kind: "stdin" };
  }
  if (argv.length !== 0) {
    throw safeError("collector_credential_argument_invalid");
  }
  return { kind: "stored" };
}

export function parseCredentialImportProcessArgs(
  argv: string[],
  isPackaged: boolean,
): CredentialImportMode {
  return parseCredentialImportArgs(argv.slice(isPackaged ? 1 : 2));
}

export function credentialInputStream(
  platform: NodeJS.Platform,
  stdin: AsyncIterable<string | Uint8Array>,
  readInput: (input: number | string) => Buffer,
  windowsPipe?: string,
): AsyncIterable<string | Uint8Array> {
  if (platform !== "win32") return stdin;
  if (
    windowsPipe !== undefined &&
    !/^\\\\\.\\pipe\\champion-follow-collector-[0-9a-f]{32}$/.test(
      windowsPipe,
    )
  ) {
    throw safeError("collector_credential_pipe_invalid");
  }
  return (async function* () {
    const bytes = readInput(windowsPipe ?? 0);
    try {
      yield bytes;
    } finally {
      bytes.fill(0);
    }
  })();
}

function parseCredential(raw: Buffer): CollectorCredential {
  if (raw.length === 0) {
    throw safeError("collector_credential_input_empty");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw.toString("utf8"));
  } catch {
    throw safeError("collector_credential_json_invalid");
  }
  const result = schema.safeParse(parsed);
  if (!result.success) {
    throw safeError("collector_credential_schema_invalid");
  }
  return result.data;
}

export class CollectorCredentialStore {
  constructor(
    private readonly path: string,
    private readonly encryption: EncryptionPort,
  ) {}

  async load(): Promise<CollectorCredential> {
    if (!hasSystemProtection(this.encryption)) {
      throw safeError("collector_credential_encryption_unavailable");
    }
    let encrypted: Buffer;
    try {
      encrypted = await readFile(this.path);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        throw safeError("collector_credential_missing");
      }
      throw safeError("collector_credential_read_failed");
    }
    try {
      return schema.parse(JSON.parse(this.encryption.decryptString(encrypted)));
    } catch {
      throw safeError("collector_credential_invalid");
    } finally {
      encrypted.fill(0);
    }
  }

  async importFromFile(sourcePath: string): Promise<CollectorCredential> {
    if (process.platform === "win32") {
      throw safeError("collector_credential_use_stdin_on_windows");
    }
    const noFollow =
      (constants as unknown as Record<string, number>).O_NOFOLLOW ?? 0;
    let handle: FileHandle | undefined;
    let raw: Buffer | undefined;
    try {
      handle = await open(sourcePath, constants.O_RDONLY | noFollow);
      const stat = await handle.stat();
      const wrongOwner =
        typeof process.getuid === "function" && stat.uid !== process.getuid();
      if (!stat.isFile() || wrongOwner || (stat.mode & 0o077) !== 0) {
        throw safeError("collector_credential_permissions_invalid");
      }
      if (stat.size < 1 || stat.size > MAX_CREDENTIAL_BYTES) {
        throw safeError("collector_credential_invalid");
      }
      raw = await handle.readFile();
      const credential = parseCredential(raw);
      await this.persist(credential);
      await handle.close();
      handle = undefined;
      try {
        await rm(sourcePath);
      } catch {
        throw safeError("collector_credential_source_delete_failed");
      }
      return credential;
    } catch (error) {
      const code = (error as Error).message;
      if (code.startsWith("collector_credential_")) {
        throw error;
      }
      throw safeError("collector_credential_input_failed");
    } finally {
      raw?.fill(0);
      if (handle) {
        await handle.close().catch(() => undefined);
      }
    }
  }

  async importFromStdin(
    input: AsyncIterable<string | Uint8Array>,
  ): Promise<CollectorCredential> {
    const chunks: Buffer[] = [];
    let size = 0;
    try {
      for await (const chunk of input) {
        const bytes = Buffer.from(chunk);
        chunks.push(bytes);
        size += bytes.length;
        if (size > MAX_CREDENTIAL_BYTES) {
          throw safeError("collector_credential_invalid");
        }
      }
      const raw = Buffer.concat(chunks);
      try {
        const credential = parseCredential(raw);
        await this.persist(credential);
        return credential;
      } finally {
        raw.fill(0);
      }
    } finally {
      for (const chunk of chunks) {
        chunk.fill(0);
      }
    }
  }

  private async persist(value: CollectorCredential): Promise<void> {
    if (!hasSystemProtection(this.encryption)) {
      throw safeError("collector_credential_encryption_unavailable");
    }
    await mkdir(dirname(this.path), { recursive: true, mode: 0o700 });
    let exists = true;
    try {
      await readFile(this.path);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        exists = false;
      } else {
        throw safeError("collector_credential_store_failed");
      }
    }
    if (exists) {
      throw safeError("collector_credential_already_initialized");
    }

    let encrypted: Buffer;
    try {
      encrypted = this.encryption.encryptString(JSON.stringify(value));
    } catch {
      throw safeError("collector_credential_store_failed");
    }
    const temporary = `${this.path}.new`;
    try {
      await rm(temporary, { force: true });
      const handle = await open(temporary, "wx", 0o600);
      try {
        await handle.writeFile(encrypted);
        await handle.sync();
      } finally {
        await handle.close();
      }
      await chmod(temporary, 0o600);
      await rename(temporary, this.path);
    } catch {
      await rm(temporary, { force: true }).catch(() => undefined);
      throw safeError("collector_credential_store_failed");
    } finally {
      encrypted.fill(0);
    }
  }
}
