import { describe, expect, it, vi } from "vitest";

import { PLATFORM_MARKETS, type PlatformState } from "../../src/main/platform-contract";
import type { FrozenOrder } from "../../src/main/platform-adapter";
import { SafeBetScheduler, type SchedulerClock } from "../../src/main/scheduler";
import { signedTask } from "../helpers/signed-task";

class FakeClock implements SchedulerClock {
  private value = 0;
  private nextId = 1;
  private readonly timers = new Map<number, { at: number; callback: () => void }>();

  now(): number { return this.value; }
  setTimer(callback: () => void, delayMs: number): number {
    const id = this.nextId++;
    this.timers.set(id, { at: this.value + delayMs, callback });
    return id;
  }
  clearTimer(id: unknown): void { this.timers.delete(id as number); }
  async advanceBy(milliseconds: number): Promise<void> {
    const target = this.value + milliseconds;
    while (true) {
      const due = [...this.timers.entries()]
        .filter(([, timer]) => timer.at <= target)
        .sort((left, right) => left[1].at - right[1].at)[0];
      if (!due) break;
      this.value = due[1].at;
      this.timers.delete(due[0]);
      due[1].callback();
      await Promise.resolve();
    }
    this.value = target;
    await Promise.resolve();
  }
}

function platform(clock: FakeClock, countdownMs: number): PlatformState {
  return {
    periodId: "2607270001",
    countdownMs,
    phase: "OPEN",
    oddsMicrosByDirection: Object.fromEntries(
      PLATFORM_MARKETS.map((market) => [market, 1_960_000]),
    ) as PlatformState["oddsMicrosByDirection"],
    minStakeFen: 100n,
    currentBalanceFen: 10_000n,
    receivedMonotonicMs: clock.now(),
  };
}

function frozen(direction: string): FrozenOrder {
  return {
    clientOrderId: "00000000-0000-0000-0000-000000000021",
    generation: "00000000-0000-0000-0000-000000000020",
    taskId: "00000000-0000-0000-0000-000000000010",
    deviceId: "00000000-0000-0000-0000-000000000001",
    periodId: "2607270001",
    taskRevision: 1,
    position: 2,
    direction: direction as FrozenOrder["direction"],
    stakeFen: 100n,
    expectedOddsMicros: 1_960_000,
  };
}

describe("SafeBetScheduler", () => {
  it("replaces the plan when a newer task arrives with enough time", async () => {
    const clock = new FakeClock();
    const execute = vi.fn(async (_order: FrozenOrder) => undefined);
    const scheduler = new SafeBetScheduler({
      clock,
      safeLeadMs: () => 1_300,
      freeze: (task) => frozen(task.action === "BET" ? task.payload.direction : "ODD"),
      execute,
    });
    scheduler.accept(signedTask({ revision: 2, payload: { direction: "BIG" } }), platform(clock, 3_000));
    scheduler.accept(signedTask({ revision: 3, payload: { direction: "SMALL" } }), platform(clock, 1_800));
    await clock.advanceBy(500);
    expect(execute).toHaveBeenCalledOnce();
    expect(execute.mock.calls[0]?.[0].direction).toBe("SMALL");
  });

  it("honors a late CANCEL and never falls back to the older BET", async () => {
    const clock = new FakeClock();
    const execute = vi.fn(async (_order: FrozenOrder) => undefined);
    const scheduler = new SafeBetScheduler({
      clock,
      safeLeadMs: () => 1_100,
      freeze: () => frozen("BIG"),
      execute,
    });
    scheduler.accept(signedTask({ revision: 4 }), platform(clock, 1_600));
    scheduler.accept(signedTask({ revision: 5, action: "CANCEL" }), platform(clock, 1_200));
    await clock.advanceBy(1_500);
    expect(execute).not.toHaveBeenCalled();
  });
});
