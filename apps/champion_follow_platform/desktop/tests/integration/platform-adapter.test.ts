import { describe, expect, it, vi } from "vitest";

import {
  SafePlatformAdapter,
  type FrozenOrder,
} from "../../src/main/platform-adapter";
import fixture from "../fixtures/platform/ffc-page-contract-v1.json";

const order: FrozenOrder = {
  clientOrderId: "00000000-0000-0000-0000-000000000021",
  generation: "00000000-0000-0000-0000-000000000020",
  taskId: "00000000-0000-0000-0000-000000000010",
  deviceId: "00000000-0000-0000-0000-000000000001",
  periodId: "2607270001",
  taskRevision: 1,
  position: 2,
  direction: "ODD",
  stakeFen: 100n,
  expectedOddsMicros: 1_960_000,
};

describe("SafePlatformAdapter", () => {
  it("preflights the page and returns only a hashed platform reference", async () => {
    const submit = vi.fn(async () => ({
      status: "CONFIRMED" as const,
      platformOrderReference: "private-platform-reference",
      periodId: order.periodId,
      position: order.position,
      direction: order.direction,
      stakeFen: order.stakeFen,
      oddsMicros: 1_960_000 as const,
      confirmedAt: "2026-07-27T04:00:01.000000Z",
      durationMs: 240,
    }));
    const adapter = new SafePlatformAdapter({
      readState: async () => fixture,
      submit,
      findOrder: async () => null,
      monotonicNow: () => 5_250,
    });
    const result = await adapter.submit(order);
    expect(result).toMatchObject({
      state: "CONFIRMED",
      platformOrderRef: expect.stringMatching(/^sha256:[0-9a-f]{64}$/),
    });
    expect(JSON.stringify(result)).not.toContain("private-platform-reference");
    expect(submit).toHaveBeenCalledOnce();
  });

  it("queries history after a timeout and freezes if no exact order exists", async () => {
    const adapter = new SafePlatformAdapter({
      readState: async () => fixture,
      submit: async () => ({ status: "TIMEOUT_AFTER_SEND" as const }),
      findOrder: async () => null,
      monotonicNow: () => 5_250,
    });
    expect(await adapter.submit(order)).toEqual({
      state: "UNKNOWN",
      reasonCode: "CONFIRMATION_TIMEOUT",
    });
  });
});
