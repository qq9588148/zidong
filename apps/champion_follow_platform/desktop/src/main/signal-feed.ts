import type { DeviceAuthViewState } from "./auth-client";
import {
  HighestRevisionTasks,
  TrustedTaskSigningKeys,
  type DeviceTaskEnvelope,
} from "./task-contract";
import { TaskSocket, type TaskSocketOptions } from "./task-socket";
import type {
  SignalTaskView,
  SignalViewState,
} from "../shared/ipc";

export type SignalFeedAuth = {
  viewState(): DeviceAuthViewState;
  deviceId(): string | null;
  accessToken(): Promise<string>;
  taskSigningKeys(): Promise<unknown>;
};

type TaskSocketLike = Pick<TaskSocket, "connect" | "close">;

type ReadonlySignalFeedOptions = {
  serverBaseUrl: string;
  auth: SignalFeedAuth;
  periodId: () => string | null;
  now?: () => number;
  reconnectDelayMs?: number;
  taskSocketFactory?: (options: TaskSocketOptions) => TaskSocketLike;
};

export class ReadonlySignalFeed {
  private state: SignalViewState = waitingState();
  private socket: TaskSocketLike | null = null;
  private connectedPeriod: string | null = null;
  private refreshInFlight = false;
  private nextAttemptAt = 0;
  private timer: ReturnType<typeof setInterval> | null = null;
  private connectionGeneration = 0;

  constructor(private readonly options: ReadonlySignalFeedOptions) {}

  viewState(): SignalViewState {
    return {
      ...this.state,
      task: this.state.task === null ? null : { ...this.state.task },
    } as SignalViewState;
  }

  start(): void {
    if (this.timer !== null) return;
    void this.refresh();
    this.timer = setInterval(() => void this.refresh(), 1_000);
    this.timer.unref?.();
  }

  stop(): void {
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    this.closeCurrent();
    this.state = waitingState();
  }

  async refresh(): Promise<void> {
    if (this.refreshInFlight) return;
    const authState = this.options.auth.viewState();
    if (authState.status !== "ONLINE") {
      this.closeCurrent();
      this.state = {
        status: authState.registered ? "AUTH_REQUIRED" : "WAITING_FOR_AUTH",
        periodId: null,
        task: null,
        errorCode: null,
      };
      return;
    }

    const periodId = this.options.periodId();
    if (periodId === null || !/^[A-Za-z0-9._-]{1,64}$/.test(periodId)) {
      this.closeCurrent();
      this.state = waitingState();
      return;
    }
    if (this.socket !== null && this.connectedPeriod === periodId) return;

    const now = this.now();
    if (now < this.nextAttemptAt) return;
    this.refreshInFlight = true;
    this.closeCurrent();
    this.state = {
      status: "CONNECTING",
      periodId,
      task: null,
      errorCode: null,
    };
    const generation = ++this.connectionGeneration;
    try {
      const deviceId = this.options.auth.deviceId();
      if (deviceId === null) throw new Error("signal_device_missing");
      const keys = TrustedTaskSigningKeys.fromResponse(
        await this.options.auth.taskSigningKeys(),
      );
      const reducer = new HighestRevisionTasks(deviceId, keys, () => this.now());
      let socket: TaskSocketLike;
      const socketOptions: TaskSocketOptions = {
        url: signalSocketUrl(this.options.serverBaseUrl),
        accessToken: () => this.options.auth.accessToken(),
        periodId: () => periodId,
        reducer,
        onSynchronized: (task) => {
          if (generation !== this.connectionGeneration) return;
          this.state = synchronizedState(periodId, task);
        },
        onTask: (task) => {
          if (generation !== this.connectionGeneration ||
              task.period_id !== this.connectedPeriod) return;
          this.state = synchronizedState(task.period_id, task);
        },
        onDisconnected: () => {
          if (generation !== this.connectionGeneration) return;
          this.socket = null;
          this.connectedPeriod = null;
          this.nextAttemptAt = this.now() + this.reconnectDelayMs();
          this.state = {
            status: "OFFLINE",
            periodId,
            task: null,
            errorCode: "SIGNAL_SOCKET_DISCONNECTED",
          };
        },
      };
      socket = this.options.taskSocketFactory?.(socketOptions) ??
        new TaskSocket(socketOptions);
      this.socket = socket;
      this.connectedPeriod = periodId;
      await socket.connect();
    } catch {
      if (generation === this.connectionGeneration) {
        this.closeCurrent();
        this.nextAttemptAt = this.now() + this.reconnectDelayMs();
        this.state = {
          status: "OFFLINE",
          periodId,
          task: null,
          errorCode: "SIGNAL_SYNC_FAILED",
        };
      }
    } finally {
      this.refreshInFlight = false;
    }
  }

  private closeCurrent(): void {
    this.connectionGeneration += 1;
    const socket = this.socket;
    this.socket = null;
    this.connectedPeriod = null;
    socket?.close();
  }

  private now(): number {
    return (this.options.now ?? Date.now)();
  }

  private reconnectDelayMs(): number {
    return this.options.reconnectDelayMs ?? 5_000;
  }
}

function synchronizedState(
  periodId: string,
  task: DeviceTaskEnvelope | null,
): SignalViewState {
  return {
    status: "SYNCED",
    periodId,
    task: task === null ? null : publicTask(task),
    errorCode: null,
  };
}

function publicTask(task: DeviceTaskEnvelope): SignalTaskView {
  if (task.action === "CANCEL") {
    return {
      action: "CANCEL",
      periodId: task.period_id,
      revision: task.revision,
      reason: task.payload.reason,
    };
  }
  return {
    action: "BET",
    periodId: task.period_id,
    revision: task.revision,
    ball: task.payload.ball,
    direction: task.payload.direction,
    signalVersion: task.payload.signal_version,
    userLevel: task.payload.user_level,
  };
}

function waitingState(): SignalViewState {
  return {
    status: "WAITING_FOR_PLATFORM",
    periodId: null,
    task: null,
    errorCode: null,
  };
}

function signalSocketUrl(serverBaseUrl: string): string {
  const url = new URL(serverBaseUrl);
  if (url.protocol !== "https:" || url.username || url.password ||
      url.search || url.hash || (url.pathname !== "/" && url.pathname !== "")) {
    throw new Error("signal_server_url_invalid");
  }
  url.protocol = "wss:";
  url.pathname = "/ws/v1/device-tasks";
  return url.toString();
}
