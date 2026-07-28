import { randomUUID } from "node:crypto";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

import { safeStorage } from "electron";

export type ProtectedSessionCrypto = Pick<
  typeof safeStorage,
  "isEncryptionAvailable" | "encryptString" | "decryptString"
>;

export type SessionStorageEntry = readonly [string, string];

export type SessionSnapshotWriteResult =
  | "SAVED"
  | "UNCHANGED"
  | "ENCRYPTION_UNAVAILABLE";

export type SessionSnapshotDiagnostics = Readonly<{
  encryptionAvailable: boolean;
  loaded: boolean;
  snapshotPresent: boolean;
  loadStatus: "NOT_LOADED" | "READY_EMPTY" | "READY" | "UNAVAILABLE" | "FAILED";
  writeStatus: "NOT_ATTEMPTED" | SessionSnapshotWriteResult | "FAILED";
  errorCode: string | null;
}>;

type SessionSnapshot = {
  version: 1;
  origins: Record<string, SessionStorageEntry[]>;
};

const MAX_FILE_BYTES = 5 * 1024 * 1024;
const MAX_ORIGINS = 8;
const MAX_ENTRIES_PER_ORIGIN = 256;
const MAX_KEY_BYTES = 8 * 1024;
const MAX_VALUE_BYTES = 2 * 1024 * 1024;

export class ProtectedSessionSnapshotStore {
  private snapshot: SessionSnapshot = emptySnapshot();
  private loaded = false;
  private diagnostics: SessionSnapshotDiagnostics = {
    encryptionAvailable: false,
    loaded: false,
    snapshotPresent: false,
    loadStatus: "NOT_LOADED",
    writeStatus: "NOT_ATTEMPTED",
    errorCode: null,
  };

  constructor(
    private readonly filePath: string,
    private readonly crypto: ProtectedSessionCrypto = safeStorage,
  ) {}

  async load(): Promise<void> {
    this.loaded = true;
    this.snapshot = emptySnapshot();
    const encryptionAvailable = encryptionIsAvailable(this.crypto);
    this.diagnostics = {
      encryptionAvailable,
      loaded: true,
      snapshotPresent: false,
      loadStatus: encryptionAvailable ? "READY_EMPTY" : "UNAVAILABLE",
      writeStatus: "NOT_ATTEMPTED",
      errorCode: encryptionAvailable ? null : "ENCRYPTION_UNAVAILABLE",
    };
    if (!encryptionAvailable) return;
    try {
      const encrypted = await readFile(this.filePath);
      if (encrypted.byteLength === 0 || encrypted.byteLength > MAX_FILE_BYTES) {
        this.recordLoadFailure("SNAPSHOT_SIZE_INVALID");
        return;
      }
      const parsed = JSON.parse(this.crypto.decryptString(encrypted)) as unknown;
      const snapshot = parseSnapshot(parsed);
      if (snapshot === null) {
        this.recordLoadFailure("SNAPSHOT_CONTENT_INVALID");
        return;
      }
      this.snapshot = snapshot;
      this.diagnostics = {
        ...this.diagnostics,
        snapshotPresent: true,
        loadStatus: "READY",
        errorCode: null,
      };
    } catch (error) {
      if (isMissingFile(error)) return;
      this.snapshot = emptySnapshot();
      this.recordLoadFailure("SNAPSHOT_LOAD_FAILED");
    }
  }

  getDiagnostics(): SessionSnapshotDiagnostics {
    return { ...this.diagnostics };
  }

  entriesForOrigin(origin: string): SessionStorageEntry[] {
    if (!validOrigin(origin) || !this.loaded ||
        !this.crypto.isEncryptionAvailable()) return [];
    return cloneEntries(this.snapshot.origins[origin] ?? []);
  }

  async replaceOrigin(
    origin: string,
    entries: readonly SessionStorageEntry[],
  ): Promise<SessionSnapshotWriteResult> {
    if (!validOrigin(origin)) throw new Error("platform_session_origin_invalid");
    const normalized = normalizeEntries(entries);
    if (!this.loaded) throw new Error("platform_session_snapshot_not_loaded");
    if (!encryptionIsAvailable(this.crypto)) {
      this.diagnostics = {
        ...this.diagnostics,
        encryptionAvailable: false,
        writeStatus: "ENCRYPTION_UNAVAILABLE",
        errorCode: "ENCRYPTION_UNAVAILABLE",
      };
      return "ENCRYPTION_UNAVAILABLE";
    }

    const next: SessionSnapshot = {
      version: 1,
      origins: {
        ...this.snapshot.origins,
        [origin]: normalized,
      },
    };
    if (Object.keys(next.origins).length > MAX_ORIGINS) {
      throw new Error("platform_session_origin_limit");
    }
    if (JSON.stringify(next) === JSON.stringify(this.snapshot)) {
      this.diagnostics = {
        ...this.diagnostics,
        writeStatus: "UNCHANGED",
        errorCode: null,
      };
      return "UNCHANGED";
    }

    const encrypted = this.crypto.encryptString(JSON.stringify(next));
    if (encrypted.byteLength === 0 || encrypted.byteLength > MAX_FILE_BYTES) {
      throw new Error("platform_session_snapshot_too_large");
    }
    const temporary = `${this.filePath}.${randomUUID()}.tmp`;
    await mkdir(dirname(this.filePath), { recursive: true });
    try {
      await writeFile(temporary, encrypted, { mode: 0o600 });
      await rename(temporary, this.filePath);
      this.snapshot = next;
      this.diagnostics = {
        ...this.diagnostics,
        snapshotPresent: true,
        writeStatus: "SAVED",
        errorCode: null,
      };
      return "SAVED";
    } catch (error) {
      await rm(temporary, { force: true });
      this.diagnostics = {
        ...this.diagnostics,
        writeStatus: "FAILED",
        errorCode: "SNAPSHOT_WRITE_FAILED",
      };
      throw error;
    }
  }

  private recordLoadFailure(errorCode: string): void {
    this.diagnostics = {
      ...this.diagnostics,
      snapshotPresent: false,
      loadStatus: "FAILED",
      errorCode,
    };
  }
}

function encryptionIsAvailable(crypto: ProtectedSessionCrypto): boolean {
  try {
    return crypto.isEncryptionAvailable();
  } catch {
    return false;
  }
}

function isMissingFile(error: unknown): boolean {
  return isObject(error) && error.code === "ENOENT";
}

function emptySnapshot(): SessionSnapshot {
  return { version: 1, origins: {} };
}

function parseSnapshot(value: unknown): SessionSnapshot | null {
  if (!isObject(value) || value.version !== 1 || !isObject(value.origins) ||
      Object.keys(value).sort().join(",") !== "origins,version") return null;
  const originPairs = Object.entries(value.origins);
  if (originPairs.length > MAX_ORIGINS) return null;
  const origins: Record<string, SessionStorageEntry[]> = {};
  try {
    for (const [origin, entries] of originPairs) {
      if (!validOrigin(origin) || !Array.isArray(entries)) return null;
      origins[origin] = normalizeEntries(entries as SessionStorageEntry[]);
    }
  } catch {
    return null;
  }
  return { version: 1, origins };
}

function normalizeEntries(
  entries: readonly SessionStorageEntry[],
): SessionStorageEntry[] {
  if (entries.length > MAX_ENTRIES_PER_ORIGIN) {
    throw new Error("platform_session_entry_limit");
  }
  const result: SessionStorageEntry[] = [];
  const keys = new Set<string>();
  for (const entry of entries) {
    if (!Array.isArray(entry) || entry.length !== 2 ||
        typeof entry[0] !== "string" || typeof entry[1] !== "string" ||
        Buffer.byteLength(entry[0], "utf8") > MAX_KEY_BYTES ||
        Buffer.byteLength(entry[1], "utf8") > MAX_VALUE_BYTES ||
        keys.has(entry[0])) {
      throw new Error("platform_session_entry_invalid");
    }
    keys.add(entry[0]);
    result.push([entry[0], entry[1]]);
  }
  result.sort(([left], [right]) => left.localeCompare(right));
  return result;
}

function cloneEntries(entries: readonly SessionStorageEntry[]): SessionStorageEntry[] {
  return entries.map(([key, value]) => [key, value]);
}

function validOrigin(value: string): boolean {
  if (value.length > 256) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.origin === value &&
      !url.username && !url.password;
  } catch {
    return false;
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
