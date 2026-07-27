import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { parsePlatformState } from "../../src/main/platform-contract";

const fixture = () => JSON.parse(readFileSync(resolve(
  __dirname,
  "../fixtures/platform/ffc-page-contract-v1.json",
), "utf8")) as Record<string, unknown>;

describe("parsePlatformState", () => {
  it("accepts the complete fixed-odds page contract", () => {
    expect(parsePlatformState(fixture(), { nowMonotonicMs: 5_250 }))
      .toMatchObject({
        ok: true,
        state: {
          periodId: "2607270001",
          countdownMs: 12_500,
          minStakeFen: 100n,
          currentBalanceFen: 100_000n,
        },
      });
  });

  it("fails closed when odds, period or markets change", () => {
    const odds = fixture();
    (odds.oddsMicrosByDirection as Record<string, number>)["P3:ODD"] = 1_950_000;
    expect(parsePlatformState(odds, { nowMonotonicMs: 5_250 }))
      .toEqual({ ok: false, code: "ODDS_MISMATCH" });

    expect(parsePlatformState({ ...fixture(), periodId: "" }, { nowMonotonicMs: 5_250 }))
      .toEqual({ ok: false, code: "PERIOD_ID_MISSING" });

    const missing = fixture();
    delete (missing.oddsMicrosByDirection as Record<string, number>)["P5:COMPOSITE"];
    expect(parsePlatformState(missing, { nowMonotonicMs: 5_250 }))
      .toEqual({ ok: false, code: "MARKET_CONTRACT_MISMATCH" });
  });

  it("rejects stale observations and a different expected period", () => {
    expect(parsePlatformState(fixture(), { nowMonotonicMs: 5_501 }))
      .toEqual({ ok: false, code: "PLATFORM_STATE_STALE" });
    expect(parsePlatformState(fixture(), {
      nowMonotonicMs: 5_250,
      expectedPeriodId: "2607270002",
    })).toEqual({ ok: false, code: "PERIOD_MISMATCH" });
  });

  it("contains no credential or account fields in its fixture", () => {
    const text = JSON.stringify(fixture()).toLowerCase();
    for (const forbidden of [
      "cookie", "token", "authorization", "account", "uid", "password",
    ]) {
      expect(text).not.toContain(forbidden);
    }
  });
});
