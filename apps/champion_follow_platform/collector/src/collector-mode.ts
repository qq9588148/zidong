import { heartbeatSchema } from "./contracts.js";
import type {
  CollectorServerPort,
  CollectorSessionValue,
} from "./server-api.js";

export type CollectorMode = "local" | "server";

export interface CollectorConfig {
  mode: CollectorMode;
  platformUrl: string;
  serverUrl: string | null;
}

type Environment = Readonly<Record<string, string | undefined>>;

function httpsUrl(value: string | undefined): string {
  try {
    const parsed = new URL(value ?? "");
    if (parsed.protocol !== "https:") throw new Error();
    return parsed.toString();
  } catch {
    throw new Error("collector_config_invalid");
  }
}

export function resolveCollectorConfig(
  environment: Environment,
): CollectorConfig {
  const configuredMode = environment.CHAMPION_COLLECTOR_MODE?.trim();
  const mode = configuredMode === undefined || configuredMode === "" ||
      configuredMode === "server"
      ? "server"
      : configuredMode === "local"
        ? "local"
      : null;
  if (mode === null) throw new Error("collector_config_invalid");

  const platformUrl = httpsUrl(
    environment.CHAMPION_PLATFORM_URL ??
      "https://ng888.com/",
  );
  const serverUrl = mode === "server"
    ? httpsUrl(
        environment.CHAMPION_COLLECTOR_SERVER_URL ??
          "https://101.37.172.66:8443/",
      )
    : null;
  return Object.freeze({ mode, platformUrl, serverUrl });
}

interface LocalCursor {
  readonly acknowledgedSeq: number;
  readonly acknowledgedEventKey: string | null;
}

export class LocalCollectorServer implements CollectorServerPort {
  constructor(private readonly cursor: LocalCursor) {}

  async session(
    _request: Parameters<CollectorServerPort["session"]>[0],
  ): Promise<CollectorSessionValue> {
    const ackSeq = this.cursor.acknowledgedSeq;
    const ackEventKey = this.cursor.acknowledgedEventKey;
    if ((ackSeq === 0) !== (ackEventKey === null)) {
      throw new Error("collector_sequence_conflict");
    }
    return {
      ack_seq: ackSeq,
      ack_event_key: ackEventKey,
      history_anchor_event_key: ackEventKey,
      namespace_empty: ackSeq === 0,
    };
  }

  async append(
    _request: Parameters<CollectorServerPort["append"]>[0],
  ): Promise<{ ack_seq: number }> {
    throw new Error("collector_offline");
  }

  async heartbeat(
    value: Parameters<CollectorServerPort["heartbeat"]>[0],
  ): Promise<void> {
    heartbeatSchema.parse(value);
  }
}

export function collectorWindowTitle(value: {
  healthy: boolean;
  issue: string | null;
  saved: number;
}): string {
  if (!value.healthy) return "NG 主采集已暂停 · 请检查页面或磁盘";
  const issue = value.issue ? ` · 第 ${value.issue} 期` : " · 等待进入游戏";
  return `NG 主采集运行中 · 已保存 ${value.saved} 条${issue}`;
}
