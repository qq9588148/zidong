import {
  ackSchema,
  eventBatchSchema,
  heartbeatSchema,
  sessionResponseSchema,
  type Heartbeat,
  type JournalRecord,
} from "./contracts.js";

export interface CollectorSessionValue {
  ack_seq: number;
  ack_event_key: string | null;
  history_anchor_event_key: string | null;
  namespace_empty: boolean;
}

export interface CollectorServerPort {
  session(request: {
    collector_id: string;
    namespace_version: "actor-hmac-v1";
  }): Promise<CollectorSessionValue>;
  append(request: {
    collector_id: string;
    namespace_version: "actor-hmac-v1";
    from_seq: number;
    to_seq: number;
    records: JournalRecord[];
  }): Promise<{ ack_seq: number }>;
  heartbeat(value: Heartbeat): Promise<void>;
}

function serverError(): Error {
  return new Error("collector_server_error");
}

export class HttpCollectorServer implements CollectorServerPort {
  private readonly root: URL;

  constructor(
    baseUrl: string,
    private readonly bearer: string,
    private readonly fetchImpl: typeof fetch = fetch,
    private readonly observeStatus: (
      status: string,
      operation: "session" | "events" | "heartbeat",
    ) => void = () => undefined,
  ) {
    try {
      this.root = new URL(baseUrl);
    } catch {
      throw new Error("collector_server_https_required");
    }
    if (this.root.protocol !== "https:") {
      throw new Error("collector_server_https_required");
    }
  }

  async session(request: {
    collector_id: string;
    namespace_version: "actor-hmac-v1";
  }): Promise<CollectorSessionValue> {
    const response = await this.call(
      "/v1/collector/session",
      request,
      "session",
    );
    const parsed = sessionResponseSchema.safeParse(response);
    if (!parsed.success) throw serverError();
    return parsed.data;
  }

  async append(
    request: Parameters<CollectorServerPort["append"]>[0],
  ): Promise<{ ack_seq: number }> {
    const parsedRequest = eventBatchSchema.safeParse(request);
    if (!parsedRequest.success) throw serverError();
    const response = await this.call(
      "/v1/collector/events",
      parsedRequest.data,
      "events",
    );
    const parsedResponse = ackSchema.safeParse(response);
    if (!parsedResponse.success) throw serverError();
    return parsedResponse.data;
  }

  async heartbeat(value: Heartbeat): Promise<void> {
    const parsed = heartbeatSchema.safeParse(value);
    if (!parsed.success) throw serverError();
    await this.call("/v1/collector/heartbeat", parsed.data, "heartbeat");
  }

  private async call(
    path: string,
    body: unknown,
    operation: "session" | "events" | "heartbeat",
  ): Promise<unknown> {
    let response: Response;
    try {
      response = await this.fetchImpl(new URL(path, this.root), {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${this.bearer}`,
        },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(
          path === "/v1/collector/heartbeat" ? 750 : 5_000,
        ),
      });
    } catch {
      this.observeStatus("network_error", operation);
      throw new Error("collector_network_error");
    }
    this.observeStatus(
      response.ok ? "ok" : `http_${response.status}`,
      operation,
    );
    if (response.status === 401 || response.status === 403) {
      throw new Error("collector_auth_rejected");
    }
    if (response.status === 409) {
      throw new Error("collector_sequence_conflict");
    }
    if (!response.ok) throw serverError();
    if (response.status === 204) return undefined;
    try {
      return await response.json();
    } catch {
      throw serverError();
    }
  }
}
