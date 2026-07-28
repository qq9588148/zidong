// @vitest-environment jsdom

import { describe, expect, it } from "vitest";

import {
  mergePlatformPageProbes,
  parsePlatformPageProbe,
  probePlatformDocument,
} from "../../src/main/platform-page-probe";

describe("platform page probe", () => {
  it("detects the game contract without returning page text or money values", () => {
    document.body.innerHTML = `
      <main>
        <h1>比特分分彩</h1>
        <div class="betData">
          <div class="left">第<span class="blueTxt">2607280123</span>期开奖</div>
          <div class="left">
            <div><span>账户余额</span></div><div class="pt-1">1000.00</div>
          </div>
        </div>
        <div class="van-count-down">00:00:42</div>
        <section>
          <button>大</button><button>小</button>
          <button>单</button><button>双</button>
          <button>质</button><button>合</button>
        </section>
        <input type="number" placeholder="请输入投注金额" />
        <button>投注</button>
      </main>`;

    const result = probePlatformDocument(document);
    expect(result).toMatchObject({
      gameVisible: true,
      currentPeriodId: "2607280123",
      countdownMs: 42_000,
      periodCandidateCount: 1,
      countdownCandidateCount: 1,
      balanceLabelVisible: true,
      balanceValueReadable: true,
      publicBetCommandCount: 0,
      stakeInputCount: 1,
      betControlCount: 1,
      contractReady: true,
      directionTextCounts: {
        BIG: 1,
        SMALL: 1,
        ODD: 1,
        EVEN: 1,
        PRIME: 1,
        COMPOSITE: 1,
      },
    });
    expect(JSON.stringify(result)).not.toMatch(/1000\.00|账户余额|投注金额/);
  });

  it("uses the fixed 1.96 contract without scraping odds from the page", () => {
    document.body.innerHTML = `
      <main>
        <h1>比特分分彩</h1>
        <div class="betData">
          <div class="left">第<span class="blueTxt">2607280124</span>期开奖</div>
          <div class="left">
            <div><span>账户余额</span></div><div class="pt-1">88.00</div>
          </div>
        </div>
        <div class="van-count-down">00:31</div>
        <section class="van-popup Game28Follow" hidden>
          <span><i><p>大</p></i></span>
          <span><i><p>小</p></i></span>
          <span><i><p>单</p></i></span>
          <span><i><p>双</p></i></span>
          <span><i><p>质</p></i></span>
          <span><i><p>合</p></i></span>
          <input class="betInput" placeholder="可发言或输入指令下注" />
        </section>
        <button>投注</button>
      </main>`;

    const result = probePlatformDocument(document);

    expect(result.currentPeriodId).toBe("2607280124");
    expect(result.countdownMs).toBe(31_000);
    expect(result.balanceValueReadable).toBe(true);
    expect(result.contractReady).toBe(true);
  });

  it("recognizes the live NG period, balance and public bet command structure", () => {
    document.body.innerHTML = `
      <main>
        <h1>比特分分彩</h1>
        <div class="betData">
          <div class="left">
            <div>第<span class="blueTxt">2607290008</span>期开奖</div>
            <div>--</div>
          </div>
          <div class="left">
            <div><span>账户余额</span></div>
            <div class="pt-1">9.50</div>
          </div>
        </div>
        <div class="van-count-down">00:01:05</div>
        <div class="online-message">
          <div>
            <p>anonymous label</p>
            <div class="online-message-details left"><p>第1球:大:100</p></div>
          </div>
        </div>
        <div class="online-message">
          <div>
            <p>another anonymous label</p>
            <div class="online-message-details left"><p>第三球:双:20</p></div>
          </div>
        </div>
        <input class="van-field__control" placeholder="不支持手动指令下注" />
        <button>投注</button>
      </main>`;

    const result = probePlatformDocument(document);

    expect(result).toMatchObject({
      currentPeriodId: "2607290008",
      countdownMs: 65_000,
      balanceLabelVisible: true,
      balanceValueReadable: true,
      publicBetCommandCount: 2,
      directionTextCounts: {
        BIG: 1,
        SMALL: 0,
        ODD: 0,
        EVEN: 1,
        PRIME: 0,
        COMPOSITE: 0,
      },
    });
    expect(JSON.stringify(result)).not.toMatch(/9\.50|100|anonymous/);
  });

  it("merges frame counts and rejects additional or malformed fields", () => {
    const empty = probePlatformDocument(document.implementation.createHTMLDocument());
    const merged = mergePlatformPageProbes([empty, {
      ...empty,
      gameVisible: true,
      currentPeriodId: "2607280125",
      countdownMs: 15_000,
      periodCandidateCount: 1,
    }]);
    expect(merged.gameVisible).toBe(true);
    expect(merged.periodCandidateCount).toBe(1);
    expect(merged.currentPeriodId).toBe("2607280125");
    expect(merged.countdownMs).toBe(15_000);

    expect(parsePlatformPageProbe({ ...empty, secret: "forbidden" })).toBeNull();
    expect(parsePlatformPageProbe({ ...empty, publicBetCommandCount: -1 })).toBeNull();
  });
});
