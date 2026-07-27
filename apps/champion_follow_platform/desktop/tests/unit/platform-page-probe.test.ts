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
        <span>2607280123</span><span>00:42</span><span>账户余额</span>
        <section>
          <button>大</button><span>1.96</span><button>小</button><span>1.96</span>
          <button>单</button><span>1.96</span><button>双</button><span>1.96</span>
          <button>质</button><span>1.96</span><button>合</button><span>1.96</span>
        </section>
        <input type="number" placeholder="请输入投注金额" />
        <button>投注</button>
      </main>`;

    const result = probePlatformDocument(document);
    expect(result).toMatchObject({
      gameVisible: true,
      periodCandidateCount: 1,
      countdownCandidateCount: 1,
      odds196Count: 6,
      balanceLabelVisible: true,
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
    expect(JSON.stringify(result)).not.toMatch(/2607280123|账户余额|投注金额/);
  });

  it("recognizes the platform ratio notation used by the hidden bet sheet", () => {
    document.body.innerHTML = `
      <main>
        <h1>比特分分彩</h1>
        <span>2607280124</span><span>00:31</span><span>账户余额</span>
        <section class="van-popup Game28Follow" hidden>
          <span><i><p>大</p></i><i>1:1.96</i></span>
          <span><i><p>小</p></i><i>1:1.96</i></span>
          <span><i><p>单</p></i><i>1:1.96</i></span>
          <span><i><p>双</p></i><i>1:1.96</i></span>
          <span><i><p>质</p></i><i>1:1.96</i></span>
          <span><i><p>合</p></i><i>1:1.96</i></span>
          <input class="betInput" placeholder="可发言或输入指令下注" />
        </section>
        <button>投注</button>
      </main>`;

    const result = probePlatformDocument(document);

    expect(result.odds196Count).toBe(6);
    expect(result.contractReady).toBe(true);
  });

  it("merges frame counts and rejects additional or malformed fields", () => {
    const empty = probePlatformDocument(document.implementation.createHTMLDocument());
    const merged = mergePlatformPageProbes([empty, {
      ...empty,
      gameVisible: true,
      periodCandidateCount: 1,
    }]);
    expect(merged.gameVisible).toBe(true);
    expect(merged.periodCandidateCount).toBe(1);

    expect(parsePlatformPageProbe({ ...empty, secret: "forbidden" })).toBeNull();
    expect(parsePlatformPageProbe({ ...empty, odds196Count: -1 })).toBeNull();
  });
});
