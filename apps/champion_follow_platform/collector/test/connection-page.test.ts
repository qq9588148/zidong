import { describe, expect, it } from "vitest";

import { connectionPageUrl } from "../src/connection-page.js";

function pageText(attempt: number): string {
  const prefix = "data:text/html;charset=utf-8,";
  const value = connectionPageUrl(attempt);
  expect(value.startsWith(prefix)).toBe(true);
  return decodeURIComponent(value.slice(prefix.length));
}

describe("collector connection page", () => {
  it("shows a visible non-technical retry state without remote assets", () => {
    const html = pageText(3);

    expect(html).toContain("正在连接 NG");
    expect(html).toContain("第 3 次自动重试");
    expect(html).toContain("登录状态和已采集数据都会保留");
    expect(html).not.toMatch(/<script|https?:\/\//i);
  });

  it("rejects invalid retry counters", () => {
    expect(() => connectionPageUrl(-1)).toThrow(
      "collector_retry_count_invalid",
    );
    expect(() => connectionPageUrl(1.5)).toThrow(
      "collector_retry_count_invalid",
    );
  });
});
