import { describe, expect, it } from "vitest";

import {
  capturedEventSchema,
  eventBatchSchema,
  heartbeatSchema,
} from "../src/contracts.js";

const actorKey = "a".repeat(64);
const eventKey = "b".repeat(64);

describe("collector contracts", () => {
  it("accepts a canonical bet and rejects private fields", () => {
    const bet = capturedEventSchema.parse({
      kind: "BET",
      eventKey,
      actorKey,
      issue: "2607270001",
      play: "P1:大",
      amountMinor: "100",
      sourceMs: 1,
      receivedAtMs: 2,
      source: "realtime",
      parserVersion: "btcffc-1",
      namespaceVersion: "actor-hmac-v1",
    });

    expect(bet.kind).toBe("BET");
    expect(() =>
      capturedEventSchema.parse({ ...bet, nickname: "forbidden" }),
    ).toThrow();
  });

  it("requires contiguous batch bounds and a strict heartbeat", () => {
    const event = capturedEventSchema.parse({
      kind: "CLOSE",
      eventKey,
      issue: "2607270001",
      sourceMs: 3,
      receivedAtMs: 4,
      source: "realtime",
      parserVersion: "btcffc-1",
      namespaceVersion: "actor-hmac-v1",
    });

    expect(
      eventBatchSchema.parse({
        collector_id: "collector-main-01",
        namespace_version: "actor-hmac-v1",
        from_seq: 7,
        to_seq: 7,
        records: [{ seq: 7, event, digest: "c".repeat(64) }],
      }).to_seq,
    ).toBe(7);
    expect(
      heartbeatSchema.parse({
        collector_id: "collector-main-01",
        issue: "2607270001",
        phase: "BETTING",
        countdown_ms: 900,
        observed_at_ms: 10,
        last_journal_seq: 7,
        capture_healthy: true,
      }).capture_healthy,
    ).toBe(true);
  });

  it("keeps final issue status completeness consistent with its reasons", () => {
    const common = {
      kind: "ISSUE_STATUS",
      eventKey,
      issue: "2607270001",
      sourceMs: 3,
      receivedAtMs: 4,
      source: "realtime",
      parserVersion: "btcffc-1",
      namespaceVersion: "actor-hmac-v1",
    };

    expect(() =>
      capturedEventSchema.parse({
        ...common,
        complete: true,
        reasons: ["result_missing"],
      }),
    ).toThrow();
    expect(() =>
      capturedEventSchema.parse({
        ...common,
        complete: false,
        reasons: [],
      }),
    ).toThrow();
  });
});
