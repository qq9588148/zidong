import { EventEmitter } from "node:events";
import { describe, expect, it } from "vitest";

import {
  HighestRevisionTasks,
  TrustedTaskSigningKeys,
} from "../../src/main/task-contract";
import { TaskSocket, type TaskWebSocket } from "../../src/main/task-socket";
import {
  DEVICE_A,
  signedTask,
  signingKeysResponse,
} from "../helpers/signed-task";

class FakeSocket extends EventEmitter implements TaskWebSocket {
  readonly sent: string[] = [];

  send(value: string): void {
    this.sent.push(value);
  }

  close(): void {}
}

describe("TaskSocket", () => {
  it("syncs the known revision before accepting the live stream", async () => {
    const fake = new FakeSocket();
    let connection: { url: string; authorization: string } | undefined;
    const reducer = new HighestRevisionTasks(
      DEVICE_A,
      TrustedTaskSigningKeys.fromResponse(signingKeysResponse),
      () => Date.parse("2026-07-27T04:05:00.000Z"),
    );
    reducer.accept(signedTask({ revision: 3 }));
    const socket = new TaskSocket({
      url: "wss://server.invalid/ws/v1/device-tasks",
      accessToken: async () => "memory-only-token",
      periodId: () => "2607270001",
      reducer,
      websocketFactory: (url, authorization) => {
        connection = { url, authorization };
        return fake;
      },
    });

    await socket.connect();
    fake.emit("open");
    expect(connection).toEqual({
      url: "wss://server.invalid/ws/v1/device-tasks",
      authorization: "Bearer memory-only-token",
    });
    expect(fake.sent).toEqual([JSON.stringify({
      type: "SYNC",
      period_id: "2607270001",
      known_revision: 3,
    })]);

    fake.emit("message", Buffer.from(JSON.stringify({ type: "UP_TO_DATE" })));
    fake.emit("message", Buffer.from(JSON.stringify({
      type: "TASK",
      task: signedTask({ revision: 4, action: "CANCEL" }),
    })));
    expect(reducer.current("2607270001")?.revision).toBe(4);
    expect(reducer.current("2607270001")?.action).toBe("CANCEL");
  });

  it("refuses non-TLS sockets and never places the token in the URL", async () => {
    const reducer = new HighestRevisionTasks(
      DEVICE_A,
      TrustedTaskSigningKeys.fromResponse(signingKeysResponse),
      () => Date.parse("2026-07-27T04:05:00.000Z"),
    );
    const socket = new TaskSocket({
      url: "ws://server.invalid/ws/v1/device-tasks",
      accessToken: async () => "memory-only-token",
      periodId: () => "2607270001",
      reducer,
    });
    await expect(socket.connect()).rejects.toThrow("task_socket_tls_required");
  });
});
