import { describe, expect, it, vi } from "vitest";

import {
  ExecutionMachine,
  MemoryExecutionStore,
} from "../../src/main/execution-machine";
import type { FrozenOrder } from "../../src/main/platform-adapter";

const order: FrozenOrder = {
  clientOrderId: "00000000-0000-0000-0000-000000000021",
  generation: "00000000-0000-0000-0000-000000000020",
  taskId: "00000000-0000-0000-0000-000000000010",
  deviceId: "00000000-0000-0000-0000-000000000001",
  periodId: "2607270001",
  taskRevision: 4,
  position: 2,
  direction: "ODD",
  stakeFen: 100n,
  expectedOddsMicros: 1_960_000,
};

describe("ExecutionMachine", () => {
  it("submits at most once when one period is called concurrently", async () => {
    const platform = {
      submit: vi.fn(async () => ({
        state: "CONFIRMED" as const,
        platformOrderRef: `sha256:${"a".repeat(64)}`,
        confirmedAt: "2026-07-27T04:00:01.000000Z",
        durationMs: 240,
      })),
      reconcile: vi.fn(),
    };
    const types: string[] = [];
    const machine = new ExecutionMachine({
      platform,
      store: new MemoryExecutionStore(),
      events: { emit: async (type) => { types.push(type); } },
    });
    await Promise.all(Array.from({ length: 100 }, () =>
      machine.execute({ order, stillCurrent: () => true })));
    expect(platform.submit).toHaveBeenCalledOnce();
    expect(types).toEqual(["EXECUTION_STATE", "ORDER_CONFIRMED"]);
  });

  it("does not retry an unknown submission or execute the next period", async () => {
    const platform = {
      submit: vi.fn(async () => ({
        state: "UNKNOWN" as const,
        reasonCode: "CONFIRMATION_TIMEOUT",
      })),
      reconcile: vi.fn(),
    };
    const store = new MemoryExecutionStore();
    const machine = new ExecutionMachine({
      platform,
      store,
      events: { emit: async () => undefined },
    });
    expect(await machine.execute({ order, stillCurrent: () => true }))
      .toMatchObject({ state: "UNKNOWN" });
    await expect(machine.execute({
      order: { ...order, periodId: "2607270002" },
      stillCurrent: () => true,
    })).rejects.toThrow("execution_frozen_unknown_order");
    expect(platform.submit).toHaveBeenCalledOnce();
  });
});
