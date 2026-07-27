import type { Direction } from "./task-contract";

export type PlatformPhase = "OPEN" | "CLOSED" | "RESULT";
export type PlatformMarket = `P${1 | 2 | 3 | 4 | 5}:${Direction}`;

export type PlatformState = {
  periodId: string;
  countdownMs: number;
  phase: PlatformPhase;
  oddsMicrosByDirection: Readonly<Record<PlatformMarket, 1_960_000>>;
  minStakeFen: bigint;
  currentBalanceFen: bigint | null;
  receivedMonotonicMs: number;
};

export type PlatformStateError =
  | "PLATFORM_STATE_INVALID"
  | "PERIOD_ID_MISSING"
  | "PERIOD_MISMATCH"
  | "COUNTDOWN_INVALID"
  | "PHASE_INVALID"
  | "MARKET_CONTRACT_MISMATCH"
  | "ODDS_MISMATCH"
  | "STAKE_CONTRACT_INVALID"
  | "BALANCE_INVALID"
  | "PLATFORM_STATE_STALE";

export type PlatformStateResult =
  | { ok: true; state: PlatformState }
  | { ok: false; code: PlatformStateError };

const directions: Direction[] = [
  "BIG", "SMALL", "ODD", "EVEN", "PRIME", "COMPOSITE",
];

export const PLATFORM_MARKETS: readonly PlatformMarket[] = Object.freeze(
  Array.from({ length: 5 }, (_, index) => index + 1)
    .flatMap((position) => directions.map(
      (direction) => `P${position}:${direction}` as PlatformMarket,
    )),
);

const topLevelFields = [
  "periodId",
  "countdownMs",
  "phase",
  "oddsMicrosByDirection",
  "minStakeFen",
  "currentBalanceFen",
  "receivedMonotonicMs",
].sort();

export function parsePlatformState(
  value: unknown,
  options: { nowMonotonicMs: number; expectedPeriodId?: string },
): PlatformStateResult {
  if (!isObject(value) ||
      !sameStrings(Object.keys(value).sort(), topLevelFields)) {
    return { ok: false, code: "PLATFORM_STATE_INVALID" };
  }
  if (typeof value.periodId !== "string" ||
      !/^[A-Za-z0-9._-]{1,64}$/.test(value.periodId)) {
    return { ok: false, code: "PERIOD_ID_MISSING" };
  }
  if (options.expectedPeriodId !== undefined &&
      value.periodId !== options.expectedPeriodId) {
    return { ok: false, code: "PERIOD_MISMATCH" };
  }
  if (!Number.isSafeInteger(value.countdownMs) ||
      (value.countdownMs as number) < 0) {
    return { ok: false, code: "COUNTDOWN_INVALID" };
  }
  if (value.phase !== "OPEN" && value.phase !== "CLOSED" && value.phase !== "RESULT") {
    return { ok: false, code: "PHASE_INVALID" };
  }
  if (!isObject(value.oddsMicrosByDirection)) {
    return { ok: false, code: "MARKET_CONTRACT_MISMATCH" };
  }
  const oddsValue = value.oddsMicrosByDirection;
  const marketKeys = Object.keys(oddsValue).sort();
  if (!sameStrings(marketKeys, [...PLATFORM_MARKETS].sort())) {
    return { ok: false, code: "MARKET_CONTRACT_MISMATCH" };
  }
  if (PLATFORM_MARKETS.some(
    (market) => oddsValue[market] !== 1_960_000,
  )) {
    return { ok: false, code: "ODDS_MISMATCH" };
  }

  const minStakeFen = parseMoney(value.minStakeFen, false);
  if (minStakeFen === null || minStakeFen <= 0n) {
    return { ok: false, code: "STAKE_CONTRACT_INVALID" };
  }
  const currentBalanceFen = value.currentBalanceFen === null
    ? null
    : parseMoney(value.currentBalanceFen, true);
  if (value.currentBalanceFen !== null && currentBalanceFen === null) {
    return { ok: false, code: "BALANCE_INVALID" };
  }
  if (!Number.isFinite(options.nowMonotonicMs) ||
      !Number.isFinite(value.receivedMonotonicMs) ||
      typeof value.receivedMonotonicMs !== "number" ||
      value.receivedMonotonicMs < 0 ||
      options.nowMonotonicMs - value.receivedMonotonicMs < 0 ||
      options.nowMonotonicMs - value.receivedMonotonicMs > 500) {
    return { ok: false, code: "PLATFORM_STATE_STALE" };
  }

  const odds = Object.freeze({ ...oddsValue }) as Record<
    PlatformMarket,
    1_960_000
  >;
  return {
    ok: true,
    state: Object.freeze({
      periodId: value.periodId,
      countdownMs: value.countdownMs as number,
      phase: value.phase,
      oddsMicrosByDirection: odds,
      minStakeFen,
      currentBalanceFen,
      receivedMonotonicMs: value.receivedMonotonicMs,
    }),
  };
}

function parseMoney(value: unknown, allowZero: boolean): bigint | null {
  if (typeof value !== "string" || !/^(0|[1-9][0-9]{0,17})$/.test(value)) {
    return null;
  }
  const money = BigInt(value);
  return allowZero || money > 0n ? money : null;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function sameStrings(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}
