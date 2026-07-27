import WebSocket, { type RawData } from "ws";

import { HighestRevisionTasks } from "./task-contract";

export interface TaskWebSocket {
  on(event: "open", listener: () => void): this;
  on(event: "message", listener: (data: RawData | Buffer) => void): this;
  on(event: "close", listener: () => void): this;
  on(event: "error", listener: () => void): this;
  send(value: string): void;
  close(code?: number): void;
}

type TaskSocketOptions = {
  url: string;
  accessToken: () => Promise<string>;
  periodId: () => string;
  reducer: HighestRevisionTasks;
  websocketFactory?: (url: string, authorization: string) => TaskWebSocket;
};

export class TaskSocket {
  private socket: TaskWebSocket | null = null;
  private synchronized = false;

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
      socket.send(JSON.stringify({
        type: "SYNC",
        period_id: periodId,
        known_revision: this.options.reducer.current(periodId)?.revision ?? 0,
      }));
    });
    socket.on("message", (data) => this.onMessage(data));
    socket.on("error", () => {
      this.synchronized = false;
    });
    socket.on("close", () => {
      this.synchronized = false;
      this.socket = null;
    });
  }

  close(): void {
    this.socket?.close(1000);
    this.socket = null;
    this.synchronized = false;
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
        this.options.reducer.accept(message.task);
        this.synchronized = true;
        return;
      }
      if (message.type === "UP_TO_DATE" || message.type === "NO_TASK") {
        this.synchronized = true;
        return;
      }
      this.socket?.close(1008);
      return;
    }

    if (message.type === "TASK" && "task" in message) {
      this.options.reducer.accept(message.task);
      return;
    }
    this.socket?.close(1008);
  }
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
