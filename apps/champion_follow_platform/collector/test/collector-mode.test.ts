import { describe, expect, it } from "vitest";

import {
  LocalCollectorServer,
  collectorWindowTitle,
  resolveCollectorConfig,
} from "../src/collector-mode.js";

describe("collector startup mode", () => {
  it("defaults the packaged main collector to the deployed server", () => {
    expect(resolveCollectorConfig({})).toEqual({
      mode: "server",
      platformUrl: "https://ng888.com/",
      serverUrl: "https://101.37.172.66:8443/",
    });
    expect(
      resolveCollectorConfig({
        CHAMPION_COLLECTOR_MODE: "local",
        CHAMPION_PLATFORM_URL: "https://example.test/room",
      }),
    ).toEqual({
      mode: "local",
      platformUrl: "https://example.test/room",
      serverUrl: null,
    });
  });

  it("allows safe explicit HTTPS endpoint overrides", () => {
    expect(() =>
      resolveCollectorConfig({
        CHAMPION_COLLECTOR_MODE: "server",
        CHAMPION_PLATFORM_URL: "https://example.test/room",
        CHAMPION_COLLECTOR_SERVER_URL: "http://collector.test/",
      }),
    ).toThrow("collector_config_invalid");
    expect(
      resolveCollectorConfig({
        CHAMPION_COLLECTOR_MODE: "server",
        CHAMPION_PLATFORM_URL: "https://example.test/room",
        CHAMPION_COLLECTOR_SERVER_URL: "https://collector.test/",
      }),
    ).toEqual({
      mode: "server",
      platformUrl: "https://example.test/room",
      serverUrl: "https://collector.test/",
    });
  });

  it("keeps local events pending until a real server acknowledges them", async () => {
    const server = new LocalCollectorServer({
      acknowledgedSeq: 0,
      acknowledgedEventKey: null,
    });

    await expect(
      server.session({
        collector_id: "collector-main-local",
        namespace_version: "actor-hmac-v1",
      }),
    ).resolves.toEqual({
      ack_seq: 0,
      ack_event_key: null,
      history_anchor_event_key: null,
      namespace_empty: true,
    });
    await expect(
      server.append({
        collector_id: "collector-main-local",
        namespace_version: "actor-hmac-v1",
        from_seq: 1,
        to_seq: 1,
        records: [],
      }),
    ).rejects.toThrow("collector_offline");
    await expect(
      server.heartbeat({
        collector_id: "collector-main-local",
        issue: null,
        phase: "UNKNOWN",
        countdown_ms: 0,
        observed_at_ms: 0,
        last_journal_seq: 0,
        capture_healthy: true,
      }),
    ).resolves.toBeUndefined();
  });

  it("formats a non-technical collection status title", () => {
    expect(
      collectorWindowTitle({ healthy: true, issue: "2607280001", saved: 128 }),
    ).toBe("NG 主采集运行中 · 已保存 128 条 · 第 2607280001 期");
    expect(
      collectorWindowTitle({ healthy: false, issue: null, saved: 128 }),
    ).toBe("NG 主采集已暂停 · 请检查页面或磁盘");
  });
});
