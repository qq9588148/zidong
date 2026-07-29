import { describe, expect, it, vi } from "vitest";

import { ExecutionMachine, MemoryExecutionStore } from "../../src/main/execution-machine";
import { SafePlatformAdapter, type FrozenOrder } from "../../src/main/platform-adapter";
import { PLATFORM_MARKETS, type PlatformState } from "../../src/main/platform-contract";
import { SafeBetScheduler, type SchedulerClock } from "../../src/main/scheduler";
import { signedTask } from "../helpers/signed-task";

class FakeClock implements SchedulerClock {
  private value = 0;
  private callback: (() => void) | null = null;

  now(): number { return this.value; }
  setTimer(callback: () => void, _delayMs: number): number {
    this.callback = callback;
    return 1;
  }
  clearTimer(): void { this.callback = null; }
  fire(): void {
    const callback = this.callback;
    this.callback = null;
    callback?.();
  }
}

describe("automatic bet flow", () => {
  it("turns one current signed task into one confirmed fixed-odds order", async () => {
    const clock = new FakeClock();
    const task = signedTask({ revision: 7, payload: { direction: "ODD" } });
    if (task.action !== "BET") throw new Error("expected_bet_task");
    const order: FrozenOrder = {
      clientOrderId: "00000000-0000-4000-8000-000000000021",
      generation: "00000000-0000-4000-8000-000000000022",
      taskId: task.task_id,
      deviceId: task.device_id,
      periodId: task.period_id,
      taskRevision: task.revision,
      position: task.payload.ball,
      direction: task.payload.direction,
      stakeFen: 100n,
      expectedOddsMicros: 1_960_000,
    };
    const odds = Object.fromEntries(
      PLATFORM_MARKETS.map((market) => [market, 1_960_000]),
    ) as PlatformState["oddsMicrosByDirection"];
    const platformState: PlatformState = {
      periodId: task.period_id,
      countdownMs: 3_000,
      phase: "OPEN",
      oddsMicrosByDirection: odds,
      minStakeFen: 100n,
      currentBalanceFen: 10_000n,
      receivedMonotonicMs: 0,
    };
    const submit = vi.fn(async () => ({
      status: "CONFIRMED" as const,
      platformOrderReference: "private-fixture-order",
      periodId: order.periodId,
      position: order.position,
      direction: order.direction,
      stakeFen: order.stakeFen,
      oddsMicros: 1_960_000,
      confirmedAt: "2026-07-29T13:00:01.000000Z",
      durationMs: 180,
    }));
    const adapter = new SafePlatformAdapter({
      readState: async () => ({
        ...platformState,
        minStakeFen: "100",
        currentBalanceFen: "10000",
      }),
      submit,
      findOrder: async () => null,
      monotonicNow: () => clock.now(),
    });
    const events: string[] = [];
    const machine = new ExecutionMachine({
      platform: adapter,
      store: new MemoryExecutionStore(),
      events: { emit: async (type) => { events.push(type); } },
    });
    let finished!: () => void;
    const completion = new Promise<void>((resolve) => { finished = resolve; });
    const scheduler = new SafeBetScheduler({
      clock,
      safeLeadMs: () => 2_000,
      freeze: () => order,
      execute: async (frozen) => {
        try {
          await machine.execute({ order: frozen, stillCurrent: () => true });
        } finally {
          finished();
        }
      },
    });

    scheduler.accept(task, platformState);
    clock.fire();
    await completion;

    expect(submit).toHaveBeenCalledOnce();
    expect(events).toEqual(["EXECUTION_STATE", "ORDER_CONFIRMED"]);
    expect(JSON.stringify(events)).not.toContain("private-fixture-order");
  });
});
