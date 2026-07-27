import type { PlatformState } from "./platform-contract";
import type { FrozenOrder } from "./platform-adapter";
import type { DeviceTaskEnvelope } from "./task-contract";

export interface SchedulerClock {
  now(): number;
  setTimer(callback: () => void, delayMs: number): unknown;
  clearTimer(id: unknown): void;
}

type SchedulerStatus =
  | "scheduled"
  | "late_signal"
  | "canceled"
  | "platform_blocked"
  | "freeze_blocked"
  | "executing";

type SchedulerOptions = {
  clock?: SchedulerClock;
  safeLeadMs: () => number;
  freeze: (task: DeviceTaskEnvelope, platform: PlatformState) => FrozenOrder | null;
  execute: (order: FrozenOrder) => Promise<unknown>;
  onStatus?: (status: SchedulerStatus) => void;
};

const systemClock: SchedulerClock = {
  now: () => performance.now(),
  setTimer: (callback, delayMs) => setTimeout(callback, delayMs),
  clearTimer: (id) => clearTimeout(id as NodeJS.Timeout),
};

export class SafeBetScheduler {
  private readonly clock: SchedulerClock;
  private currentTask: DeviceTaskEnvelope | null = null;
  private currentPlatform: PlatformState | null = null;
  private timer: unknown = null;

  constructor(private readonly options: SchedulerOptions) {
    this.clock = options.clock ?? systemClock;
  }

  accept(task: DeviceTaskEnvelope, platform: PlatformState): void {
    if (this.currentTask && task.period_id === this.currentTask.period_id &&
        task.revision < this.currentTask.revision) {
      return;
    }
    this.currentTask = task;
    this.currentPlatform = platform;
    this.reschedule();
  }

  updatePlatform(platform: PlatformState): void {
    this.currentPlatform = platform;
    this.reschedule();
  }

  stop(): void {
    this.clearTimer();
    this.currentTask = null;
    this.currentPlatform = null;
  }

  private reschedule(): void {
    this.clearTimer();
    const task = this.currentTask;
    const platform = this.currentPlatform;
    if (!task || !platform) return;
    if (task.action === "CANCEL") {
      this.options.onStatus?.("canceled");
      return;
    }
    if (platform.periodId !== task.period_id || platform.phase !== "OPEN") {
      this.options.onStatus?.("platform_blocked");
      return;
    }
    const safeLeadMs = this.options.safeLeadMs();
    if (!Number.isSafeInteger(safeLeadMs) || safeLeadMs < 0) {
      throw new Error("scheduler_safe_lead_invalid");
    }
    const delayMs = effectiveCountdown(platform, this.clock.now()) - safeLeadMs;
    if (delayMs <= 0) {
      this.options.onStatus?.("late_signal");
      return;
    }
    const revision = task.revision;
    this.timer = this.clock.setTimer(() => {
      this.timer = null;
      void this.freezeAndExecute(revision);
    }, delayMs);
    this.options.onStatus?.("scheduled");
  }

  private async freezeAndExecute(expectedRevision: number): Promise<void> {
    const task = this.currentTask;
    const platform = this.currentPlatform;
    if (!task || !platform || task.revision !== expectedRevision ||
        task.action !== "BET" || platform.periodId !== task.period_id ||
        platform.phase !== "OPEN") {
      return;
    }
    const order = this.options.freeze(task, platform);
    if (!order) {
      this.options.onStatus?.("freeze_blocked");
      return;
    }
    if (this.currentTask?.revision !== expectedRevision ||
        this.currentTask.action !== "BET") {
      return;
    }
    this.options.onStatus?.("executing");
    await this.options.execute(order);
  }

  private clearTimer(): void {
    if (this.timer !== null) {
      this.clock.clearTimer(this.timer);
      this.timer = null;
    }
  }
}

function effectiveCountdown(platform: PlatformState, now: number): number {
  const age = Math.max(0, now - platform.receivedMonotonicMs);
  return Math.max(0, platform.countdownMs - age);
}
