import type { ClientEventType } from "./client-event-contract";
import type {
  FrozenOrder,
  PlatformSubmissionResult,
  SafePlatformAdapter,
} from "./platform-adapter";

export type ExecutionState = "SUBMITTING" | "CONFIRMED" | "REJECTED" | "UNKNOWN" | "CANCELED";

export type ExecutionRecord = {
  state: ExecutionState;
  order: FrozenOrder;
  result?: PlatformSubmissionResult;
};

export interface ExecutionStore {
  get(periodId: string): Promise<ExecutionRecord | null>;
  put(record: ExecutionRecord): Promise<void>;
  hasUnknown(): Promise<boolean>;
}

export class MemoryExecutionStore implements ExecutionStore {
  private readonly records = new Map<string, ExecutionRecord>();

  async get(periodId: string): Promise<ExecutionRecord | null> {
    const record = this.records.get(periodId);
    return record ? structuredClone(record) : null;
  }

  async put(record: ExecutionRecord): Promise<void> {
    this.records.set(record.order.periodId, structuredClone(record));
  }

  async hasUnknown(): Promise<boolean> {
    return [...this.records.values()].some((record) => record.state === "UNKNOWN");
  }
}

type EventSink = {
  emit(type: ClientEventType, payload: Record<string, unknown>): Promise<void>;
};

type ExecutionMachineOptions = {
  platform: Pick<SafePlatformAdapter, "submit" | "reconcile">;
  store: ExecutionStore;
  events: EventSink;
};

export class ExecutionMachine {
  private readonly inFlight = new Map<string, Promise<ExecutionRecord>>();

  constructor(private readonly options: ExecutionMachineOptions) {}

  execute(command: {
    order: FrozenOrder;
    stillCurrent: () => boolean;
  }): Promise<ExecutionRecord> {
    const existing = this.inFlight.get(command.order.periodId);
    if (existing) return existing;
    const running = this.executeOnce(command).finally(() => {
      this.inFlight.delete(command.order.periodId);
    });
    this.inFlight.set(command.order.periodId, running);
    return running;
  }

  async canExecuteNextPeriod(): Promise<boolean> {
    return !(await this.options.store.hasUnknown());
  }

  private async executeOnce(command: {
    order: FrozenOrder;
    stillCurrent: () => boolean;
  }): Promise<ExecutionRecord> {
    if (await this.options.store.hasUnknown()) {
      throw new Error("execution_frozen_unknown_order");
    }
    const existing = await this.options.store.get(command.order.periodId);
    if (existing) {
      if (existing.state === "SUBMITTING") return this.recover(existing);
      return existing;
    }
    if (!command.stillCurrent()) {
      const canceled: ExecutionRecord = { state: "CANCELED", order: command.order };
      await this.options.store.put(canceled);
      return canceled;
    }

    const submitting: ExecutionRecord = { state: "SUBMITTING", order: command.order };
    await this.options.store.put(submitting);
    await this.options.events.emit("EXECUTION_STATE", {
      task_id: command.order.taskId,
      period_id: command.order.periodId,
      revision: command.order.taskRevision,
      state: "SUBMITTING",
    });
    if (!command.stillCurrent()) {
      const canceled: ExecutionRecord = { state: "CANCELED", order: command.order };
      await this.options.store.put(canceled);
      return canceled;
    }

    let result: PlatformSubmissionResult;
    try {
      result = await this.options.platform.submit(command.order);
    } catch {
      result = { state: "UNKNOWN", reasonCode: "ADAPTER_FAILURE" };
    }
    return this.finish(command.order, result);
  }

  private async recover(record: ExecutionRecord): Promise<ExecutionRecord> {
    let result: PlatformSubmissionResult;
    try {
      result = await this.options.platform.reconcile(record.order);
    } catch {
      result = { state: "UNKNOWN", reasonCode: "RECOVERY_FAILURE" };
    }
    return this.finish(record.order, result);
  }

  private async finish(
    order: FrozenOrder,
    result: PlatformSubmissionResult,
  ): Promise<ExecutionRecord> {
    const record: ExecutionRecord = { state: result.state, order, result };
    await this.options.store.put(record);
    if (result.state === "CONFIRMED") {
      await this.options.events.emit("ORDER_CONFIRMED", {
        task_id: order.taskId,
        period_id: order.periodId,
        task_revision: order.taskRevision,
        generation: order.generation,
        client_order_id: order.clientOrderId,
        platform_order_ref: result.platformOrderRef,
        stake_minor: safeMinor(order.stakeFen),
        confirmed_at: result.confirmedAt,
      });
    } else {
      const unknown = result.state === "UNKNOWN";
      await this.options.events.emit(unknown ? "ORDER_UNKNOWN" : "ORDER_REJECTED", {
        task_id: order.taskId,
        period_id: order.periodId,
        task_revision: order.taskRevision,
        generation: order.generation,
        client_order_id: order.clientOrderId,
        reason_code: result.reasonCode,
        [unknown ? "unknown_at" : "rejected_at"]: utcMicros(new Date()),
      });
    }
    return record;
  }
}

function safeMinor(value: bigint): number {
  if (value < 1n || value > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new Error("execution_stake_invalid");
  }
  return Number(value);
}

function utcMicros(value: Date): string {
  return value.toISOString().replace(/\.(\d{3})Z$/, (_match, millis: string) =>
    `.${millis}000Z`);
}
