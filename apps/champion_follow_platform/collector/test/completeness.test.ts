import { describe, expect, it } from "vitest";

import { IssueCompletenessTracker } from "../src/completeness.js";
import { capturedEventSchema, type CapturedEvent } from "../src/contracts.js";

const ISSUE = "2607270001";
const actorKey = "a".repeat(64);
let eventNumber = 0;

function event(
  kind: CapturedEvent["kind"],
  changes: Record<string, unknown> = {},
): CapturedEvent {
  eventNumber += 1;
  const base = {
    kind,
    eventKey: eventNumber.toString(16).padStart(64, "0"),
    issue: ISSUE,
    sourceMs: 1000 + eventNumber,
    receivedAtMs: 2000 + eventNumber,
    source: "realtime",
    parserVersion: "btcffc-1",
    namespaceVersion: "actor-hmac-v1",
  };
  if (kind === "BET" || kind === "CANCEL") {
    Object.assign(base, {
      actorKey,
      play: "P1:大",
      amountMinor: "1000",
    });
  } else if (kind === "RESULT") {
    Object.assign(base, { digits: [1, 2, 3, 4, 5] });
  } else if (kind === "CAPTURE_GAP") {
    Object.assign(base, { reason: "decrypt_failure" });
  }
  return capturedEventSchema.parse({ ...base, ...changes });
}

function readyTracker(): IssueCompletenessTracker {
  const tracker = new IssueCompletenessTracker();
  tracker.observeBetting(ISSUE);
  tracker.markHistoryAnchorRecovered(ISSUE);
  return tracker;
}

function finish(tracker: IssueCompletenessTracker): void {
  tracker.ingest(event("CLOSE"));
  tracker.ingest(event("RESULT"));
}

describe("IssueCompletenessTracker", () => {
  it("accepts an exactly cancelled side followed by its opposite", () => {
    const tracker = readyTracker();
    tracker.ingest(event("BET"));
    tracker.ingest(event("CANCEL"));
    tracker.ingest(event("BET", { play: "P1:小", amountMinor: "500" }));
    finish(tracker);

    expect(tracker.evaluate(ISSUE)).toEqual({ complete: true, reasons: [] });
  });

  it("keeps an unattributed cancellation sticky", () => {
    const tracker = readyTracker();
    tracker.ingest(event("CANCEL_UNATTRIBUTED"));
    finish(tracker);

    expect(tracker.evaluate(ISSUE)).toMatchObject({
      complete: false,
      reasons: ["unattributed_cancel"],
    });
  });

  it("rejects cancellation larger than the matching net without changing it", () => {
    const tracker = readyTracker();
    tracker.ingest(event("BET", { amountMinor: "500" }));
    tracker.ingest(event("CANCEL", { amountMinor: "600" }));
    finish(tracker);

    expect(tracker.evaluate(ISSUE).reasons).toContain("cancel_overdraw");
  });

  it("rejects positive net on both opposite sides", () => {
    const tracker = readyTracker();
    tracker.ingest(event("BET", { play: "P1:大" }));
    tracker.ingest(event("BET", { play: "P1:小" }));
    finish(tracker);

    expect(tracker.evaluate(ISSUE).reasons).toContain(
      "opposite_net_conflict",
    );
  });

  it("requires both close and result boundaries", () => {
    const withoutResult = readyTracker();
    withoutResult.ingest(event("CLOSE"));
    expect(withoutResult.evaluate(ISSUE).reasons).toContain("result_missing");

    const withoutClose = readyTracker();
    withoutClose.ingest(event("RESULT"));
    expect(withoutClose.evaluate(ISSUE).reasons).toContain("close_missing");
  });

  it("requires a recovered history anchor", () => {
    const tracker = new IssueCompletenessTracker();
    tracker.observeBetting(ISSUE);
    finish(tracker);

    expect(tracker.evaluate(ISSUE).reasons).toContain(
      "history_anchor_missing",
    );
  });

  it("keeps the exact capture-gap reason sticky", () => {
    const tracker = readyTracker();
    tracker.ingest(event("CAPTURE_GAP", { reason: "journal_torn_tail" }));
    finish(tracker);

    expect(tracker.evaluate(ISSUE).reasons).toContain("journal_torn_tail");
  });

  it("emits only real incomplete-to-complete status transitions", () => {
    const tracker = readyTracker();
    const firstCommon = event("CLOSE");
    const first = tracker.statusTransition(ISSUE, firstCommon);
    expect(first).toMatchObject({
      kind: "ISSUE_STATUS",
      complete: false,
      reasons: ["close_missing", "result_missing"],
    });
    expect(tracker.statusTransition(ISSUE, firstCommon)).toBeNull();

    finish(tracker);
    const secondCommon = event("CLOSE");
    expect(tracker.statusTransition(ISSUE, secondCommon)).toMatchObject({
      kind: "ISSUE_STATUS",
      complete: true,
      reasons: [],
    });
    expect(tracker.statusTransition(ISSUE, secondCommon)).toBeNull();
  });
});
