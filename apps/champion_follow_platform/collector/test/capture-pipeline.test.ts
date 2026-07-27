import { describe, expect, it } from "vitest";

import {
  chunkCaptureEvents,
  createFifoDispatcher,
} from "../src/capture-pipeline.js";

describe("preload capture pipeline", () => {
  it("keeps asynchronously normalized message batches in FIFO order", async () => {
    let releaseFirst!: () => void;
    const firstBlocked = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    const order: string[] = [];
    const failures: unknown[] = [];
    const dispatch = createFifoDispatcher<number>(
      async (value) => {
        order.push(`start:${value}`);
        if (value === 1) await firstBlocked;
        order.push(`finish:${value}`);
      },
      (error) => failures.push(error),
    );

    const first = dispatch(1);
    const second = dispatch(2);
    await Promise.resolve();
    await Promise.resolve();

    expect(order).toEqual(["start:1"]);
    releaseFirst();
    await Promise.all([first, second]);
    expect(order).toEqual(["start:1", "finish:1", "start:2", "finish:2"]);
    expect(failures).toEqual([]);
  });

  it("splits legal expanded captures instead of rejecting the whole batch", () => {
    const events = Array.from({ length: 2_001 }, (_, index) => index);

    expect(chunkCaptureEvents(events).map((chunk) => chunk.length)).toEqual([
      1_000, 1_000, 1,
    ]);
    expect(chunkCaptureEvents([])).toEqual([]);
  });
});
