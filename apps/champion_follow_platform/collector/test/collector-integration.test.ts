import { createHash } from "node:crypto";
import {
  chmod,
  lstat,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Readable } from "node:stream";

import { afterEach, describe, expect, it, vi } from "vitest";

import { CollectorCredentialStore } from "../src/credential-store.js";
import { capturedEventSchema, type CapturedEvent, type JournalRecord } from "../src/contracts.js";
import { AppendOnlyJournal } from "../src/journal.js";
import { CollectorRuntime } from "../src/runtime.js";
import { HttpCollectorServer, type CollectorServerPort } from "../src/server-api.js";

const ACTOR = "a".repeat(64);
const RAW_MARKERS = ["raw-player-marker", "raw-message-marker"] as const;
let eventNumber = 0;

function event(
  issue: string,
  kind: CapturedEvent["kind"],
  changes: Record<string, unknown> = {},
): CapturedEvent {
  eventNumber += 1;
  const base: Record<string, unknown> = {
    kind,
    eventKey: createHash("sha256")
      .update(`collector-integration:${issue}:${eventNumber}:${kind}`)
      .digest("hex"),
    issue,
    sourceMs: 10_000 + eventNumber,
    receivedAtMs: 20_000 + eventNumber,
    source: "realtime",
    parserVersion: "btcffc-1",
    namespaceVersion: "actor-hmac-v1",
  };
  if (kind === "BET" || kind === "CANCEL") {
    Object.assign(base, {
      actorKey: ACTOR,
      play: "P1:大",
      amountMinor: "100",
    });
  }
  if (kind === "RESULT") Object.assign(base, { digits: [3, 8, 4, 6, 0] });
  return capturedEventSchema.parse({ ...base, ...changes });
}

class RecordingServer implements CollectorServerPort {
  appendAttempts = 0;
  readonly stored = new Map<number, JournalRecord>();

  constructor(private failuresRemaining = 0) {}

  async session() {
    return {
      ack_seq: 0,
      ack_event_key: null,
      history_anchor_event_key: null,
      namespace_empty: true,
    } as const;
  }

  async append(request: Parameters<CollectorServerPort["append"]>[0]) {
    this.appendAttempts += 1;
    if (this.failuresRemaining > 0) {
      this.failuresRemaining -= 1;
      throw new Error("collector_network_error");
    }
    for (const row of request.records) {
      const prior = this.stored.get(row.seq);
      if (prior && prior.digest !== row.digest) {
        throw new Error("collector_sequence_conflict");
      }
      this.stored.set(row.seq, row);
    }
    return { ack_seq: request.to_seq };
  }

  async heartbeat() {}

  records(): JournalRecord[] {
    return [...this.stored.values()].sort((left, right) => left.seq - right.seq);
  }
}

const cleanupRoots: string[] = [];

async function runtimeFixture(issue: string, failures = 0) {
  const root = await mkdtemp(join(tmpdir(), "collector-e2e-"));
  cleanupRoots.push(root);
  const journal = new AppendOnlyJournal(root);
  await journal.start();
  const server = new RecordingServer(failures);
  const runtime = new CollectorRuntime({
    collectorId: "collector-main-01",
    journal,
    server,
  });
  await runtime.observeBettingBoundary(event(issue, "CLOSE"));
  runtime.markHistoryAnchorRecovered(issue);
  return { root, journal, server, runtime };
}

afterEach(async () => {
  vi.restoreAllMocks();
  while (cleanupRoots.length) {
    await rm(cleanupRoots.pop()!, { recursive: true, force: true });
  }
});

describe("collector integration", () => {
  it("replays one complete net prediction exactly once after outage and restart", async () => {
    const issue = "2607270101";
    const first = await runtimeFixture(issue, 2);
    await first.runtime.ingest([
      event(issue, "BET"),
      event(issue, "CANCEL"),
      event(issue, "BET", { play: "P1:小" }),
      event(issue, "CLOSE"),
      event(issue, "RESULT"),
    ]);
    expect(first.runtime.completeness(issue)).toEqual({ complete: true, reasons: [] });
    await expect(first.runtime.uploadOnce()).rejects.toThrow("collector_network_error");
    await expect(first.runtime.uploadOnce()).rejects.toThrow("collector_network_error");
    expect(first.journal.acknowledgedSeq).toBe(0);
    const expectedLastSequence = first.journal.lastSeq;
    await first.journal.close();

    const reopened = new AppendOnlyJournal(first.root);
    await reopened.start();
    const restarted = new CollectorRuntime({
      collectorId: "collector-main-01",
      journal: reopened,
      server: first.server,
    });
    await expect(restarted.uploadOnce()).resolves.toBe(expectedLastSequence);
    expect(reopened.acknowledgedSeq).toBe(expectedLastSequence);

    const stored = first.server.records();
    expect(stored.map((row) => row.seq)).toEqual(
      Array.from({ length: expectedLastSequence }, (_, index) => index + 1),
    );
    const money = stored.filter(
      (row) => row.event.kind === "BET" || row.event.kind === "CANCEL",
    );
    const net = new Map<string, bigint>();
    for (const row of money) {
      if (row.event.kind !== "BET" && row.event.kind !== "CANCEL") continue;
      const prior = net.get(row.event.play) ?? 0n;
      const amount = BigInt(row.event.amountMinor);
      net.set(row.event.play, row.event.kind === "BET" ? prior + amount : prior - amount);
    }
    expect(net.get("P1:大")).toBe(0n);
    expect(net.get("P1:小")).toBe(100n);
    expect(
      stored.some(
        (row) => row.event.kind === "ISSUE_STATUS" && row.event.complete,
      ),
    ).toBe(true);

    const serialized =
      (await readFile(join(first.root, "events.ndjson"), "utf8")) +
      (await readFile(join(first.root, "cursor.json"), "utf8")) +
      JSON.stringify(stored);
    for (const marker of RAW_MARKERS) expect(serialized).not.toContain(marker);
    await reopened.close();
  });

  it("replays an unattributed cancellation but never synthesizes an actor cancel", async () => {
    const issue = "2607270102";
    const fixture = await runtimeFixture(issue);
    await fixture.runtime.ingest([
      event(issue, "CANCEL_UNATTRIBUTED"),
      event(issue, "CLOSE"),
      event(issue, "RESULT"),
    ]);

    expect(fixture.runtime.completeness(issue)).toEqual({
      complete: false,
      reasons: ["unattributed_cancel"],
    });
    await fixture.runtime.uploadOnce();
    const events = fixture.server.records().map((row) => row.event);
    expect(events.filter((value) => value.kind === "CANCEL")).toHaveLength(0);
    expect(events).toContainEqual(expect.objectContaining({ kind: "CANCEL_UNATTRIBUTED" }));
    expect(events).toContainEqual(
      expect.objectContaining({
        kind: "ISSUE_STATUS",
        complete: false,
        reasons: ["unattributed_cancel"],
      }),
    );
    await fixture.journal.close();
  });

  it("imports a credential before networking and leaks it to no durable artifact", async () => {
    const root = await mkdtemp(join(tmpdir(), "collector-credential-e2e-"));
    cleanupRoots.push(root);
    const marker = `synthetic_fixture_${"z".repeat(48)}`;
    const bundle = {
      format: "champion-collector-credential-v1" as const,
      collector_id: "collector-main-01",
      bearer: marker,
    };
    const encryption = {
      isEncryptionAvailable: () => true,
      encryptString: (value: string) => Buffer.from(value, "utf8").reverse(),
      decryptString: (value: Buffer) => Buffer.from(value).reverse().toString("utf8"),
    };
    const target = join(root, "collector-credential.enc");
    const store = new CollectorCredentialStore(target, encryption);
    let imported;
    if (process.platform === "win32") {
      imported = await store.importFromStdin(Readable.from([JSON.stringify(bundle)]));
    } else {
      const source = join(root, "handoff.json");
      await writeFile(source, JSON.stringify(bundle), { mode: 0o600 });
      await chmod(source, 0o600);
      imported = await store.importFromFile(source);
      await expect(lstat(source)).rejects.toMatchObject({ code: "ENOENT" });
    }

    let authorizationMatched = false;
    const bodies: string[] = [];
    const capturedConsole: string[] = [];
    vi.spyOn(console, "log").mockImplementation((...values) => {
      capturedConsole.push(values.join(" "));
    });
    vi.spyOn(console, "error").mockImplementation((...values) => {
      capturedConsole.push(values.join(" "));
    });

    const server = new HttpCollectorServer(
      "https://collector.test/",
      imported.bearer,
      (async (_url: URL, init?: RequestInit) => {
        authorizationMatched =
          new Headers(init?.headers).get("authorization") === `Bearer ${marker}`;
        bodies.push(String(init?.body ?? ""));
        return new Response(
          JSON.stringify({
            ack_seq: 0,
            ack_event_key: null,
            history_anchor_event_key: null,
            namespace_empty: true,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }) as typeof fetch,
    );
    await server.session({
      collector_id: imported.collector_id,
      namespace_version: "actor-hmac-v1",
    });

    const journalRoot = join(root, "journal");
    const journal = new AppendOnlyJournal(journalRoot);
    await journal.start();
    await journal.close();
    const serialized =
      (await readFile(target, "utf8")) +
      (await readFile(join(journalRoot, "events.ndjson"), "utf8")) +
      bodies.join("\n") +
      capturedConsole.join("\n");
    expect(authorizationMatched).toBe(true);
    expect(serialized).not.toContain(marker);
  });
});
