import { describe, expect, it } from "vitest";

import {
  decodeHistoryMessages,
  installRoomHook,
  LiveCaptureMode,
  readBtcffcPageState,
} from "../src/bridge/page-hook.js";

describe("public room hook", () => {
  it("chooses exactly one live capture source for the page lifetime", () => {
    const sdkFirst = new LiveCaptureMode();
    expect(sdkFirst.claimSdk()).toBe(true);
    expect(sdkFirst.claimDom()).toBe(false);
    expect(sdkFirst.claimSdk()).toBe(true);

    const domFirst = new LiveCaptureMode();
    expect(domFirst.claimDom()).toBe(true);
    expect(domFirst.claimSdk()).toBe(false);
    expect(domFirst.claimDom()).toBe(true);
  });

  it("exports only the current Btcffc issue, countdown, and phase", () => {
    expect(
      readBtcffcPageState([
        {
          paramData: { model: "Btcffc" },
          game28Info: {
            serial: "2607270001",
            countdown: 1.25,
            process: "1",
            privateField: "must-not-leave-page",
          },
        },
      ]),
    ).toEqual({
      issue: "2607270001",
      countdownMs: 1_250,
      phase: "BETTING",
    });
    expect(
      readBtcffcPageState([
        {
          paramData: { model: "Btcffc" },
          game28Info: {
            serial: "2607270001",
            countdown: 0,
            process: "0",
          },
        },
      ]),
    ).toEqual({
      issue: "2607270001",
      countdownMs: 0,
      phase: "CLOSED",
    });
  });

  it("reads the current NG store when an empty legacy model masks paramData", () => {
    expect(
      readBtcffcPageState([
        {
          model: "",
          paramData: { model: "Btcffc" },
          game28Info: {
            serial: "2607280001",
            countdown: "17.5",
            process: "1",
          },
        },
      ]),
    ).toEqual({
      issue: "2607280001",
      countdownMs: 17_500,
      phase: "BETTING",
    });
  });

  it("wraps each active room once and restores the replaced room", async () => {
    const emitted: unknown[] = [];
    let firstCalls = 0;
    let secondCalls = 0;
    const first = {
      options: {
        onmsgs(messages: unknown[]) {
          firstCalls += messages.length;
          return "first";
        },
      },
    };
    const second = {
      options: {
        onmsgs(messages: unknown[]) {
          secondCalls += messages.length;
          return "second";
        },
      },
    };

    expect(installRoomHook(first, (payload) => emitted.push(payload))).toBe(true);
    expect(installRoomHook(first, (payload) => emitted.push(payload))).toBe(false);
    expect(first.options.onmsgs([1])).toBe("first");
    await Promise.resolve();
    expect(firstCalls).toBe(1);
    expect(emitted).toHaveLength(1);

    expect(installRoomHook(second, (payload) => emitted.push(payload))).toBe(true);
    first.options.onmsgs([2]);
    second.options.onmsgs([3]);
    await Promise.resolve();

    expect(firstCalls).toBe(2);
    expect(secondCalls).toBe(1);
    expect(emitted).toHaveLength(2);
  });

  it("prefers the SDK protocol callback over the page callback", async () => {
    const emitted: unknown[] = [];
    let pageCalls = 0;
    let protocolCalls = 0;
    const pageCallback = () => {
      pageCalls += 1;
    };
    const room = {
      options: { onmsgs: pageCallback },
      protocol: {
        options: {
          onmsgs(_messages: unknown[]) {
            protocolCalls += 1;
          },
        },
      },
    };

    expect(installRoomHook(room, (payload) => emitted.push(payload))).toBe(true);
    room.protocol.options.onmsgs([1]);
    await Promise.resolve();

    expect(protocolCalls).toBe(1);
    expect(pageCalls).toBe(0);
    expect(room.options.onmsgs).toBe(pageCallback);
    expect(emitted).toHaveLength(1);
  });

  it("captures the batch even when the page callback throws", async () => {
    const emitted: unknown[] = [];
    const failure = new Error("page callback failed");
    const room = {
      options: {
        onmsgs(_messages: unknown[]) {
          throw failure;
        },
      },
    };
    installRoomHook(room, (payload) => emitted.push(payload));

    expect(() => room.options.onmsgs([1])).toThrow(failure);
    await Promise.resolve();
    expect(emitted).toHaveLength(1);
  });

  it("emits the message after the SDK callback has completed decoding it", async () => {
    const emitted: Array<{ messages: unknown[] }> = [];
    const message: { text: unknown } = { text: "encrypted" };
    const room = {
      protocol: {
        options: {
          onmsgs(messages: unknown[]) {
            (messages[0] as { text: unknown }).text = {
              ext: { isRobot: "1", ext: { model: "Btcffc" } },
            };
          },
        },
      },
    };

    installRoomHook(room, (payload) => emitted.push(payload));
    room.protocol.options.onmsgs([message]);
    await Promise.resolve();

    expect(emitted).toEqual([{ messages: [message], origin: "realtime" }]);
    expect(message.text).toEqual({
      ext: { isRobot: "1", ext: { model: "Btcffc" } },
    });
  });

  it("decodes an SDK history page through the original page callback once", async () => {
    const emitted: unknown[] = [];
    const message: { text: unknown } = { text: "encrypted-history" };
    const room = {
      protocol: {
        options: {
          onmsgs(messages: unknown[]) {
            (messages[0] as { text: unknown }).text = {
              ext: {
                isRobot: "1",
                ext: {
                  type: "1",
                  serial: "2607291317",
                  at: "raw-player-candidate",
                  items: [{ title: "猜双面-第一球_大", money: "1" }],
                },
              },
            };
          },
        },
      },
    };
    installRoomHook(room, (payload) => emitted.push(payload));

    expect(decodeHistoryMessages(room, [message])).toBe(true);
    await Promise.resolve();

    expect(message.text).toEqual({
      ext: {
        isRobot: "1",
        ext: expect.objectContaining({ serial: "2607291317" }),
      },
    });
    expect(emitted).toEqual([]);
  });
});
