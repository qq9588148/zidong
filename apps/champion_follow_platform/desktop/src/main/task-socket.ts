import WebSocket, { type RawData } from "ws";

import {
  HighestRevisionTasks,
  type DeviceTaskEnvelope,
} from "./task-contract";

export interface TaskWebSocket {
  on(event: "open", listener: () => void): this;
  on(event: "message", listener: (data: RawData | Buffer) => void): this;
  on(event: "close", listener: () => void): this;
  on(event: "error", listener: () => void): this;
  send(value: string): void;
  close(code?: number): void;
}

export type TaskSocketOptions = {
  url: string;
  accessToken: () => Promise<string>;
  periodId: () => string;
  reducer: HighestRevisionTasks;
  onSynchronized?: (task: DeviceTaskEnvelope | null) => void;
  onTask?: (task: DeviceTaskEnvelope) => void;
  onDisconnected?: () => void;
  websocketFactory?: (url: string, authorization: string) => TaskWebSocket;
};

export class TaskSocket {
  private socket: TaskWebSocket | null = null;
  private synchronized = false;
  private requestedPeriodId: string | null = null;

  constructor(private readonly options: TaskSocketOptions) {}

  async connect(): Promise<void> {
    const url = new URL(this.options.url);
    if (url.protocol !== "wss:") throw new Error("task_socket_tls_required");
    if (url.username || url.password || url.search || url.hash) {
      throw new Error("task_socket_url_invalid");
    }
    if (this.socket) throw new Error("task_socket_already_connected");

    const token = await this.options.accessToken();
    if (!token || /[\r\n]/.test(token)) throw new Error("task_socket_token_invalid");
    const factory = this.options.websocketFactory ?? ((target, authorization) =>
      new WebSocket(target, { headers: { Authorization: authorization } }));
    const socket = factory(url.toString(), `Bearer ${token}`);
    this.socket = socket;
    this.synchronized = false;

    socket.on("open", () => {
      const periodId = this.options.periodId();
      if (!/^[A-Za-z0-9._-]{1,64}$/.test(periodId)) {
        socket.close(1008);
        return;
      }
      this.requestedPeriodId = periodId;
      socket.send(JSON.stringify({
        type: "SYNC",
        period_id: periodId,
        known_revision: this.options.reducer.current(periodId)?.revision ?? 0,
      }));
    });
    socket.on("message", (data) => this.onMessage(data));
    socket.on("error", () => {
      this.synchronized = false;
      this.options.onDisconnected?.();
    });
    socket.on("close", () => {
      this.synchronized = false;
      this.requestedPeriodId = null;
      this.socket = null;
      this.options.onDisconnected?.();
    });
  }

  close(): void {
    this.socket?.close(1000);
    this.socket = null;
    this.synchronized = false;
    this.requestedPeriodId = null;
  }

  private onMessage(data: RawData | Buffer): void {
    const text = rawDataText(data);
    if (text === null || Buffer.byteLength(text, "utf8") > 256 * 1024) {
      this.socket?.close(1008);
      return;
    }
    let message: unknown;
    try {
      message = JSON.parse(text);
    } catch {
      this.socket?.close(1008);
      return;
    }
    if (!isObject(message) || typeof message.type !== "string") {
      this.socket?.close(1008);
      return;
    }

    if (!this.synchronized) {
      if (message.type === "TASK" && "task" in message) {
        const acceptance = this.options.reducer.accept(message.task);
        const task = parseTaskForPeriod(
          message.task,
          this.requestedPeriodId,
          this.options.reducer,
        );
        if ((acceptance !== "accepted" && acceptance !== "duplicate") ||
            task === null) {
          this.socket?.close(1008);
          return;
        }
        this.synchronized = true;
        this.options.onSynchronized?.(task);
        return;
      }
      if (isAuthoritativeSyncFrame(message, this.requestedPeriodId)) {
        this.synchronized = true;
        const task = message.type === "NO_TASK" || this.requestedPeriodId === null
          ? null
          : this.options.reducer.current(this.requestedPeriodId);
        this.options.onSynchronized?.(task);
        return;
      }
      this.socket?.close(1008);
      return;
    }

    if (message.type === "TASK" && "task" in message) {
      const acceptance = this.options.reducer.accept(message.task);
      if (acceptance !== "accepted" && acceptance !== "duplicate") {
        this.socket?.close(1008);
        return;
      }
      const task = parseAcceptedTask(message.task, this.options.reducer);
      if (task === null) {
        this.socket?.close(1008);
        return;
      }
      this.options.onTask?.(task);
      return;
    }
    if (isHeartbeatFrame(message)) {
      return;
    }
    this.socket?.close(1008);
  }
}

function parseTaskForPeriod(
  value: unknown,
  periodId: string | null,
  reducer: HighestRevisionTasks,
): DeviceTaskEnvelope | null {
  const task = parseAcceptedTask(value, reducer);
  return task !== null && periodId !== null && task.period_id === periodId
    ? task
    : null;
}

function parseAcceptedTask(
  value: unknown,
  reducer: HighestRevisionTasks,
): DeviceTaskEnvelope | null {
  if (!isObject(value) || typeof value.period_id !== "string") return null;
  return reducer.current(value.period_id);
}

function isAuthoritativeSyncFrame(
  value: Record<string, unknown>,
  periodId: string | null,
): value is Record<string, unknown> & {
  type: "UP_TO_DATE" | "NO_TASK";
  period_id: string;
  highest_revision: number;
} {
  if ((value.type !== "UP_TO_DATE" && value.type !== "NO_TASK") ||
      Object.keys(value).sort().join(",") !==
        "highest_revision,period_id,type" ||
      value.period_id !== periodId ||
      !Number.isSafeInteger(value.highest_revision) ||
      (value.highest_revision as number) < 0) return false;
  return value.type !== "NO_TASK" || value.highest_revision === 0;
}

function isHeartbeatFrame(value: Record<string, unknown>): boolean {
  return value.type === "HEARTBEAT" &&
    Object.keys(value).sort().join(",") === "server_time,type" &&
    typeof value.server_time === "string" &&
    Number.isFinite(Date.parse(value.server_time));
}

function rawDataText(data: RawData | Buffer): string | null {
  if (Buffer.isBuffer(data)) return data.toString("utf8");
  if (data instanceof ArrayBuffer) return Buffer.from(data).toString("utf8");
  if (Array.isArray(data)) return Buffer.concat(data).toString("utf8");
  return null;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
