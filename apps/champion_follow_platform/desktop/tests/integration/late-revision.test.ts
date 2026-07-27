import { describe, expect, it, vi } from "vitest";

import { SafeBetScheduler } from "../../src/main/scheduler";
import { signedTask } from "../helpers/signed-task";

describe("late revision", () => {
  it("records a late signal instead of submitting immediately", () => {
    const status = vi.fn();
    const scheduler = new SafeBetScheduler({
      clock: {
        now: () => 1_000,
        setTimer: vi.fn(() => 1),
        clearTimer: vi.fn(),
      },
      safeLeadMs: () => 2_000,
      freeze: vi.fn(),
      execute: vi.fn(),
      onStatus: status,
    });
    scheduler.accept(signedTask({ revision: 9 }), {
      periodId: "2607270001",
      countdownMs: 1_500,
      phase: "OPEN",
      oddsMicrosByDirection: {} as never,
      minStakeFen: 100n,
      currentBalanceFen: null,
      receivedMonotonicMs: 1_000,
    });
    expect(status).toHaveBeenCalledWith("late_signal");
  });
});
