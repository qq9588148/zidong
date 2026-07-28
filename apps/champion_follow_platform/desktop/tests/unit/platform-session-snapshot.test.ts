import { readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { mkdtemp } from "node:fs/promises";

import { afterEach, describe, expect, it } from "vitest";

import {
  ProtectedSessionSnapshotStore,
  type ProtectedSessionCrypto,
} from "../../src/main/platform-session-snapshot";

const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(temporaryDirectories.splice(0).map((directory) =>
    rm(directory, { recursive: true, force: true })));
});

describe("ProtectedSessionSnapshotStore", () => {
  it("round-trips session storage without writing plaintext", async () => {
    const directory = await mkdtemp(join(tmpdir(), "champion-session-"));
    temporaryDirectories.push(directory);
    const filePath = join(directory, "session-storage.enc");
    const crypto = fakeCrypto();
    const store = new ProtectedSessionSnapshotStore(filePath, crypto);
    await store.load();

    expect(await store.replaceOrigin("https://ng.example.test", [
      ["session-key", "session-token-canary"],
      ["profile", "fixture-user"],
    ])).toBe("SAVED");
    expect(store.getDiagnostics()).toMatchObject({
      encryptionAvailable: true,
      loaded: true,
      snapshotPresent: true,
      loadStatus: "READY_EMPTY",
      writeStatus: "SAVED",
      errorCode: null,
    });

    const encrypted = await readFile(filePath);
    expect(encrypted.toString("utf8")).not.toContain("session-token-canary");
    expect(encrypted.toString("utf8")).not.toContain("fixture-user");

    const restored = new ProtectedSessionSnapshotStore(filePath, crypto);
    await restored.load();
    expect(restored.getDiagnostics()).toMatchObject({
      loadStatus: "READY",
      snapshotPresent: true,
    });
    expect(restored.entriesForOrigin("https://ng.example.test")).toEqual([
      ["profile", "fixture-user"],
      ["session-key", "session-token-canary"],
    ]);
  });

  it("fails closed for unavailable encryption and invalid origins", async () => {
    const directory = await mkdtemp(join(tmpdir(), "champion-session-"));
    temporaryDirectories.push(directory);
    const filePath = join(directory, "session-storage.enc");
    const unavailable: ProtectedSessionCrypto = {
      isEncryptionAvailable: () => false,
      encryptString: () => Buffer.from("unexpected"),
      decryptString: () => "unexpected",
    };
    const store = new ProtectedSessionSnapshotStore(filePath, unavailable);
    await store.load();

    await expect(store.replaceOrigin("http://ng.example.test", [
      ["key", "value"],
    ])).rejects.toThrow("platform_session_origin_invalid");
    expect(await store.replaceOrigin(
      "https://ng.example.test",
      [["key", "value"]],
    )).toBe("ENCRYPTION_UNAVAILABLE");
    await expect(readFile(filePath)).rejects.toMatchObject({ code: "ENOENT" });
    expect(store.entriesForOrigin("https://ng.example.test")).toEqual([]);
    expect(store.getDiagnostics()).toMatchObject({
      loadStatus: "UNAVAILABLE",
      writeStatus: "ENCRYPTION_UNAVAILABLE",
      errorCode: "ENCRYPTION_UNAVAILABLE",
    });
  });

  it("reports a missing snapshot without treating it as an error", async () => {
    const directory = await mkdtemp(join(tmpdir(), "champion-session-"));
    temporaryDirectories.push(directory);
    const store = new ProtectedSessionSnapshotStore(
      join(directory, "missing.enc"),
      fakeCrypto(),
    );

    await store.load();

    expect(store.getDiagnostics()).toEqual({
      encryptionAvailable: true,
      loaded: true,
      snapshotPresent: false,
      loadStatus: "READY_EMPTY",
      writeStatus: "NOT_ATTEMPTED",
      errorCode: null,
    });
  });
});

function fakeCrypto(): ProtectedSessionCrypto {
  return {
    isEncryptionAvailable: () => true,
    encryptString: (value) => Buffer.from(
      Buffer.from(value, "utf8").toString("base64").split("").reverse().join(""),
      "utf8",
    ),
    decryptString: (value) => Buffer.from(
      value.toString("utf8").split("").reverse().join(""),
      "base64",
    ).toString("utf8"),
  };
}
