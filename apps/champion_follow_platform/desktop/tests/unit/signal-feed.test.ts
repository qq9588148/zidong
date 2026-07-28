import { describe, expect, it, vi } from "vitest";

import {
  ReadonlySignalFeed,
  type SignalFeedAuth,
} from "../../src/main/signal-feed";
import type { TaskSocketOptions } from "../../src/main/task-socket";
import {
  DEVICE_A,
  signedTask,
  signingKeysResponse,
} from "../helpers/signed-task";

function onlineAuth(): SignalFeedAuth {
  return {
    viewState: () => ({
      status: "ONLINE",
      registered: true,
      username: "client-user",
      deviceLabel: "00000001",
      errorCode: null,
    }),
    deviceId: () => DEVICE_A,
    accessToken: async () => "memory-only-token",
    taskSigningKeys: async () => signingKeysResponse,
  };
}

describe("ReadonlySignalFeed", () => {
  it("shows only a sanitized signed signal and never arms execution", async () => {
    let socketOptions: TaskSocketOptions | null = null;
    const connect = vi.fn(async () => undefined);
    const feed = new ReadonlySignalFeed({
      serverBaseUrl: "https://server.invalid",
      auth: onlineAuth(),
      periodId: () => "2607270001",
      now: () => Date.parse("2026-07-27T04:05:00.000Z"),
      taskSocketFactory: (options) => {
        socketOptions = options;
        return { connect, close: vi.fn() };
      },
    });

    await feed.refresh();
    expect(connect).toHaveBeenCalledOnce();
    expect(feed.viewState()).toMatchObject({
      status: "CONNECTING",
      periodId: "2607270001",
      task: null,
    });

    socketOptions!.onSynchronized?.(null);
    expect(feed.viewState()).toMatchObject({ status: "SYNCED", task: null });

    const task = signedTask();
    socketOptions!.onTask?.(task);
    expect(feed.viewState()).toMatchObject({
      status: "SYNCED",
      task: {
        action: "BET",
        periodId: "2607270001",
        revision: 1,
        ball: 2,
        direction: "ODD",
        signalVersion: 1,
        userLevel: "CORE",
      },
    });
    expect(JSON.stringify(feed.viewState())).not.toMatch(
      /A000007|signature|task_id|device_id|actor_ref/,
    );
  });

  it("waits for a platform period without opening a server socket", async () => {
    const keys = vi.fn(async () => signingKeysResponse);
    const auth = { ...onlineAuth(), taskSigningKeys: keys };
    const factory = vi.fn();
    const feed = new ReadonlySignalFeed({
      serverBaseUrl: "https://server.invalid",
      auth,
      periodId: () => null,
      taskSocketFactory: factory,
    });

    await feed.refresh();

    expect(feed.viewState()).toEqual({
      status: "WAITING_FOR_PLATFORM",
      periodId: null,
      task: null,
      errorCode: null,
    });
    expect(keys).not.toHaveBeenCalled();
    expect(factory).not.toHaveBeenCalled();
  });
});
