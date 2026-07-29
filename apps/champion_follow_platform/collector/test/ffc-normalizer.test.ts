import { describe, expect, it } from "vitest";

import { createFfcNormalizer } from "../src/bridge/ffc-normalizer.js";

const normalize = await createFfcNormalizer(new Uint8Array(32).fill(7), () =>
  2000,
);

describe("Btcffc normalizer", () => {
  it("hashes actor/message identities and keeps only supported plays", async () => {
    const rows = await normalize(
      {
        idClient: "raw-message-marker",
        from: "shared-robot",
        time: 1000,
        text: {
          ext: {
            isRobot: "1",
            uid: "raw-player-marker",
            ext: {
              model: "Btcffc",
              type: "1",
              serial: "2607270001",
              items: [
                {
                  title: "第一球",
                  items: [
                    { title: "大", money: "10.50" },
                    { title: "7", money: "99" },
                  ],
                },
              ],
            },
          },
        },
      },
      "realtime",
    );
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      kind: "BET",
      play: "P1:大",
      amountMinor: "1050",
    });
    const first = rows[0];
    expect(first?.kind).toBe("BET");
    if (first?.kind !== "BET") throw new Error("expected_bet");
    expect(first.actorKey).toBe(
      "3f2c2852b9b66ac8f72ff53743739a72b4da885b22030048c87eb91a06933641",
    );
    expect(first.eventKey).toBe(
      "f589af606f587cf2c8d8cc41c059a934b3987355a60f0a99da751c0e58402b30",
    );
    expect(JSON.stringify(rows)).not.toMatch(
      /raw-player-marker|raw-message-marker|shared-robot/,
    );
  });

  it("emits exact cancel only with actor, play, and amount", async () => {
    const rows = await normalize(
      {
        idClient: "cancel-1",
        time: 1001,
        text: {
          ext: {
            isRobot: "1",
            uid: "player-1",
            ext: {
              model: "Btcffc",
              type: "2",
              serial: "2607270001",
              tipType: "1,b",
              title: "已取消",
              items: [
                {
                  title: "第一球",
                  items: [{ title: "大", money: "10.50" }],
                },
              ],
            },
          },
        },
      },
      "realtime",
    );
    expect(rows[0]).toMatchObject({
      kind: "CANCEL",
      play: "P1:大",
      amountMinor: "1050",
    });
  });

  it("never guesses ambiguous cancellation ownership", async () => {
    const rows = await normalize(
      {
        idClient: "cancel-2",
        time: 1002,
        text: {
          ext: {
            isRobot: "1",
            ext: {
              model: "Btcffc",
              type: "2",
              serial: "2607270001",
              title: "玩家已撤单",
            },
          },
        },
      },
      "history",
    );
    expect(rows[0]).toMatchObject({
      kind: "CANCEL_UNATTRIBUTED",
      issue: "2607270001",
    });
  });

  it("requires an exact five-digit result", async () => {
    const valid = await normalize(
      {
        idClient: "result-1",
        time: 1003,
        text: {
          ext: {
            isRobot: "1",
            ext: {
              model: "Btcffc",
              type: "4",
              serial: "2607270001",
              result: [1, 2, 3, 4, 5],
            },
          },
        },
      },
      "realtime",
    );
    const invalid = await normalize(
      {
        idClient: "result-2",
        time: 1004,
        text: {
          ext: {
            isRobot: "1",
            ext: {
              model: "Btcffc",
              type: "4",
              serial: "2607270002",
              result: [1, 2, 3],
            },
          },
        },
      },
      "realtime",
    );
    expect(valid[0]).toMatchObject({ kind: "RESULT", digits: [1, 2, 3, 4, 5] });
    expect(invalid).toEqual([]);
  });

  it("normalizes the live NG flat public-bet payload and hashes its page actor", async () => {
    const rows = await normalize(
      {
        idClient: "live-flat-bet-1",
        from: "shared-room-robot",
        time: 1005,
        text: {
          ext: {
            isRobot: "1",
            ext: {
              type: "1",
              serial: "2607291317",
              at: "raw-live-player-candidate",
              items: [
                { title: "猜双面-第五球_小", money: "12.50" },
                { title: "猜双面-第五球_双", money: "8" },
                { title: "猜数字-第五球_7", money: "99" },
              ],
            },
          },
        },
      },
      "history",
    );

    expect(rows).toHaveLength(2);
    expect(rows).toEqual(expect.arrayContaining([
      expect.objectContaining({ kind: "BET", play: "P5:小", amountMinor: "1250" }),
      expect.objectContaining({ kind: "BET", play: "P5:双", amountMinor: "800" }),
    ]));
    expect(rows.every((row) => row.kind === "BET" &&
      /^[a-f0-9]{64}$/.test(row.actorKey))).toBe(true);
    expect(JSON.stringify(rows)).not.toMatch(
      /raw-live-player-candidate|shared-room-robot|live-flat-bet-1/,
    );
  });

  it("normalizes the live NG result object without trusting its title", async () => {
    const rows = await normalize(
      {
        idClient: "live-result-1",
        time: 1006,
        text: {
          ext: {
            isRobot: "1",
            ext: {
              type: "4",
              title: "第2607291317期开奖结果",
              result: {
                serial: "2607291317",
                time: "12:34",
                value: "1+2+3+4+5=15",
              },
            },
          },
        },
      },
      "realtime",
    );

    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      kind: "RESULT",
      issue: "2607291317",
      digits: [1, 2, 3, 4, 5],
    });
  });
});
