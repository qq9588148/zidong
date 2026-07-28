import { chmod, lstat, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Readable } from "node:stream";

import { describe, expect, it } from "vitest";

import {
  CollectorCredentialStore,
  credentialInputStream,
  parseCredentialImportArgs,
  parseCredentialImportProcessArgs,
} from "../src/credential-store.js";

const fixtureBearer = `synthetic_fixture_${"x".repeat(48)}`;
const bundle = {
  format: "champion-collector-credential-v1" as const,
  collector_id: "collector-main-01",
  bearer: fixtureBearer,
};
const encryption = {
  isEncryptionAvailable: () => true,
  encryptString: (value: string) => Buffer.from(value, "utf8").reverse(),
  decryptString: (value: Buffer) => Buffer.from(value).reverse().toString("utf8"),
};

describe("CollectorCredentialStore", () => {
  it("imports a 0600 handoff, persists ciphertext, and unlinks the source", async () => {
    if (process.platform === "win32") return;
    const root = await mkdtemp(join(tmpdir(), "collector-credential-"));
    const source = join(root, "handoff.json");
    const target = join(root, "collector-credential.enc");
    await writeFile(source, JSON.stringify(bundle), { mode: 0o600 });
    const store = new CollectorCredentialStore(target, encryption);
    expect(await store.importFromFile(source)).toEqual(bundle);
    await expect(lstat(source)).rejects.toMatchObject({ code: "ENOENT" });
    expect(await readFile(target, "utf8")).not.toContain(fixtureBearer);
    expect(await store.load()).toEqual(bundle);
    await rm(root, { recursive: true });
  });

  it("imports from stdin without accepting a token argument", async () => {
    const root = await mkdtemp(join(tmpdir(), "collector-stdin-"));
    const store = new CollectorCredentialStore(
      join(root, "collector-credential.enc"),
      encryption,
    );
    await store.importFromStdin(Readable.from([JSON.stringify(bundle)]));
    expect(await store.load()).toEqual(bundle);
    await rm(root, { recursive: true });
  });

  it("accepts only a handoff path or stdin mode in argv", () => {
    expect(
      parseCredentialImportArgs([
        "--credential-handoff",
        "/private/handoff.json",
      ]),
    ).toEqual({ kind: "file", path: "/private/handoff.json" });
    expect(parseCredentialImportArgs(["--credential-stdin"])).toEqual({
      kind: "stdin",
    });
    expect(parseCredentialImportArgs([])).toEqual({ kind: "stored" });
    expect(() => parseCredentialImportArgs(["--bearer", fixtureBearer])).toThrow(
      "collector_credential_argument_forbidden",
    );
    expect(() =>
      parseCredentialImportArgs([
        "--credential-handoff",
        "a",
        "--credential-stdin",
      ]),
    ).toThrow("collector_credential_argument_invalid");
  });

  it("reads packaged Windows credential input from file descriptor zero", async () => {
    const root = await mkdtemp(join(tmpdir(), "collector-windows-stdin-"));
    const store = new CollectorCredentialStore(
      join(root, "collector-credential.enc"),
      encryption,
    );
    const source = credentialInputStream(
      "win32",
      Readable.from([]),
      (fileDescriptor) => {
        expect(fileDescriptor).toBe(0);
        return Buffer.from(JSON.stringify(bundle));
      },
    );
    await store.importFromStdin(source);
    expect(await store.load()).toEqual(bundle);
    await rm(root, { recursive: true });
  });

  it("reads packaged Windows credential input from a validated named pipe", async () => {
    const pipe = "\\\\.\\pipe\\champion-follow-collector-0123456789abcdef0123456789abcdef";
    const seen: Array<number | string> = [];
    const source = credentialInputStream(
      "win32",
      Readable.from([]),
      (input) => {
        seen.push(input);
        return Buffer.from(JSON.stringify(bundle));
      },
      pipe,
    );
    const chunks: Buffer[] = [];
    for await (const chunk of source) chunks.push(Buffer.from(chunk));
    expect(JSON.parse(Buffer.concat(chunks).toString("utf8"))).toEqual(bundle);
    expect(seen).toEqual([pipe]);
    expect(() =>
      credentialInputStream(
        "win32",
        Readable.from([]),
        () => Buffer.alloc(0),
        "C:\\temp\\credential.json",
      )
    ).toThrow("collector_credential_pipe_invalid");
  });

  it("reports safe stdin framing errors without exposing input", async () => {
    const root = await mkdtemp(join(tmpdir(), "collector-stdin-errors-"));
    const store = new CollectorCredentialStore(
      join(root, "collector-credential.enc"),
      encryption,
    );
    await expect(store.importFromStdin(Readable.from([]))).rejects.toThrow(
      "collector_credential_input_empty",
    );
    await expect(
      store.importFromStdin(Readable.from(["not-json"])),
    ).rejects.toThrow("collector_credential_json_invalid");
    await expect(
      store.importFromStdin(Readable.from([JSON.stringify({ format: "wrong" })])),
    ).rejects.toThrow("collector_credential_schema_invalid");
    await rm(root, { recursive: true });
  });

  it("reads credential flags from packaged and development Electron argv", () => {
    expect(
      parseCredentialImportProcessArgs(
        ["Champion Follow Collector.exe", "--credential-stdin"],
        true,
      ),
    ).toEqual({ kind: "stdin" });
    expect(
      parseCredentialImportProcessArgs(
        ["electron.exe", "main.js", "--credential-stdin"],
        false,
      ),
    ).toEqual({ kind: "stdin" });
  });

  it("rejects a group-readable handoff before reading it", async () => {
    if (process.platform === "win32") return;
    const root = await mkdtemp(join(tmpdir(), "collector-mode-"));
    const source = join(root, "handoff.json");
    await writeFile(source, JSON.stringify(bundle), { mode: 0o600 });
    await chmod(source, 0o640);
    const store = new CollectorCredentialStore(
      join(root, "collector-credential.enc"),
      encryption,
    );
    await expect(store.importFromFile(source)).rejects.toThrow(
      "collector_credential_permissions_invalid",
    );
    await rm(root, { recursive: true });
  });

  it("keeps the owner-only source for retry when safeStorage persistence fails", async () => {
    if (process.platform === "win32") return;
    const root = await mkdtemp(join(tmpdir(), "collector-retry-"));
    const source = join(root, "handoff.json");
    await writeFile(source, JSON.stringify(bundle), { mode: 0o600 });
    const broken = {
      ...encryption,
      encryptString: () => {
        throw new Error(fixtureBearer);
      },
    };
    const store = new CollectorCredentialStore(
      join(root, "collector-credential.enc"),
      broken,
    );
    await expect(store.importFromFile(source)).rejects.toThrow(
      "collector_credential_store_failed",
    );
    expect(await readFile(source, "utf8")).toContain(fixtureBearer);
    await rm(root, { recursive: true });
  });

  it("rejects Electron's unprotected Linux basic_text backend", async () => {
    const root = await mkdtemp(join(tmpdir(), "collector-basic-text-"));
    const unprotected = {
      ...encryption,
      getSelectedStorageBackend: () => "basic_text",
    };
    const store = new CollectorCredentialStore(
      join(root, "collector-credential.enc"),
      unprotected,
    );
    await expect(
      store.importFromStdin(Readable.from([JSON.stringify(bundle)])),
    ).rejects.toThrow("collector_credential_encryption_unavailable");
    await rm(root, { recursive: true });
  });
});
