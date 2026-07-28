import { randomUUID } from "node:crypto";
import { join } from "node:path";

import type { DeviceAuthClient } from "./auth-client";
import { BankrollStore } from "./bankroll-store";
import {
  freezeUnknownSettlement,
  freshBankroll,
  planNextStake,
  settleLoss,
  settleWin,
  type BankrollState,
} from "./bankroll";
import {
  JsonClientEventOutbox,
  ReliableClientEventClient,
} from "./client-event-client";
import { ClientEventContract, type ClientEventType } from "./client-event-contract";
import { parseDeviceSyncSafety } from "./device-sync";
import { deviceKeyName } from "./device-identity";
import { ExecutionMachine, type ExecutionRecord } from "./execution-machine";
import { JsonExecutionStore } from "./execution-journal";
import type { NativeHelper } from "./native-helper";
import { SafePlatformAdapter, type FrozenOrder } from "./platform-adapter";
import { parsePlatformState, type PlatformState } from "./platform-contract";
import { NgPlatformBridge } from "./platform-live-bridge";
import {
  isPlatformWindowOpen,
  platformEndpointRegistry,
  reopenNgPlatformWindow,
} from "./platform-window";
import { SafeBetScheduler } from "./scheduler";
import type { ReadonlySignalFeed } from "./signal-feed";
import {
  TrustedTaskSigningKeys,
  type DeviceTaskEnvelope,
  type Direction,
} from "./task-contract";
import type { ExecutionBlock } from "../shared/ipc";

type RuntimeOptions = {
  auth: DeviceAuthClient;
  signals: ReadonlySignalFeed;
  helper: NativeHelper;
  journalDirectory: string;
  generation: () => string;
};

export class DesktopExecutionRuntime {
  private enabled = false;
  private initialized = false;
  private initializing = false;
  private timer: ReturnType<typeof setInterval> | null = null;
  private readonly bridge = new NgPlatformBridge();
  private readonly platform = new SafePlatformAdapter(this.bridge);
  private scheduler: SafeBetScheduler | null = null;
  private machine: ExecutionMachine | null = null;
  private executionStore: JsonExecutionStore | null = null;
  private bankrollStore: BankrollStore | null = null;
  private bankroll: BankrollState | null = null;
  private events: ReliableClientEventClient | null = null;
  private eventChain: Promise<void> = Promise.resolve();
  private platformState: PlatformState | null = null;
  private lastTaskKey: string | null = null;
  private pendingSettlement: ExecutionRecord | null = null;
  private globalStopEnabled = true;
  private deviceSyncVerified = false;
  private nextDeviceSyncMonotonicMs = 0;
  private tickInFlight = false;

  constructor(private readonly options: RuntimeOptions) {}

  start(): void {
    if (this.timer !== null) return;
    void this.tick();
    this.timer = setInterval(() => void this.tick(), 250);
    this.timer.unref?.();
  }

  stop(): void {
    this.enabled = false;
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    this.scheduler?.stop();
  }

  canEnable(): boolean {
    return this.initialized &&
      this.deviceSyncVerified &&
      !this.globalStopEnabled &&
      this.options.auth.viewState().status === "ONLINE" &&
      isPlatformWindowOpen() &&
      this.platformState !== null &&
      this.platformState.periodId.length > 0 &&
      this.platformState.phase === "OPEN" &&
      this.platformState.currentBalanceFen !== null &&
      this.options.signals.viewState().status === "SYNCED" &&
      this.bankroll?.status === "READY" &&
      this.pendingSettlement === null;
  }

  setEnabled(enabled: boolean): void {
    this.enabled = enabled && this.canEnable();
    if (!this.enabled) {
      this.scheduler?.stop();
      this.lastTaskKey = null;
    }
  }

  isEnabled(): boolean {
    return this.enabled;
  }

  blockReason(): ExecutionBlock | null {
    if (!this.initialized || !this.deviceSyncVerified) {
      return "SAFETY_SYNC_UNAVAILABLE";
    }
    if (this.globalStopEnabled) return "SERVER_GLOBAL_STOP";
    return this.canEnable() ? null : "STARTUP_SYNC_REQUIRED";
  }

  private async initialize(): Promise<void> {
    if (this.initialized || this.initializing ||
        this.options.auth.viewState().status !== "ONLINE") return;
    const identity = this.options.auth.runtimeIdentity();
    if (identity === null) return;
    this.initializing = true;
    try {
      const sync = parseDeviceSyncSafety(
        await this.options.auth.deviceSync(),
        { deviceId: identity.deviceId, bindingEpoch: identity.bindingEpoch },
      );
      this.deviceSyncVerified = true;
      this.globalStopEnabled = sync.globalStopEnabled;
      this.nextDeviceSyncMonotonicMs = this.bridge.monotonicNow() + 1_000;
      try {
        const keys = TrustedTaskSigningKeys.fromResponse(
          await this.options.auth.taskSigningKeys(),
        );
        if (platformEndpointRegistry.applySigned(
          await this.options.auth.platformEndpointConfig(),
          keys,
        ) === "accepted") {
          reopenNgPlatformWindow();
        }
      } catch {
        // No override means the built-in HTTPS endpoint remains authoritative.
      }
      const outbox = new JsonClientEventOutbox(
        join(this.options.journalDirectory, "client-events.json"),
        identity.bindingEpoch,
      );
      const contract = new ClientEventContract({
        deviceId: identity.deviceId,
        bindingEpoch: identity.bindingEpoch,
        helper: this.options.helper,
        keyName: deviceKeyName(identity.localId),
      });
      this.events = new ReliableClientEventClient({
        outbox,
        build: (sequence, type, payload) => contract.build(sequence, type, payload),
        transport: (bytes) => this.options.auth.sendClientEvent(bytes),
      });
      await this.events.flush();

      this.executionStore = new JsonExecutionStore(
        join(this.options.journalDirectory, "executions.json"),
      );
      this.bankrollStore = new BankrollStore(
        join(this.options.journalDirectory, "bankroll.json"),
      );
      this.bankroll = await this.bankrollStore.load();
      if (this.bankroll === null) {
        this.bankroll = freshBankroll({
          baseFen: 100n,
          capFen: 100n,
          stakeUnitFen: 1n,
        });
        await this.bankrollStore.save(this.bankroll, null);
      }

      this.machine = new ExecutionMachine({
        platform: this.platform,
        store: this.executionStore,
        events: { emit: (type, payload) => this.emit(type, payload) },
      });
      this.scheduler = new SafeBetScheduler({
        safeLeadMs: () => 2_000,
        freeze: (task, state) => this.freeze(task, state),
        execute: (order) => this.execute(order),
        onStatus: (status) => {
          if (status === "platform_blocked" || status === "freeze_blocked") {
            this.enabled = false;
          }
        },
      });

      const pending = await this.executionStore.pendingRecovery();
      if (pending?.state === "SUBMITTING") {
        const recovered = await this.machine.execute({
          order: pending.order,
          stillCurrent: () => false,
        });
        await this.afterExecution(recovered);
      } else if (pending?.state === "CONFIRMED") {
        this.pendingSettlement = pending;
      }
      if (await this.executionStore.hasUnknown()) {
        this.bankroll = freezeUnknownSettlement(
          this.bankroll,
          "unresolved-platform-order",
        );
        await this.bankrollStore.save(this.bankroll, this.bankroll.version - 1);
      }
      this.initialized = true;
    } finally {
      this.initializing = false;
    }
  }

  private async tick(): Promise<void> {
    if (this.tickInFlight) return;
    this.tickInFlight = true;
    try {
      await this.initialize();
      if (!this.initialized || !isPlatformWindowOpen()) {
        this.platformState = null;
        this.enabled = false;
        return;
      }
      await this.refreshDeviceSync();
      const parsed = parsePlatformState(await this.bridge.readState(), {
        nowMonotonicMs: this.bridge.monotonicNow(),
      });
      if (!parsed.ok) {
        this.platformState = null;
        this.enabled = false;
        return;
      }
      this.platformState = parsed.state;
      await this.checkSettlement();

      const task = this.options.signals.currentTask();
      if (task === null || task.period_id !== parsed.state.periodId) {
        this.scheduler?.updatePlatform(parsed.state);
        return;
      }
      const taskKey = `${task.task_id}:${task.revision}:${task.action}`;
      if (taskKey !== this.lastTaskKey) {
        this.lastTaskKey = taskKey;
        await this.emit("TASK_RECEIVED", {
          task_id: task.task_id,
          period_id: task.period_id,
          revision: task.revision,
        });
        this.scheduler?.accept(task, parsed.state);
      } else {
        this.scheduler?.updatePlatform(parsed.state);
      }
    } catch {
      this.enabled = false;
      this.platformState = null;
    } finally {
      this.tickInFlight = false;
    }
  }

  private async refreshDeviceSync(): Promise<void> {
    const now = this.bridge.monotonicNow();
    if (now < this.nextDeviceSyncMonotonicMs) return;
    this.nextDeviceSyncMonotonicMs = now + 1_000;
    const identity = this.options.auth.runtimeIdentity();
    if (identity === null) {
      this.deviceSyncVerified = false;
      this.globalStopEnabled = true;
      this.enabled = false;
      throw new Error("device_sync_identity_unavailable");
    }
    try {
      const sync = parseDeviceSyncSafety(
        await this.options.auth.deviceSync(),
        { deviceId: identity.deviceId, bindingEpoch: identity.bindingEpoch },
      );
      this.deviceSyncVerified = true;
      this.globalStopEnabled = sync.globalStopEnabled;
    } catch {
      this.deviceSyncVerified = false;
      this.globalStopEnabled = true;
      this.enabled = false;
      this.scheduler?.stop();
      throw new Error("device_sync_unavailable");
    }
    if (this.globalStopEnabled) {
      this.enabled = false;
      this.scheduler?.stop();
      this.lastTaskKey = null;
    }
  }

  private freeze(task: DeviceTaskEnvelope, state: PlatformState): FrozenOrder | null {
    const identity = this.options.auth.runtimeIdentity();
    if (!this.enabled || this.globalStopEnabled || identity === null ||
        task.action !== "BET" ||
        this.bankroll === null || this.pendingSettlement !== null ||
        state.currentBalanceFen === null) return null;
    const plan = planNextStake(this.bankroll, state.currentBalanceFen);
    if (plan.kind !== "READY") return null;
    return {
      clientOrderId: randomUUID(),
      generation: this.options.generation(),
      taskId: task.task_id,
      deviceId: identity.deviceId,
      periodId: task.period_id,
      taskRevision: task.revision,
      position: task.payload.ball,
      direction: task.payload.direction,
      stakeFen: plan.stakeFen,
      expectedOddsMicros: 1_960_000,
    };
  }

  private async execute(order: FrozenOrder): Promise<ExecutionRecord | null> {
    if (this.machine === null) return null;
    const result = await this.machine.execute({
      order,
      stillCurrent: () => {
        const current = this.options.signals.currentTask();
        return this.enabled && !this.globalStopEnabled &&
          current?.action === "BET" &&
          current.task_id === order.taskId &&
          current.revision === order.taskRevision;
      },
    });
    await this.afterExecution(result);
    return result;
  }

  private async afterExecution(record: ExecutionRecord): Promise<void> {
    if (record.state === "CONFIRMED") {
      this.pendingSettlement = record;
      return;
    }
    if (record.state === "UNKNOWN" && this.bankroll && this.bankrollStore) {
      const previous = this.bankroll.version;
      this.bankroll = freezeUnknownSettlement(this.bankroll, record.order.clientOrderId);
      await this.bankrollStore.save(this.bankroll, previous);
      this.enabled = false;
      await this.emitBankroll();
    }
  }

  private async checkSettlement(): Promise<void> {
    const record = this.pendingSettlement;
    if (record === null || this.executionStore === null ||
        this.bankrollStore === null || this.bankroll === null) return;
    const digits = await this.bridge.readIssueResult(record.order.periodId);
    if (digits === null || digits.length !== 5) return;
    const digit = digits[record.order.position - 1];
    if (!Number.isInteger(digit)) return;
    const win = directionFor(record.order.direction, digit!) === record.order.direction;
    const settlementId = randomUUID();
    const previousVersion = this.bankroll.version;
    this.bankroll = win
      ? settleWin(this.bankroll, {
          orderId: record.order.clientOrderId,
          stakeFen: record.order.stakeFen,
          netFen: record.order.stakeFen * 96n / 100n,
        })
      : settleLoss(this.bankroll, {
          orderId: record.order.clientOrderId,
          stakeFen: record.order.stakeFen,
        });
    await this.bankrollStore.save(this.bankroll, previousVersion);
    await this.executionStore.put({ ...record, state: "SETTLED" });
    this.pendingSettlement = null;
    await this.emit("SETTLEMENT_CONFIRMED", {
      client_order_id: record.order.clientOrderId,
      period_id: record.order.periodId,
      outcome: win ? "WIN" : "LOSS",
      net_pnl_minor: Number(win
        ? record.order.stakeFen * 96n / 100n
        : -record.order.stakeFen),
      settled_at: utcMicros(new Date()),
    });
    void settlementId;
    await this.emitBankroll();
    if (this.platformState?.currentBalanceFen !== null &&
        this.platformState?.currentBalanceFen !== undefined) {
      await this.emit("BALANCE_SNAPSHOT", {
        availability: "AVAILABLE",
        balance_minor: Number(this.platformState.currentBalanceFen),
      });
    }
  }

  private emit(type: ClientEventType, payload: Record<string, unknown>): Promise<void> {
    this.eventChain = this.eventChain.catch(() => undefined).then(async () => {
      if (this.events === null) throw new Error("client_event_runtime_unavailable");
      await this.events.enqueue(type, payload);
      await this.events.flush();
    });
    return this.eventChain;
  }

  private async emitBankroll(): Promise<void> {
    if (this.bankroll === null) return;
    const plan = planNextStake(this.bankroll);
    await this.emit("BANKROLL_STATE", {
      base_minor: Number(this.bankroll.baseFen),
      cap_minor: Number(this.bankroll.capFen),
      unrecovered_loss_minor: Number(this.bankroll.unrecoveredFen),
      next_stake_minor: plan.kind === "READY" ? Number(plan.stakeFen) : 0,
      cycle_id: this.bankroll.cycleId,
      cycle_version: this.bankroll.version,
      frozen_reason: this.bankroll.status === "FROZEN_UNKNOWN_SETTLEMENT"
        ? "UNKNOWN_SETTLEMENT" : null,
    });
  }
}

function directionFor(expected: Direction, digit: number): Direction {
  if (expected === "BIG" || expected === "SMALL") return digit >= 5 ? "BIG" : "SMALL";
  if (expected === "ODD" || expected === "EVEN") return digit % 2 ? "ODD" : "EVEN";
  return new Set([1, 2, 3, 5, 7]).has(digit) ? "PRIME" : "COMPOSITE";
}

function utcMicros(value: Date): string {
  return value.toISOString().replace(/\.(\d{3})Z$/, (_match, millis: string) =>
    `.${millis}000Z`);
}
