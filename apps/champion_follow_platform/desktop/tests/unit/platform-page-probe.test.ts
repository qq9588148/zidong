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
