import { describe, expect, it } from "vitest";

import { LatencyWindow, nearestRankP99 } from "../../src/main/latency";

describe("latency", () => {
  it("uses a conservative two-second lead before 30 real samples", () => {
    const latency = new LatencyWindow(400);
    for (let index = 0; index < 29; index += 1) latency.add(250);
    expect(latency.safeLeadMs()).toBe(2_000);
  });

  it("uses the nearest-rank p99 plus margin and keeps 500 samples", () => {
    const latency = new LatencyWindow(400);
    for (let value = 1; value <= 600; value += 1) latency.add(value);
    expect(latency.count).toBe(500);
    expect(nearestRankP99([1, 2, 3, 4, 5])).toBe(5);
    expect(latency.safeLeadMs()).toBe(995);
  });

  it("clamps learned lead time to the safe range", () => {
    const fast = new LatencyWindow(0);
    const slow = new LatencyWindow(2_000);
    for (let index = 0; index < 30; index += 1) {
      fast.add(1);
      slow.add(2_000);
    }
    expect(fast.safeLeadMs()).toBe(700);
    expect(slow.safeLeadMs()).toBe(3_000);
  });
});
