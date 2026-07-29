// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import {
  readPlatformLiveState,
} from "../../src/main/platform-live-state";
import { readPageStateScript } from "../../src/main/platform-live-bridge";

type TestWindow = Window & typeof globalThis & {
  chatroom?: unknown;
  __championFollowSourceCounterV1?: unknown;
};

function installGameStore(input: Readonly<{
  serial: string;
  countdown: number | string;
  process: string;
}>): void {
  document.body.innerHTML = `
    <main id="app">
      <h1>比特分分彩</h1>
      <div class="betData"><span class="blueTxt">${input.serial}</span></div>
      <div class="van-count-down">00:00:00</div>
      <button>投注</button>
    </main>`;
  const root = document.querySelector("#app") as Element & {
    __vue_app__?: unknown;
  };
  root.__vue_app__ = {
    _context: {
      provides: {
        [Symbol("pinia")]: {
          _s: new Map([["gameStore", {
            model: "",
            paramData: { model: "Btcffc" },
            game28Info: {
              serial: input.serial,
              countdown: input.countdown,
              process: input.process,
            },
          }]]),
        },
      },
    },
  };
}

afterEach(() => {
  delete (window as TestWindow).chatroom;
  delete (window as TestWindow).__championFollowSourceCounterV1;
  document.body.innerHTML = "";
});

describe("platform live state", () => {
  it("uses Pinia countdown even when the visible DOM is stuck at zero", () => {
    installGameStore({
      serial: "2607291317",
      countdown: "17.5",
      process: "1",
    });

    expect(readPlatformLiveState(document)).toMatchObject({
      currentPeriodId: "2607291317",
      countdownMs: 17_500,
      phase: "OPEN",
    });

    const pageState = Function(
      "document",
      `return ${readPageStateScript()}`,
    )(document) as Record<string, unknown>;
    expect(pageState).toMatchObject({
      periodId: "2607291317",
      countdownMs: 17_500,
      phase: "OPEN",
    });
  });

  it("backfills and deduplicates current-issue public bets from the room source", async () => {
    installGameStore({
      serial: "2607291318",
      countdown: 21,
      process: "1",
    });
    const current = {
      idClient: "message-current",
      time: 2_000,
      text: "encrypted-current",
    };
    const previous = {
      idClient: "message-previous",
      time: 1_000,
      text: "encrypted-previous",
    };
    const decode = (messages: unknown[]) => {
      for (const message of messages as Array<typeof current>) {
        if (typeof message.text !== "string") continue;
        const isCurrent = message.idClient === "message-current";
        message.text = {
          ext: {
            isRobot: "1",
            ext: {
              type: "1",
              serial: isCurrent ? "2607291318" : "2607291317",
              at: isCurrent ? "actor-current" : "actor-previous",
              items: [{ title: "猜双面-第一球_大", money: "1" }],
            },
          },
        } as unknown as string;
      }
    };
    (window as TestWindow).chatroom = {
      options: { onmsgs: decode },
      getHistoryMsgs(options: {
        done(error: unknown, result: { msgs: unknown[] }): void;
      }) {
        queueMicrotask(() => options.done(null, { msgs: [current, previous] }));
      },
    };

    const initial = readPlatformLiveState(document);
    expect(initial.publicBetSourceComplete).toBe(false);
    await new Promise((resolve) => setTimeout(resolve, 0));

    const complete = readPlatformLiveState(document);
    expect(complete).toMatchObject({
      currentPeriodId: "2607291318",
      publicBetCommandCount: 1,
      publicBetSourceComplete: true,
    });

    const room = (window as TestWindow).chatroom as {
      options: { onmsgs(messages: unknown[]): void };
    };
    room.options.onmsgs([current]);
    await Promise.resolve();
    expect(readPlatformLiveState(document).publicBetCommandCount).toBe(1);
  });
});
