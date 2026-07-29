import { describe, expect, it } from "vitest";

import { createFfcNormalizer } from "../src/bridge/ffc-normalizer.js";
import {
  publicBetMessageFromValues,
} from "../src/bridge/dom-public-room.js";

describe("public room DOM fallback", () => {
  it("turns only an exact public bet row into the existing encrypted pipeline", async () => {
    const message = publicBetMessageFromValues({
      actor: "public-player-label",
      command: "第1球:大:10.50",
      stableId: "dom-id|public-17",
      issue: "2607291222",
      observedAtMs: 1_785_340_800_123,
      nonce: 1,
    });
    expect(message).not.toBeNull();
    expect(message?.idClient).toBe("dom-id|public-17|2607291222");
    const normalize = await createFfcNormalizer(
      new Uint8Array(32).fill(9),
      () => 1_785_340_800_456,
    );
    const events = await normalize(message, "realtime");
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      kind: "BET",
      issue: "2607291222",
      play: "P1:大",
      amountMinor: "1050",
    });
    expect(JSON.stringify(events)).not.toMatch(
      /public-player-label|public-17|10\.50/,
    );
  });

  it("supports the live command form and rejects unrelated room text", () => {
    expect(publicBetMessageFromValues({
      actor: "player",
      command: "猜双面:第三球:双:20",
      stableId: null,
      issue: "2607291223",
      observedAtMs: 1_785_340_801_000,
      nonce: 1,
    })).toMatchObject({
      text: { ext: { ext: { items: [{
        title: "第三球",
        items: [{ title: "双", money: "20" }],
      }] } } },
    });
    expect(publicBetMessageFromValues({
      actor: "player",
      command: "普通聊天消息",
      stableId: null,
      issue: "2607291223",
      observedAtMs: 1_785_340_801_001,
      nonce: 2,
    })).toBeNull();
  });
});
