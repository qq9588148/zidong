import { describe, expect, it } from "vitest";

import { capturedEventSchema, type CapturedEvent } from "../src/contracts.js";
import {
  HistoryBoundaryTracker,
  HistoryPageChunkAssembler,
} from "../src/history-page.js";

function closeEvent(index: number): CapturedEvent {
  return capturedEventSchema.parse({
    kind: "CLOSE",
    eventKey: index.toString(16).padStart(64, "0"),
    issue: "2607270001",
    sourceMs: 1_000 + index,
    receivedAtMs: 2_000 + index,
    source: "history",
    parserVersion: "btcffc-1",
    namespaceVersion: "actor-hmac-v1",
  });
}

describe("history page IPC", () => {
  it("reassembles every chunk before exposing a history page", () => {
    const events = Array.from({ length: 1_001 }, (_, index) =>
      closeEvent(index + 1),
    );
    const assembler = new HistoryPageChunkAssembler("history-1");
    const common = {
      requestId: "history-1",
      chunkCount: 2,
      messageCount: 100,
      minSourceMs: 1_000,
    };

    expect(
      assembler.push({
        ...common,
        chunkIndex: 0,
        events: events.slice(0, 1_000),
      }),
    ).toBeNull();
    expect(
      assembler.push({
        ...common,
        chunkIndex: 1,
        events: events.slice(1_000),
      }),
    ).toEqual({
      requestId: "history-1",
      events,
      messageCount: 100,
      minSourceMs: 1_000,
    });
  });

  it("marks a full-page timestamp boundary as uncertain on later pages", () => {
    const tracker = new HistoryBoundaryTracker();

    expect(tracker.observe(100, 1_000)).toEqual({
      crossedUncertainBoundary: false,
      uncertainBoundarySourceMs: 1_000,
    });
    expect(tracker.observe(25, 900)).toEqual({
      crossedUncertainBoundary: true,
      uncertainBoundarySourceMs: null,
    });

    tracker.reset();
    expect(tracker.observe(25, 800)).toEqual({
      crossedUncertainBoundary: false,
      uncertainBoundarySourceMs: null,
    });
  });
});
