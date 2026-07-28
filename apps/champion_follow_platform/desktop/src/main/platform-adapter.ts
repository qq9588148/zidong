import { createHash } from "node:crypto";

import { parsePlatformState } from "./platform-contract";
import type { Direction } from "./task-contract";

export type FrozenOrder = {
  clientOrderId: string;
  generation: string;
  taskId: string;
  deviceId: string;
  periodId: string;
  taskRevision: number;
  position: 1 | 2 | 3 | 4 | 5;
  direction: Direction;
  stakeFen: bigint;
  expectedOddsMicros: 1_960_000;
};

export type RawConfirmation = {
  status: "CONFIRMED";
  platformOrderReference: string;
  periodId: string;
  position: number;
  direction: string;
  stakeFen: bigint;
  oddsMicros: number;
  confirmedAt: string;
  durationMs: number;
};

export type RawSubmission =
  | RawConfirmation
  | { status: "REJECTED"; reasonCode?: string }
  | { status: "TIMEOUT_AFTER_SEND" };

export type PlatformSubmissionResult =
  | {
      state: "CONFIRMED";
      platformOrderRef: string;
      confirmedAt: string;
      durationMs: number;
    }
  | { state: "REJECTED"; reasonCode: string }
  | { state: "UNKNOWN"; reasonCode: string };

export type PlatformBridge = {
  readState(): Promise<unknown>;
  submit(order: FrozenOrder): Promise<RawSubmission>;
  findOrder(order: FrozenOrder): Promise<RawConfirmation | null>;
  monotonicNow(): number;
};

export class SafePlatformAdapter {
  constructor(private readonly bridge: PlatformBridge) {}

  async submit(order: FrozenOrder): Promise<PlatformSubmissionResult> {
    const contract = parsePlatformState(await this.bridge.readState(), {
      nowMonotonicMs: this.bridge.monotonicNow(),
      expectedPeriodId: order.periodId,
    });
    if (!contract.ok || contract.state.phase !== "OPEN" ||
        order.expectedOddsMicros !== 1_960_000 ||
        order.stakeFen < contract.state.minStakeFen ||
        (contract.state.currentBalanceFen !== null &&
         contract.state.currentBalanceFen < order.stakeFen)) {
      return { state: "REJECTED", reasonCode: "PLATFORM_PREFLIGHT_BLOCKED" };
    }

    let submitted: RawSubmission;
    try {
      submitted = await this.bridge.submit(order);
    } catch {
      return this.reconcile(order);
    }
    if (submitted.status === "CONFIRMED") {
      return normalizeConfirmation(order, submitted);
    }
    if (submitted.status === "REJECTED") {
      return { state: "REJECTED", reasonCode: safeReason(submitted.reasonCode) };
    }
    return this.reconcile(order);
  }

  async reconcile(order: FrozenOrder): Promise<PlatformSubmissionResult> {
    let found: RawConfirmation | null;
    try {
      found = await this.bridge.findOrder(order);
    } catch {
      found = null;
    }
    if (!found) {
      return { state: "UNKNOWN", reasonCode: "CONFIRMATION_TIMEOUT" };
    }
    return normalizeConfirmation(order, found);
  }
}

function normalizeConfirmation(
  order: FrozenOrder,
  confirmation: RawConfirmation,
): PlatformSubmissionResult {
  if (confirmation.periodId !== order.periodId ||
      confirmation.position !== order.position ||
      confirmation.direction !== order.direction ||
      confirmation.stakeFen !== order.stakeFen ||
      confirmation.oddsMicros !== order.expectedOddsMicros ||
      !confirmation.platformOrderReference ||
      !Number.isSafeInteger(confirmation.durationMs) || confirmation.durationMs < 0 ||
      !Number.isFinite(Date.parse(confirmation.confirmedAt))) {
    return { state: "UNKNOWN", reasonCode: "CONFIRMATION_MISMATCH" };
  }
  return {
    state: "CONFIRMED",
    platformOrderRef: `sha256:${createHash("sha256")
      .update(confirmation.platformOrderReference, "utf8")
      .digest("hex")}`,
    confirmedAt: confirmation.confirmedAt,
    durationMs: confirmation.durationMs,
  };
}

function safeReason(value?: string): string {
  return value && /^[A-Z0-9_]{1,64}$/.test(value)
    ? value
    : "PLATFORM_REJECTED";
}
