import { createHash } from "node:crypto";

import { describe, expect, it, vi } from "vitest";

import { canonicalJson } from "../src/canonical-json.js";
import {
  capturedEventSchema,
  journalRecordSchema,
  type Heartbeat,
  type JournalRecord,
} from "../src/contracts.js";
import {
  HttpCollectorServer,
  type CollectorServerPort,
} from "../src/server-api.js";
import { ReliableUploader } from "../src/uploader.js";

function record(seq: number): JournalRecord {
  const event = capturedEventSchema.parse({
    kind: "CLOSE",
    eventKey: seq.toString(16).padStart(64, "0"),
    issue: "2607270001",
    sourceMs: 1000 + seq,
    receivedAtMs: 2000 + seq,
    source: "realtime",
    parserVersion: "btcffc-1",
    namespaceVersion: "actor-hmac-v1",
  });
  const digest = createHash("sha256")
    .update(canonicalJson({ seq, event }))
    .digest("hex");
  return journalRecordSchema.parse({ seq, event, digest });
}

class FakeJournal {
  acknowledgedSeq = 3;
  readonly records = [record(4), record(5), record(6)];
  readonly advances: number[] = [];

  pending(limit = 200): JournalRecord[] {
    return this.records.slice(0, limit);
  }

  async advanceAck(seq: number): Promise<void> {
    this.advances.push(seq);
    this.acknowledgedSeq = seq;
  }
}

const heartbeat: Heartbeat = {
  collector_id: "collector-main-01",
  issue: "2607270001",
  phase: "BETTING",
  countdown_ms: 900,
  observed_at_ms: 10,
  last_journal_seq: 6,
  capture_healthy: true,
};

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function serverWithAppend(
  append: CollectorServerPort["append"],
): CollectorServerPort {
  return {
    append,
    async session() {
      throw new Error("unused");
    },
    async heartbeat() {},
  };
}

describe("ReliableUploader", () => {
  it("sends pending records in order and advances only to the server ACK", async () => {
    const journal = new FakeJournal();
    let sent: number[] = [];
    const server = serverWithAppend(
      async (request) => {
        sent = request.records.map((row) => row.seq);
        return { ack_seq: 6 };
      },
    );

    const uploader = new ReliableUploader("collector-main-01", journal, server);

    await expect(uploader.tick()).resolves.toBe(6);
    expect(sent).toEqual([4, 5, 6]);
    expect(journal.advances).toEqual([6]);
  });

  it("leaves the cursor unchanged after a network error and resends", async () => {
    const journal = new FakeJournal();
    const attempts: number[][] = [];
    const server = serverWithAppend(
      async (request) => {
        attempts.push(request.records.map((row) => row.seq));
        if (attempts.length === 1) throw new Error("collector_network_error");
        return { ack_seq: 6 };
      },
    );
    const uploader = new ReliableUploader("collector-main-01", journal, server);

    await expect(uploader.tick()).rejects.toThrow("collector_network_error");
    expect(journal.acknowledgedSeq).toBe(3);
    await expect(uploader.tick()).resolves.toBe(6);
    expect(attempts).toEqual([
      [4, 5, 6],
      [4, 5, 6],
    ]);
  });

  it("rejects an ACK beyond the sent batch", async () => {
    const journal = new FakeJournal();
    const server = serverWithAppend(async () => ({ ack_seq: 7 }));
    const uploader = new ReliableUploader("collector-main-01", journal, server);

    await expect(uploader.tick()).rejects.toThrow("collector_ack_invalid");
    expect(journal.acknowledgedSeq).toBe(3);
  });

  it("never rewinds for a lower ACK", async () => {
    const journal = new FakeJournal();
    const server = serverWithAppend(async () => ({ ack_seq: 2 }));
    const uploader = new ReliableUploader("collector-main-01", journal, server);

    await expect(uploader.tick()).resolves.toBe(3);
    expect(journal.advances).toEqual([]);
  });

  it("uses bounded deterministic retry delays and resets after success", async () => {
    const journal = new FakeJournal();
    let attempts = 0;
    const server = serverWithAppend(async () => {
      attempts += 1;
      if (attempts < 3) throw new Error("collector_network_error");
      return { ack_seq: 6 };
    });
    const controller = new AbortController();
    const delays: number[] = [];
    const uploader = new ReliableUploader(
      "collector-main-01",
      journal,
      server,
      async (milliseconds) => {
        delays.push(milliseconds);
        if (delays.length === 3) controller.abort();
      },
    );

    await uploader.run(controller.signal);

    expect(delays).toEqual([250, 500, 0]);
    expect(journal.acknowledgedSeq).toBe(6);
  });

  it("runs heartbeat health independently from event delivery", async () => {
    const journal = new FakeJournal();
    let attempts = 0;
    const server = serverWithAppend(async () => ({ ack_seq: 6 }));
    server.heartbeat = async () => {
      attempts += 1;
      if (attempts === 1) throw new Error("collector_network_error");
    };
    const controller = new AbortController();
    const delays: number[] = [];
    const uploader = new ReliableUploader(
      "collector-main-01",
      journal,
      server,
      async (milliseconds) => {
        delays.push(milliseconds);
        if (delays.length === 2) controller.abort();
      },
    );

    await uploader.runHeartbeats(controller.signal, () => heartbeat);

    expect(delays).toEqual([250, 250]);
    expect(uploader.heartbeatHealthy).toBe(true);
    expect(journal.acknowledgedSeq).toBe(3);
  });
});

describe("HttpCollectorServer", () => {
  it("sends a minimal heartbeat body and accepts 204 without JSON decoding", async () => {
    let body = "";
    const response = new Response(null, { status: 204 });
    const json = vi.spyOn(response, "json");
    const fetchImpl = (async (_url: URL, init?: RequestInit) => {
      body = String(init?.body ?? "");
      return response;
    }) as typeof fetch;
    const server = new HttpCollectorServer(
      "https://collector.test/",
      `synthetic_${"x".repeat(48)}`,
      fetchImpl,
    );

    await server.heartbeat(heartbeat);

    expect(JSON.parse(body)).toEqual(heartbeat);
    expect(body).not.toMatch(/actorKey|eventKey|token|cookie|rawRequest/i);
    expect(json).not.toHaveBeenCalled();
  });

  it.each([
    [401, "collector_auth_rejected"],
    [403, "collector_auth_rejected"],
    [409, "collector_sequence_conflict"],
    [500, "collector_server_error"],
  ])("maps status %i to a safe error", async (status, code) => {
    const server = new HttpCollectorServer(
      "https://collector.test/",
      `synthetic_${"x".repeat(48)}`,
      (async () => new Response("PRIVATE_RESPONSE", { status })) as typeof fetch,
    );

    await expect(
      server.session({
        collector_id: "collector-main-01",
        namespace_version: "actor-hmac-v1",
      }),
    ).rejects.toThrow(code);
  });

  it("maps transport failure without exposing its message", async () => {
    const server = new HttpCollectorServer(
      "https://collector.test/",
      `synthetic_${"x".repeat(48)}`,
      (async () => {
        throw new Error("PRIVATE_NETWORK_DETAIL");
      }) as typeof fetch,
    );

    await expect(
      server.session({
        collector_id: "collector-main-01",
        namespace_version: "actor-hmac-v1",
      }),
    ).rejects.toThrow("collector_network_error");
  });

  it("puts a fixed timeout signal on every HTTP request", async () => {
    let suppliedSignal: AbortSignal | null | undefined;
    const server = new HttpCollectorServer(
      "https://collector.test/",
      `synthetic_${"x".repeat(48)}`,
      (async (_url: URL, init?: RequestInit) => {
        suppliedSignal = init?.signal;
        throw new Error("synthetic timeout probe");
      }) as typeof fetch,
    );

    await expect(
      server.session({
        collector_id: "collector-main-01",
        namespace_version: "actor-hmac-v1",
      }),
    ).rejects.toThrow("collector_network_error");
    expect(suppliedSignal).toBeInstanceOf(AbortSignal);
  });

  it.each([
    "short",
    `${"a".repeat(64)}:unsupported`,
    "界".repeat(64),
    "a".repeat(81),
  ])("rejects malformed session event keys", async (eventKey) => {
    const server = new HttpCollectorServer(
      "https://collector.test/",
      `synthetic_${"x".repeat(48)}`,
      (async () =>
        jsonResponse({
          ack_seq: 1,
          ack_event_key: eventKey,
          history_anchor_event_key: eventKey,
          namespace_empty: false,
        })) as typeof fetch,
    );

    await expect(
      server.session({
        collector_id: "collector-main-01",
        namespace_version: "actor-hmac-v1",
      }),
    ).rejects.toThrow("collector_server_error");
  });

  it("rejects extra session response fields", async () => {
    const server = new HttpCollectorServer(
      "https://collector.test/",
      `synthetic_${"x".repeat(48)}`,
      (async () =>
        jsonResponse({
          ack_seq: 0,
          ack_event_key: null,
          history_anchor_event_key: null,
          namespace_empty: true,
          private_field: "PRIVATE",
        })) as typeof fetch,
    );

    await expect(
      server.session({
        collector_id: "collector-main-01",
        namespace_version: "actor-hmac-v1",
      }),
    ).rejects.toThrow("collector_server_error");
  });

  it.each([
    {
      ack_seq: 1,
      ack_event_key: null,
      history_anchor_event_key: null,
      namespace_empty: true,
    },
    {
      ack_seq: 0,
      ack_event_key: "a".repeat(64),
      history_anchor_event_key: null,
      namespace_empty: true,
    },
    {
      ack_seq: 0,
      ack_event_key: null,
      history_anchor_event_key: null,
      namespace_empty: false,
    },
    {
      ack_seq: 0,
      ack_event_key: null,
      history_anchor_event_key: "a".repeat(64),
      namespace_empty: true,
    },
  ])("rejects a contradictory session tuple", async (payload) => {
    const server = new HttpCollectorServer(
      "https://collector.test/",
      `synthetic_${"x".repeat(48)}`,
      (async () => jsonResponse(payload)) as typeof fetch,
    );

    await expect(
      server.session({
        collector_id: "collector-main-01",
        namespace_version: "actor-hmac-v1",
      }),
    ).rejects.toThrow("collector_server_error");
  });

  it.each([-1, 1.5, Number.MAX_SAFE_INTEGER + 1])(
    "rejects unsafe session ACK %s",
    async (ackSeq) => {
      const server = new HttpCollectorServer(
        "https://collector.test/",
        `synthetic_${"x".repeat(48)}`,
        (async () =>
          jsonResponse({
            ack_seq: ackSeq,
            ack_event_key: null,
            history_anchor_event_key: null,
            namespace_empty: true,
          })) as typeof fetch,
      );

      await expect(
        server.session({
          collector_id: "collector-main-01",
          namespace_version: "actor-hmac-v1",
        }),
      ).rejects.toThrow("collector_server_error");
    },
  );

  it("maps invalid success JSON to a server error", async () => {
    const server = new HttpCollectorServer(
      "https://collector.test/",
      `synthetic_${"x".repeat(48)}`,
      (async () => new Response("not-json", { status: 200 })) as typeof fetch,
    );

    await expect(
      server.session({
        collector_id: "collector-main-01",
        namespace_version: "actor-hmac-v1",
      }),
    ).rejects.toThrow("collector_server_error");
  });
});
