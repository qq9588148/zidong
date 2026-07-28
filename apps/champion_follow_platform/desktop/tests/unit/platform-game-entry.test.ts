// @vitest-environment jsdom

import { describe, expect, it, vi } from "vitest";

import {
  clickBtcFfcEntry,
  primeBtcFfcBetPanel,
} from "../../src/main/platform-game-entry";

describe("NG game entry", () => {
  it("clicks the exact 比特分分彩 lobby card", () => {
    document.body.innerHTML = `
      <section class="lottery-game"><p class="game-title">分分彩</p></section>
      <section class="lottery-game" id="target">
        <p class="game-title">比特分分彩</p>
      </section>`;
    const target = document.querySelector<HTMLElement>("#target")!;
    const click = vi.spyOn(target, "click");

    expect(clickBtcFfcEntry(document)).toBe("CLICKED");
    expect(click).toHaveBeenCalledOnce();
  });

  it("does not enter a game before the platform session is authenticated", () => {
    document.body.innerHTML = `
      <section class="lottery-game" id="target">
        <p class="game-title">比特分分彩</p>
      </section>`;
    Object.defineProperty(document.body, "innerText", {
      configurable: true,
      value: "请先登录或注册",
    });
    const target = document.querySelector<HTMLElement>("#target")!;
    const click = vi.spyOn(target, "click");

    expect(clickBtcFfcEntry(document)).toBe("AUTH_REQUIRED");
    expect(click).not.toHaveBeenCalled();
    Object.defineProperty(document.body, "innerText", {
      configurable: true,
      value: "",
    });
  });

  it("fails closed when the exact lobby card is absent", () => {
    document.body.innerHTML = `
      <section class="lottery-game"><p class="game-title">比特28</p></section>`;

    expect(clickBtcFfcEntry(document)).toBe("NOT_FOUND");
  });

  it("opens only the outer bet panel control without selecting or submitting", () => {
    document.body.innerHTML = `
      <button id="open">投注</button>
      <section class="Game28Follow" hidden>
        <button id="submit">投注</button>
      </section>`;
    const open = vi.spyOn(document.querySelector<HTMLElement>("#open")!, "click");
    const submit = vi.spyOn(
      document.querySelector<HTMLElement>("#submit")!,
      "click",
    );

    expect(primeBtcFfcBetPanel(document)).toBe("OPENED");
    expect(open).toHaveBeenCalledOnce();
    expect(submit).not.toHaveBeenCalled();
  });
});
