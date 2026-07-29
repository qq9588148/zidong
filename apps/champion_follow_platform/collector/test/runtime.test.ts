import { createHash } from "node:crypto";

import { describe, expect, it } from "vitest";

import { canonicalJson } from "../src/canonical-json.js";
import { IssueCompletenessTracker } from "../src/completeness.js";
import {
  capturedEventSchema,
  journalRecordSchema,
  type CapturedEvent,
  type Heartbeat,
  type JournalRecord,
} from "../src/contracts.js";
import {
  CollectorRuntime,
  bootstrapCollector,
  type CollectorBootstrapSteps,
  type HistoryPage,
  type RuntimeJournalPort,
} from "../src/runtime.js";
import type {
  CollectorServerPort,
  CollectorSessionValue,
} from "../src/server-api.js";

const ISSUE = "2607270001";
const ACTOR = "a".repeat(64);
let marker = 0;

function event(
  kind: CapturedEvent["kind"],
  changes: Record<string, unknown> = {},
): CapturedEvent {
  marker += 1;
  const base: Record<string, unknown> = {
    kind,
    eventKey: marker.toString(16).padStart(64, "0"),
    issue: ISSUE,
    sourceMs: 1_000 + marker,
    receivedAtMs: 2_000 + marker,
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
  } else if (kind === "RESULT") {
    Object.assign(base, { digits: [1, 2, 3, 4, 5] });
  } else if (kind === "CAPTURE_GAP") {
    Object.assign(base, { reason: "decrypt_failure" });
  }
  return capturedEventSchema.parse({ ...base, ...changes });
}

function record(seq: number, value: CapturedEvent): JournalRecord {
  return journalRecordSchema.parse({
    seq,
    event: value,
    digest: createHash("sha256")
      .update(canonicalJson({ seq, event: value }))
      .digest("hex"),
  });
}

class MemoryJournal implements RuntimeJournalPort {
  repairedTail = false;
  acknowledgedSeq = 0;
  acknowledgedEventKey: string | null = null;
  rows: JournalRecord[] = [];
  beforeAppend: (() => Promise<void>) | null = null;
  appendError: Error | null = null;
  advanceError: Error | null = null;

  get lastSeq(): number {
    return this.rows.at(-1)?.seq ?? this.acknowledgedSeq;
  }

  async append(value: CapturedEvent): Promise<JournalRecord> {
    await this.beforeAppend?.();
    if (this.appendError) throw this.appendError;
    const row = record(this.lastSeq + 1, capturedEventSchema.parse(value));
    this.rows.push(row);
    return row;
  }

  pending(limit = Number.MAX_SAFE_INTEGER): JournalRecord[] {
    return this.rows
      .filter((row) => row.seq > this.acknowledgedSeq)
      .slice(0, limit);
  }

  replay(): JournalRecord[] {
    return [...this.rows];
  }

  async advanceAck(seq: number): Promise<void> {
    if (this.advanceError) throw this.advanceError;
    const row = this.rows.find((candidate) => candidate.seq === seq);
    if (!row) throw new Error("journal_ack_invalid");
    this.acknowledgedSeq = seq;
    this.acknowledgedEventKey = row.event.eventKey;
  }
}

function session(
  changes: Partial<CollectorSessionValue> = {},
): CollectorSessionValue {
  return {
    ack_seq: 0,
    ack_event_key: null,
    history_anchor_event_key: null,
    namespace_empty: true,
    ...changes,
  };
}

function server(
  sessionValue: CollectorSessionValue = session(),
): CollectorServerPort & { sent: number[][] } {
  return {
    sent: [],
    async session() {
      return sessionValue;
    },
    async append(request) {
      this.sent.push(request.records.map((row) => row.seq));
      return { ack_seq: request.to_seq };
    },
    async heartbeat(_value: Heartbeat) {},
  };
}

function runtime(
  journal = new MemoryJournal(),
  serverPort = server(),
  stopped: string[] = [],
): CollectorRuntime {
  return new CollectorRuntime({
    collectorId: "collector-main-01",
    journal,
    server: serverPort,
    tracker: new IssueCompletenessTracker(),
    stopCapture: (code) => stopped.push(code),
  });
}

function historyPage(
  events: CapturedEvent[],
  changes: Partial<Omit<HistoryPage, "events">> = {},
): HistoryPage {
  return {
    events,
    crossedUncertainBoundary: false,
    uncertainBoundarySourceMs: null,
    ...changes,
  };
}

describe("CollectorRuntime durability boundary", () => {
  it("acknowledges renderer capture only after every journal append is durable", async () => {
    const journal = new MemoryJournal();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    journal.beforeAppend = () => gate;
    const pending = runtime(journal).ingest([event("BET")]);
    let settled = false;
    void pending.then(() => {
      settled = true;
    });

    await Promise.resolve();
    expect(settled).toBe(false);
    release();

    await expect(pending).resolves.toBe(1);
    expect(journal.rows.map((row) => row.event.kind)).toEqual(["BET"]);
  });

  it("fails closed permanently after a local append failure", async () => {
    const journal = new MemoryJournal();
    const stopped: string[] = [];
    journal.appendError = new Error("PRIVATE_DISK_DETAIL");
    const collector = runtime(journal, server(), stopped);

    await expect(collector.ingest([event("BET")])).rejects.toThrow(
      "journal_write_failed",
    );
    expect(stopped).toEqual(["journal_write_failed"]);
    expect(
      collector.currentHeartbeat({
        issue: ISSUE,
        phase: "BETTING",
        countdownMs: 900,
        observedAtMs: 3_000,
      }).capture_healthy,
    ).toBe(false);
    journal.appendError = null;
    await expect(collector.ingest([event("BET")])).rejects.toThrow(
      "journal_write_failed",
    );
    expect(journal.rows).toEqual([]);
  });

  it("keeps local capture available while the server is offline", async () => {
    const journal = new MemoryJournal();
    const offline = server();
    offline.append = async () => {
      throw new Error("collector_network_error");
    };
    const collector = runtime(journal, offline);

    await collector.ingest([event("BET")]);
    await expect(collector.uploadOnce()).rejects.toThrow(
      "collector_network_error",
    );
    await expect(collector.ingest([event("BET")])).resolves.toBeGreaterThan(0);
  });

  it("fails closed when an upload ACK cannot update the local cursor", async () => {
    const journal = new MemoryJournal();
    const collector = runtime(journal, server());
    await collector.ingest([event("BET")]);
    journal.advanceError = new Error("PRIVATE_CURSOR_DETAIL");

    await expect(collector.uploadOnce()).rejects.toThrow(
      "journal_write_failed",
    );
    expect(collector.currentHeartbeat().capture_healthy).toBe(false);
  });

  it("writes a repaired-tail gap before accepting the next boundary event", async () => {
    const journal = new MemoryJournal();
    journal.repairedTail = true;
    const collector = runtime(journal);
    const next = event("BET");

    await collector.observeBettingBoundary(next);
    await collector.ingest([next]);

    expect(journal.rows[0]?.event).toMatchObject({
      kind: "CAPTURE_GAP",
      issue: ISSUE,
      reason: "journal_torn_tail",
    });
    expect(journal.rows[0]?.event.eventKey).toMatch(/^[0-9a-f]{64}$/);
    expect(
      journal.rows.findIndex((row) => row.event.eventKey === next.eventKey),
    ).toBeGreaterThan(0);
  });

  it("does not emit a complete status before all four boundaries", async () => {
    const journal = new MemoryJournal();
    const collector = runtime(journal);
    const bet = event("BET");
    await collector.observeBettingBoundary(bet);
    collector.markHistoryAnchorRecovered(ISSUE);
    await collector.ingest([bet, event("CLOSE")]);

    expect(
      journal.rows.some(
        (row) =>
          row.event.kind === "ISSUE_STATUS" && row.event.complete === true,
      ),
    ).toBe(false);

    await collector.ingest([event("RESULT")]);
    expect(journal.rows.at(-1)?.event).toMatchObject({
      kind: "ISSUE_STATUS",
      complete: true,
      reasons: [],
    });
  });

  it("publishes one final incomplete status only after close and result", async () => {
    const journal = new MemoryJournal();
    const collector = runtime(journal);
    const bet = event("BET");
    await collector.observeBettingBoundary(bet);
    collector.markHistoryAnchorRecovered(ISSUE);
    await collector.ingest([event("CANCEL_UNATTRIBUTED"), event("CLOSE")]);

    expect(
      journal.rows.some((row) => row.event.kind === "ISSUE_STATUS"),
    ).toBe(false);

    await collector.ingest([event("RESULT")]);
    const statuses = journal.rows.filter(
      (row) => row.event.kind === "ISSUE_STATUS",
    );
    expect(statuses).toHaveLength(1);
    expect(statuses[0]?.event).toMatchObject({
      complete: false,
      reasons: ["unattributed_cancel"],
    });
  });

  it("deduplicates a history/live overlap by event key", async () => {
    const journal = new MemoryJournal();
    const collector = runtime(journal);
    const history = event("BET", { source: "history" });
    const live = capturedEventSchema.parse({ ...history, source: "realtime" });

    const first = await collector.ingest([history]);
    const second = await collector.ingest([live]);

    expect(second).toBe(first);
    expect(
      journal.rows.filter((row) => row.event.eventKey === history.eventKey),
    ).toHaveLength(1);
  });

  it("deduplicates one result observed at different DOM and SDK times", async () => {
    const journal = new MemoryJournal();
    const collector = runtime(journal);
    const history = event("RESULT", { source: "history", sourceMs: 1_000 });
    const live = capturedEventSchema.parse({
      ...history,
      source: "realtime",
      sourceMs: 2_000,
    });

    await collector.ingest([history]);
    await collector.ingest([live]);

    expect(
      journal.rows.filter((row) => row.event.eventKey === history.eventKey),
    ).toHaveLength(1);
  });

  it("fails closed when one event key is reused for different semantics", async () => {
    const journal = new MemoryJournal();
    const collector = runtime(journal);
    const first = event("BET");
    const conflicting = capturedEventSchema.parse({
      ...first,
      amountMinor: "200",
    });

    await collector.ingest([first]);
    await expect(collector.ingest([conflicting])).rejects.toThrow(
      "collector_event_conflict",
    );
    expect(collector.currentHeartbeat().capture_healthy).toBe(false);
  });
});

describe("CollectorRuntime recovery", () => {
  it("waits for in-flight durable ingestion before applying reconnect state", async () => {
    const journal = new MemoryJournal();
    const collector = runtime(journal, server(session()));
    await collector.reconcileSession();

    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    journal.beforeAppend = () => gate;
    const ingesting = collector.ingest([event("BET")]);
    let reconciled = false;
    const reconnecting = collector.reconcileSession().then(() => {
      reconciled = true;
    });

    await Promise.resolve();
    await Promise.resolve();
    expect(reconciled).toBe(false);

    release();
    await ingesting;
    await reconnecting;
    expect(journal.rows).toHaveLength(1);
  });

  it("replays retained journal semantics before accepting post-restart events", async () => {
    const journal = new MemoryJournal();
    const prior = event("BET", { play: "P1:大" });
    journal.rows = [record(1, prior)];
    const collector = runtime(journal);
    const boundary = event("BET", { play: "P2:大" });
    await collector.observeBettingBoundary(boundary);
    collector.markHistoryAnchorRecovered(ISSUE);

    await collector.ingest([
      event("BET", { play: "P1:小" }),
      event("CLOSE"),
      event("RESULT"),
    ]);

    expect(collector.completeness(ISSUE).reasons).toContain(
      "opposite_net_conflict",
    );
  });

  it("turns an explicit page-state transition into one betting boundary and one close", async () => {
    const journal = new MemoryJournal();
    const collector = runtime(journal);

    await collector.observePageState({
      issue: ISSUE,
      phase: "BETTING",
      countdownMs: 5_000,
      observedAtMs: 3_000,
    });
    collector.markHistoryAnchorRecovered(ISSUE);
    await collector.observePageState({
      issue: ISSUE,
      phase: "CLOSED",
      countdownMs: 0,
      observedAtMs: 8_000,
    });
    await collector.observePageState({
      issue: ISSUE,
      phase: "CLOSED",
      countdownMs: 0,
      observedAtMs: 8_250,
    });

    expect(
      journal.rows.filter((row) => row.event.kind === "CLOSE"),
    ).toHaveLength(1);
    expect(collector.currentHeartbeat().phase).toBe("CLOSED");
    expect(collector.completeness(ISSUE).reasons).toContain("result_missing");
  });

  it("closes the prior issue when the page advances directly to the next betting issue", async () => {
    const journal = new MemoryJournal();
    const collector = runtime(journal);

    await collector.observePageState({
      issue: ISSUE,
      phase: "BETTING",
      countdownMs: 5_000,
      observedAtMs: 3_000,
    });
    collector.markHistoryAnchorRecovered(ISSUE);
    await collector.ingest([event("RESULT")]);

    await collector.observePageState({
      issue: "2607270002",
      phase: "BETTING",
      countdownMs: 5_000,
      observedAtMs: 8_000,
    });

    expect(
      journal.rows.filter(
        (row) => row.event.kind === "CLOSE" && row.event.issue === ISSUE,
      ),
    ).toHaveLength(1);
    expect(
      journal.rows.filter(
        (row) => row.event.kind === "ISSUE_STATUS" && row.event.issue === ISSUE,
      ),
    ).toEqual([
      expect.objectContaining({
        event: expect.objectContaining({ complete: true, reasons: [] }),
      }),
    ]);
    expect(collector.currentHeartbeat()).toMatchObject({
      issue: "2607270002",
      phase: "BETTING",
    });
  });

  it("stops retrying history after the observed issue closes", async () => {
    const collector = runtime();
    await collector.observePageState({
      issue: ISSUE,
      phase: "BETTING",
      countdownMs: 5_000,
      observedAtMs: 3_000,
    });
    expect(collector.historyRecoveryOpen()).toBe(true);

    await collector.observePageState({
      issue: ISSUE,
      phase: "CLOSED",
      countdownMs: 0,
      observedAtMs: 8_000,
    });
    expect(collector.historyRecoveryOpen()).toBe(false);
  });

  it("starts local live collection by marking the partial opening issue incomplete", async () => {
    const journal = new MemoryJournal();
    const collector = runtime(journal, server(session()));
    await collector.reconcileSession();
    await collector.observePageState({
      issue: ISSUE,
      phase: "BETTING",
      countdownMs: 5_000,
      observedAtMs: 3_000,
    });

    await collector.startLiveCollectionWithoutHistory();

    expect(collector.completeness(ISSUE).reasons).toContain(
      "history_anchor_missing",
    );
    expect(
      journal.rows.some(
        (row) =>
          row.event.kind === "CAPTURE_GAP" &&
          row.event.reason === "history_anchor_missing",
      ),
    ).toBe(true);
  });

  it("abandons an incompatible history source and resumes at the next boundary", async () => {
    const journal = new MemoryJournal();
    const collector = runtime(
      journal,
      server(session({
        history_anchor_event_key: "f".repeat(64),
        namespace_empty: false,
      })),
    );
    await collector.reconcileSession();
    await collector.observePageState({
      issue: ISSUE,
      phase: "BETTING",
      countdownMs: 5_000,
      observedAtMs: 3_000,
    });

    await collector.abandonHistoryRecovery();

    expect(collector.completeness(ISSUE).reasons).toContain(
      "history_anchor_missing",
    );
    const nextIssue = "2607270002";
    await collector.observePageState({
      issue: nextIssue,
      phase: "BETTING",
      countdownMs: 5_000,
      observedAtMs: 8_000,
    });
    await collector.ingest([event("RESULT", { issue: nextIssue })]);
    await collector.observePageState({
      issue: "2607270003",
      phase: "BETTING",
      countdownMs: 5_000,
      observedAtMs: 13_000,
    });
    expect(collector.completeness(nextIssue)).toEqual({
      complete: true,
      reasons: [],
    });
  });

  it("keeps the issue incomplete when close arrives before a delayed history anchor", async () => {
    const anchor = event("BET", { source: "history" });
    const journal = new MemoryJournal();
    const collector = runtime(
      journal,
      server(
        session({
          history_anchor_event_key: anchor.eventKey,
          namespace_empty: false,
        }),
      ),
    );
    await collector.reconcileSession();
    await collector.observeBettingBoundary(event("BET"));

    let release!: () => void;
    const pageReady = new Promise<void>((resolve) => {
      release = resolve;
    });
    const recovering = collector.recoverHistory(async () => {
      await pageReady;
      return historyPage([anchor]);
    });
    await collector.observePageState({
      issue: ISSUE,
      phase: "CLOSED",
      countdownMs: 0,
      observedAtMs: 8_000,
    });
    await collector.ingest([event("RESULT")]);
    release();
    await recovering;

    expect(collector.completeness(ISSUE)).toEqual({
      complete: false,
      reasons: ["history_anchor_missing"],
    });
    expect(
      journal.rows.some(
        (row) =>
          row.event.kind === "CAPTURE_GAP" &&
          row.event.reason === "history_anchor_missing",
      ),
    ).toBe(true);
  });

  it("marks crossed issues incomplete and carries continuity to the open issue", async () => {
    const anchor = event("BET", { source: "history" });
    const journal = new MemoryJournal();
    const collector = runtime(
      journal,
      server(
        session({
          history_anchor_event_key: anchor.eventKey,
          namespace_empty: false,
        }),
      ),
    );
    await collector.reconcileSession();
    await collector.observePageState({
      issue: ISSUE,
      phase: "BETTING",
      countdownMs: 5_000,
      observedAtMs: 3_000,
    });

    let release!: () => void;
    const pageReady = new Promise<void>((resolve) => {
      release = resolve;
    });
    const recovering = collector.recoverHistory(async () => {
      await pageReady;
      return historyPage([anchor]);
    });
    await collector.ingest([event("RESULT")]);
    const nextIssue = "2607270002";
    await collector.observePageState({
      issue: nextIssue,
      phase: "BETTING",
      countdownMs: 5_000,
      observedAtMs: 8_000,
    });
    release();
    await recovering;

    expect(collector.completeness(ISSUE).reasons).toContain(
      "history_anchor_missing",
    );
    expect(
      journal.rows.find(
        (row) => row.event.kind === "ISSUE_STATUS" && row.event.issue === ISSUE,
      )?.event,
    ).toMatchObject({ complete: false });

    await collector.ingest([event("RESULT", { issue: nextIssue })]);
    await collector.observePageState({
      issue: "2607270003",
      phase: "BETTING",
      countdownMs: 5_000,
      observedAtMs: 13_000,
    });
    expect(collector.completeness(nextIssue)).toEqual({
      complete: true,
      reasons: [],
    });
  });

  it("resumes upload after the persisted ACK without resending it", async () => {
    const journal = new MemoryJournal();
    journal.rows = [record(1, event("CLOSE")), record(2, event("RESULT"))];
    journal.acknowledgedSeq = 1;
    journal.acknowledgedEventKey = journal.rows[0]!.event.eventKey;
    const remote = server(
      session({
        ack_seq: 1,
        ack_event_key: journal.acknowledgedEventKey,
        namespace_empty: false,
      }),
    );
    const collector = runtime(journal, remote);

    await collector.reconcileSession();
    await collector.uploadOnce();

    expect(remote.sent).toEqual([[2]]);
  });

  it("fails closed when a reconciled ACK cursor cannot be persisted", async () => {
    const journal = new MemoryJournal();
    const value = event("BET");
    journal.rows = [record(1, value)];
    journal.advanceError = new Error("PRIVATE_CURSOR_DETAIL");
    const stopped: string[] = [];
    const collector = runtime(
      journal,
      server(
        session({
          ack_seq: 1,
          ack_event_key: value.eventKey,
          history_anchor_event_key: value.eventKey,
          namespace_empty: false,
        }),
      ),
      stopped,
    );

    await expect(collector.reconcileSession()).rejects.toThrow(
      "journal_write_failed",
    );
    expect(collector.currentHeartbeat().capture_healthy).toBe(false);
    expect(stopped).toEqual(["journal_write_failed"]);
  });

  it("uses only the money history anchor, never the marker ACK", async () => {
    const journal = new MemoryJournal();
    const markerAck = event("CLOSE");
    journal.rows = [record(1, markerAck)];
    journal.acknowledgedSeq = 1;
    journal.acknowledgedEventKey = markerAck.eventKey;
    const anchor = event("BET", { source: "history" });
    const remote = server(
      session({
        ack_seq: 1,
        ack_event_key: markerAck.eventKey,
        history_anchor_event_key: anchor.eventKey,
        namespace_empty: false,
      }),
    );
    const collector = runtime(journal, remote);
    const requests: Array<{ historyAnchorEventKey: string; limit: 100 }> = [];

    await collector.reconcileSession();
    await collector.recoverHistory(async (request) => {
      requests.push(request);
      return historyPage([anchor]);
    });

    expect(requests).toEqual([
      { historyAnchorEventKey: anchor.eventKey, limit: 100 },
    ]);
    expect(requests[0]?.historyAnchorEventKey).not.toBe(markerAck.eventKey);
  });

  it("processes every non-anchor event from the page that contains the anchor", async () => {
    const journal = new MemoryJournal();
    const anchor = event("BET", { source: "history" });
    const newer = event("BET", { source: "history", play: "P2:单" });
    const remote = server(
      session({
        history_anchor_event_key: anchor.eventKey,
        namespace_empty: false,
      }),
    );
    const collector = runtime(journal, remote);

    await collector.reconcileSession();
    await collector.recoverHistory(async () => historyPage([anchor, newer]));

    expect(
      journal.rows.some((row) => row.event.eventKey === newer.eventKey),
    ).toBe(true);
  });

  it("does not accept an anchor on an uncertain same-millisecond boundary", async () => {
    const journal = new MemoryJournal();
    const anchor = event("BET", { source: "history", sourceMs: 100 });
    const newer = event("BET", {
      source: "history",
      sourceMs: 100,
      play: "P2:单",
    });
    const collector = runtime(
      journal,
      server(
        session({
          history_anchor_event_key: anchor.eventKey,
          namespace_empty: false,
        }),
      ),
    );
    await collector.reconcileSession();
    await collector.observeBettingBoundary(event("BET"));

    await collector.recoverHistory(async () =>
      historyPage([newer, anchor], { uncertainBoundarySourceMs: 100 }),
    );
    await collector.ingest([event("CLOSE"), event("RESULT")]);

    expect(collector.completeness(ISSUE)).toEqual({
      complete: false,
      reasons: ["history_anchor_missing"],
    });
  });

  it("does not accept an anchor after crossing an uncertain page boundary", async () => {
    const journal = new MemoryJournal();
    const anchor = event("BET", { source: "history", sourceMs: 100 });
    const newer = event("BET", { source: "history", sourceMs: 200 });
    const collector = runtime(
      journal,
      server(
        session({
          history_anchor_event_key: anchor.eventKey,
          namespace_empty: false,
        }),
      ),
    );
    await collector.reconcileSession();
    await collector.observeBettingBoundary(event("BET"));
    const pages = [
      historyPage([newer], { uncertainBoundarySourceMs: 200 }),
      historyPage([anchor], { crossedUncertainBoundary: true }),
    ];

    await collector.recoverHistory(async () =>
      pages.shift() ?? historyPage([]),
    );
    await collector.ingest([event("CLOSE"), event("RESULT")]);

    expect(collector.completeness(ISSUE)).toEqual({
      complete: false,
      reasons: ["history_anchor_missing"],
    });
  });

  it("rejects a history anchor that is not a money event", async () => {
    const journal = new MemoryJournal();
    const invalidAnchor = event("CLOSE", { source: "history" });
    const remote = server(
      session({
        history_anchor_event_key: invalidAnchor.eventKey,
        namespace_empty: false,
      }),
    );
    const collector = runtime(journal, remote);
    await collector.reconcileSession();

    await expect(
      collector.recoverHistory(async () => historyPage([invalidAnchor])),
    ).rejects.toThrow("collector_history_anchor_invalid");
    expect(collector.currentHeartbeat().capture_healthy).toBe(false);
  });

  it("rebuilds cancellation semantics chronologically across newest-first pages", async () => {
    const journal = new MemoryJournal();
    const anchor = event("BET", {
      source: "history",
      sourceMs: 100,
      receivedAtMs: 1_000,
      play: "P1:大",
      amountMinor: "100",
    });
    const cancel = event("CANCEL", {
      source: "history",
      sourceMs: 200,
      receivedAtMs: 1_001,
      play: "P1:大",
      amountMinor: "100",
    });
    const opposite = event("BET", {
      source: "history",
      sourceMs: 300,
      receivedAtMs: 1_002,
      play: "P1:小",
      amountMinor: "100",
    });
    const remote = server(
      session({
        history_anchor_event_key: anchor.eventKey,
        namespace_empty: false,
      }),
    );
    const collector = runtime(journal, remote);
    const boundary = event("BET", { play: "P2:大" });
    await collector.reconcileSession();
    await collector.observeBettingBoundary(boundary);
    const pages = [historyPage([opposite]), historyPage([anchor, cancel])];
    await collector.recoverHistory(async () =>
      pages.shift() ?? historyPage([]),
    );
    await collector.ingest([event("CLOSE"), event("RESULT")]);

    expect(collector.completeness(ISSUE)).toEqual({
      complete: true,
      reasons: [],
    });
  });

  it("accepts a null anchor only for an empty namespace with a fresh boundary", async () => {
    const emptyCollector = runtime(new MemoryJournal(), server(session()));
    const boundary = event("BET");
    await emptyCollector.reconcileSession();
    await emptyCollector.observeBettingBoundary(boundary);
    await expect(
      emptyCollector.recoverHistory(async () => historyPage([])),
    ).resolves.toBeUndefined();
    expect(emptyCollector.completeness(ISSUE).reasons).not.toContain(
      "history_anchor_missing",
    );

    const nonEmptyCollector = runtime(
      new MemoryJournal(),
      server(session({ namespace_empty: false })),
    );
    await nonEmptyCollector.reconcileSession();
    await nonEmptyCollector.observeBettingBoundary(event("BET"));
    await expect(
      nonEmptyCollector.recoverHistory(async () => historyPage([])),
    ).rejects.toThrow("collector_history_anchor_missing");
    expect(nonEmptyCollector.currentHeartbeat().capture_healthy).toBe(false);
  });

  it("carries proven live continuity into each later explicit betting boundary", async () => {
    const journal = new MemoryJournal();
    const collector = runtime(journal, server(session()));
    const first = event("BET");
    await collector.reconcileSession();
    await collector.observeBettingBoundary(first);
    await collector.recoverHistory(async () => historyPage([]));

    const nextIssue = "2607270002";
    await collector.observePageState({
      issue: nextIssue,
      phase: "BETTING",
      countdownMs: 5_000,
      observedAtMs: 5_000,
    });
    await collector.ingest([
      event("CLOSE", { issue: nextIssue }),
      event("RESULT", { issue: nextIssue }),
    ]);

    expect(collector.completeness(nextIssue)).toEqual({
      complete: true,
      reasons: [],
    });
  });

  it("limits a missing history anchor to the current issue after a later fresh boundary", async () => {
    const missingAnchor = "f".repeat(64);
    const collector = runtime(
      new MemoryJournal(),
      server(
        session({
          history_anchor_event_key: missingAnchor,
          namespace_empty: false,
        }),
      ),
    );
    await collector.reconcileSession();
    await collector.observeBettingBoundary(event("BET"));
    await collector.recoverHistory(async () => historyPage([]));
    expect(collector.completeness(ISSUE).reasons).toContain(
      "history_anchor_missing",
    );

    const nextIssue = "2607270002";
    await collector.observePageState({
      issue: nextIssue,
      phase: "BETTING",
      countdownMs: 5_000,
      observedAtMs: 5_000,
    });
    await collector.ingest([
      event("CLOSE", { issue: nextIssue }),
      event("RESULT", { issue: nextIssue }),
    ]);

    expect(collector.completeness(nextIssue)).toEqual({
      complete: true,
      reasons: [],
    });
  });

  it("suspends continuity on reconnect until the new history proof completes", async () => {
    const journal = new MemoryJournal();
    const collector = runtime(journal, server(session()));
    await collector.reconcileSession();
    await collector.observeBettingBoundary(event("BET"));
    await collector.recoverHistory(async () => historyPage([]));

    await collector.reconcileSession();
    expect(collector.currentHeartbeat()).toMatchObject({
      issue: null,
      phase: "UNKNOWN",
    });
    const nextIssue = "2607270002";
    await collector.observePageState({
      issue: nextIssue,
      phase: "BETTING",
      countdownMs: 5_000,
      observedAtMs: 5_000,
    });
    expect(collector.completeness(nextIssue).reasons).toContain(
      "history_anchor_missing",
    );

    await collector.recoverHistory(async () => historyPage([]));
    expect(collector.completeness(nextIssue).reasons).not.toContain(
      "history_anchor_missing",
    );
  });

  it("removes an open issue's old history proof when reconnecting in the same issue", async () => {
    const collector = runtime(new MemoryJournal(), server(session()));
    await collector.reconcileSession();
    await collector.observeBettingBoundary(event("BET"));
    await collector.recoverHistory(async () => historyPage([]));
    expect(collector.completeness(ISSUE).reasons).not.toContain(
      "history_anchor_missing",
    );

    await collector.reconcileSession();
    await collector.observePageState({
      issue: ISSUE,
      phase: "BETTING",
      countdownMs: 5_000,
      observedAtMs: 5_000,
    });
    expect(collector.completeness(ISSUE).reasons).toContain(
      "history_anchor_missing",
    );

    await collector.recoverHistory(async () => historyPage([]));
    expect(collector.completeness(ISSUE).reasons).not.toContain(
      "history_anchor_missing",
    );
  });

  it("keeps offline reload capture suspended until the later session proof arrives", async () => {
    const collector = runtime(new MemoryJournal(), server(session()));
    await collector.reconcileSession();
    await collector.observeBettingBoundary(event("BET"));
    await collector.recoverHistory(async () => historyPage([]));

    await collector.suspendForReconnect();
    expect(collector.currentHeartbeat()).toMatchObject({
      issue: null,
      phase: "UNKNOWN",
    });
    await collector.observePageState({
      issue: ISSUE,
      phase: "BETTING",
      countdownMs: 4_000,
      observedAtMs: 6_000,
    });
    await collector.ingest([event("BET")]);

    await collector.reconcileSession();
    expect(collector.currentHeartbeat()).toMatchObject({
      issue: ISSUE,
      phase: "BETTING",
    });
    expect(collector.completeness(ISSUE).reasons).toContain(
      "history_anchor_missing",
    );

    await collector.recoverHistory(async () => historyPage([]));
    expect(collector.completeness(ISSUE).reasons).not.toContain(
      "history_anchor_missing",
    );
  });
});

describe("secure collector bootstrap", () => {
  function steps(order: string[]): CollectorBootstrapSteps<string, string, string> {
    return {
      async loadIdentity() {
        order.push("identity");
      },
      async loadCredential() {
        order.push("credential");
        return "credential";
      },
      createServer() {
        order.push("network");
        return "server";
      },
      async openJournal() {
        order.push("journal");
      },
      async reconcileSession() {
        order.push("session");
      },
      async openWindow() {
        order.push("window");
        return "window";
      },
      startLoops() {
        order.push("loops");
      },
    };
  }

  it("constructs no network or window before credential import completes", async () => {
    const order: string[] = [];
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const value = steps(order);
    value.loadCredential = async () => {
      order.push("credential:start");
      await gate;
      order.push("credential:deleted");
      return "credential";
    };

    const pending = bootstrapCollector(value);
    await Promise.resolve();
    await Promise.resolve();
    expect(order).toEqual(["journal", "identity", "credential:start"]);
    release();
    await pending;

    expect(order).toEqual([
      "journal",
      "identity",
      "credential:start",
      "credential:deleted",
      "network",
      "session",
      "window",
      "loops",
    ]);
  });

  it.each([
    "collector_credential_missing",
    "collector_credential_encryption_unavailable",
    "collector_credential_source_delete_failed",
  ])("exposes only fixed credential error %s and keeps networking stopped", async (code) => {
    const order: string[] = [];
    const value = steps(order);
    value.loadCredential = async () => {
      throw new Error(code);
    };

    await expect(bootstrapCollector(value)).rejects.toThrow(code);
    expect(order).toEqual(["journal", "identity"]);
  });

  it("acquires the exclusive journal lock before identity or credential access", async () => {
    const order: string[] = [];
    const value = steps(order);
    value.openJournal = async () => {
      order.push("journal");
      throw new Error("journal_locked");
    };

    await expect(bootstrapCollector(value)).rejects.toThrow("journal_locked");
    expect(order).toEqual(["journal"]);
  });

  it("redacts an unexpected startup error", async () => {
    const value = steps([]);
    value.loadCredential = async () => {
      throw new Error("PRIVATE_CREDENTIAL_DETAIL");
    };

    await expect(bootstrapCollector(value)).rejects.toThrow(
      "collector_start_failed",
    );
  });

  it("runs injected cleanup when a post-journal bootstrap step fails", async () => {
    const order: string[] = [];
    const value = steps(order);
    value.openWindow = async () => {
      order.push("window");
      throw new Error("PRIVATE_WINDOW_DETAIL");
    };
    value.cleanup = async () => {
      order.push("cleanup");
    };

    await expect(bootstrapCollector(value)).rejects.toThrow(
      "collector_start_failed",
    );
    expect(order).toEqual([
      "journal",
      "identity",
      "credential",
      "network",
      "session",
      "window",
      "cleanup",
    ]);
  });
});
