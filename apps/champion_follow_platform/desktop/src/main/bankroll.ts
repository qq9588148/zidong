import { randomUUID } from "node:crypto";

export type BankrollStatus = "READY" | "FROZEN_UNKNOWN_SETTLEMENT";

export type BankrollState = {
  generation: string;
  cycleId: string;
  version: number;
  baseFen: bigint;
  capFen: bigint;
  stakeUnitFen: bigint;
  unrecoveredFen: bigint;
  realizedPnlFen: bigint;
  status: BankrollStatus;
  pendingUnknownOrderId: string | null;
  lastSettlementId: string | null;
  settledOrderIds: readonly string[];
};

export type BankrollConfig = {
  baseFen: bigint;
  capFen: bigint;
  stakeUnitFen: bigint;
};

export type StakePlan =
  | { kind: "READY"; stakeFen: bigint }
  | { kind: "RESET_AT_CAP"; realizedLossFen: bigint; nextStakeFen: bigint }
  | { kind: "BLOCKED_BALANCE"; requiredFen: bigint; availableFen: bigint }
  | { kind: "FROZEN_UNKNOWN_SETTLEMENT" };

export const ceilDiv = (numerator: bigint, denominator: bigint): bigint => {
  if (numerator < 0n || denominator <= 0n) {
    throw new Error("bankroll_division_invalid");
  }
  return (numerator + denominator - 1n) / denominator;
};

export const ceilToUnit = (value: bigint, unit: bigint): bigint =>
  ceilDiv(value, unit) * unit;

export const recoveryStakeFen = (
  lossFen: bigint,
  stakeUnitFen: bigint,
): bigint => ceilToUnit(ceilDiv(lossFen * 100n, 96n), stakeUnitFen);

export function freshBankroll(config: BankrollConfig): BankrollState {
  validateConfig(config);
  return {
    generation: randomUUID(),
    cycleId: randomUUID(),
    version: 1,
    ...config,
    unrecoveredFen: 0n,
    realizedPnlFen: 0n,
    status: "READY",
    pendingUnknownOrderId: null,
    lastSettlementId: null,
    settledOrderIds: [],
  };
}

export function nextStakeFen(state: BankrollState): bigint {
  validateBankrollState(state);
  return state.unrecoveredFen === 0n
    ? state.baseFen
    : recoveryStakeFen(state.unrecoveredFen, state.stakeUnitFen);
}

export function planNextStake(
  state: BankrollState,
  availableBalanceFen?: bigint,
): StakePlan {
  validateBankrollState(state);
  if (state.status === "FROZEN_UNKNOWN_SETTLEMENT") {
    return { kind: "FROZEN_UNKNOWN_SETTLEMENT" };
  }
  const requiredFen = nextStakeFen(state);
  if (requiredFen > state.capFen) {
    return {
      kind: "RESET_AT_CAP",
      realizedLossFen: state.unrecoveredFen,
      nextStakeFen: state.baseFen,
    };
  }
  if (availableBalanceFen !== undefined) {
    if (availableBalanceFen < 0n) throw new Error("bankroll_balance_invalid");
    if (availableBalanceFen < requiredFen) {
      return { kind: "BLOCKED_BALANCE", requiredFen, availableFen: availableBalanceFen };
    }
  }
  return { kind: "READY", stakeFen: requiredFen };
}

export function settleLoss(
  state: BankrollState,
  settlement: { orderId: string; stakeFen: bigint },
): BankrollState {
  validateSettlement(state, settlement.orderId, settlement.stakeFen);
  if (state.settledOrderIds.includes(settlement.orderId)) return state;
  return {
    ...state,
    version: state.version + 1,
    unrecoveredFen: state.unrecoveredFen + settlement.stakeFen,
    realizedPnlFen: state.realizedPnlFen - settlement.stakeFen,
    lastSettlementId: settlement.orderId,
    settledOrderIds: [...state.settledOrderIds, settlement.orderId],
  };
}

export function settleWin(
  state: BankrollState,
  settlement: { orderId: string; stakeFen: bigint; netFen: bigint },
): BankrollState {
  validateSettlement(state, settlement.orderId, settlement.stakeFen);
  if (settlement.netFen < 0n) throw new Error("bankroll_net_invalid");
  if (state.settledOrderIds.includes(settlement.orderId)) return state;
  return {
    ...state,
    cycleId: randomUUID(),
    version: state.version + 1,
    unrecoveredFen: 0n,
    realizedPnlFen: state.realizedPnlFen + settlement.netFen,
    lastSettlementId: settlement.orderId,
    settledOrderIds: [...state.settledOrderIds, settlement.orderId],
  };
}

export function freezeUnknownSettlement(
  state: BankrollState,
  orderId: string,
): BankrollState {
  validateBankrollState(state);
  validateOrderId(orderId);
  if (state.status === "FROZEN_UNKNOWN_SETTLEMENT" &&
      state.pendingUnknownOrderId === orderId) {
    return state;
  }
  return {
    ...state,
    version: state.version + 1,
    status: "FROZEN_UNKNOWN_SETTLEMENT",
    pendingUnknownOrderId: orderId,
  };
}

export function validateBankrollState(state: BankrollState): void {
  validateConfig(state);
  if (!Number.isSafeInteger(state.version) || state.version < 1 ||
      !state.generation || !state.cycleId ||
      state.unrecoveredFen < 0n ||
      !Array.isArray(state.settledOrderIds) ||
      state.settledOrderIds.some((orderId) => typeof orderId !== "string" || !orderId) ||
      (state.status !== "READY" && state.status !== "FROZEN_UNKNOWN_SETTLEMENT") ||
      (state.status === "READY" && state.pendingUnknownOrderId !== null) ||
      (state.status === "FROZEN_UNKNOWN_SETTLEMENT" && !state.pendingUnknownOrderId)) {
    throw new Error("bankroll_state_invalid");
  }
}

function validateConfig(config: BankrollConfig): void {
  if (config.baseFen <= 0n || config.capFen < config.baseFen ||
      config.stakeUnitFen <= 0n ||
      config.baseFen % config.stakeUnitFen !== 0n ||
      config.capFen % config.stakeUnitFen !== 0n) {
    throw new Error("bankroll_config_invalid");
  }
}

function validateSettlement(
  state: BankrollState,
  orderId: string,
  stakeFen: bigint,
): void {
  validateBankrollState(state);
  validateOrderId(orderId);
  if (state.status !== "READY" || stakeFen <= 0n ||
      stakeFen % state.stakeUnitFen !== 0n) {
    throw new Error("bankroll_settlement_invalid");
  }
}

function validateOrderId(orderId: string): void {
  if (!orderId || orderId.length > 128) throw new Error("bankroll_order_id_invalid");
}
