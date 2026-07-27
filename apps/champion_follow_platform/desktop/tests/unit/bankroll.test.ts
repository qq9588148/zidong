import { describe, expect, it } from "vitest";

import {
  freshBankroll,
  freezeUnknownSettlement,
  nextStakeFen,
  planNextStake,
  recoveryStakeFen,
  settleLoss,
  settleWin,
} from "../../src/main/bankroll";

describe("bankroll", () => {
  it("recovers accumulated principal without adding a profit target", () => {
    let state = freshBankroll({
      baseFen: 1_000n,
      capFen: 100_000n,
      stakeUnitFen: 100n,
    });
    state = settleLoss(state, { orderId: "o1", stakeFen: 1_000n });
    expect(nextStakeFen(state)).toBe(1_100n);
    state = settleLoss(state, { orderId: "o2", stakeFen: 1_100n });
    expect(nextStakeFen(state)).toBe(2_200n);
    state = settleWin(state, {
      orderId: "o3",
      stakeFen: 2_200n,
      netFen: 2_112n,
    });
    expect(state.unrecoveredFen).toBe(0n);
    expect(nextStakeFen(state)).toBe(1_000n);
  });

  it("closes a cycle at the cap without erasing historical loss", () => {
    const state = {
      ...freshBankroll({ baseFen: 100n, capFen: 500n, stakeUnitFen: 100n }),
      unrecoveredFen: 600n,
    };
    expect(planNextStake(state)).toEqual({
      kind: "RESET_AT_CAP",
      realizedLossFen: 600n,
      nextStakeFen: 100n,
    });
    expect(state.realizedPnlFen).toBe(0n);
  });

  it("never shrinks a stake to fit the balance and freezes unknown orders", () => {
    let state = settleLoss(
      freshBankroll({ baseFen: 100n, capFen: 10_000n, stakeUnitFen: 100n }),
      { orderId: "o1", stakeFen: 1_000n },
    );
    expect(planNextStake(state, 1_000n)).toEqual({
      kind: "BLOCKED_BALANCE",
      requiredFen: 1_100n,
      availableFen: 1_000n,
    });
    state = freezeUnknownSettlement(state, "o2");
    expect(planNextStake(state)).toEqual({ kind: "FROZEN_UNKNOWN_SETTLEMENT" });
  });

  it("applies the same settlement id only once", () => {
    const initial = freshBankroll({
      baseFen: 100n,
      capFen: 10_000n,
      stakeUnitFen: 1n,
    });
    const once = settleLoss(initial, { orderId: "same", stakeFen: 100n });
    expect(settleLoss(once, { orderId: "same", stakeFen: 100n })).toEqual(once);
  });

  it("rounds to the smallest platform unit that recovers every loss", () => {
    for (const unit of [1n, 10n, 100n]) {
      for (let loss = 1n; loss <= 1_000_000n; loss += 1n) {
        const stake = recoveryStakeFen(loss, unit);
        if ((stake * 96n) / 100n < loss) {
          throw new Error(`under-recovered loss ${loss} at unit ${unit}`);
        }
        if (stake > unit && ((stake - unit) * 96n) / 100n >= loss) {
          throw new Error(`stake was not minimal for loss ${loss} at unit ${unit}`);
        }
      }
    }
  }, 20_000);
});
