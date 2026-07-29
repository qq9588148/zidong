import { ipcRenderer } from "electron";

import { createFfcNormalizer } from "./bridge/ffc-normalizer.js";
import {
  chunkCaptureEvents,
  CAPTURE_MESSAGE_LIMIT,
  createFifoDispatcher,
  isSecureCollectorPage,
} from "./capture-pipeline.js";
import { capturedEventSchema, type CapturedEvent } from "./contracts.js";
import { identityFromWire } from "./identity-wire.js";

const MARKER = "champion-follow-public-room-v1";

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function issueFromMessage(value: unknown): string | null {
  const root = record(value);
  const text = record(root?.text);
  const outer = record(text?.ext);
  const payload = record(outer?.ext);
  const issue = String(payload?.serial ?? "");
  return /^\d{8,16}$/.test(issue) ? issue : null;
}

function hex(value: ArrayBuffer): string {
  return [...new Uint8Array(value)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function gapForMessage(
  raw: unknown,
  source: "realtime" | "history",
  index: number,
): Promise<CapturedEvent | null> {
  const issue = issueFromMessage(raw);
  if (issue === null) return null;
  const root = record(raw);
  const rawSourceMs = root?.time;
  const sourceMs =
    typeof rawSourceMs === "number" &&
    Number.isSafeInteger(rawSourceMs) &&
    rawSourceMs >= 0
      ? rawSourceMs
      : Date.now();
  const eventKey = hex(
    await window.crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(
        `decrypt_failure|${issue}|${sourceMs}|${index}`,
      ),
    ),
  );
  return capturedEventSchema.parse({
    kind: "CAPTURE_GAP",
    eventKey,
    issue,
    sourceMs,
    receivedAtMs: Date.now(),
    source,
    parserVersion: "btcffc-1",
    namespaceVersion: "actor-hmac-v1",
    reason: "decrypt_failure",
  });
}

async function start(): Promise<void> {
  if (!isSecureCollectorPage(location.href)) return;
  let temporaryKey: Uint8Array;
  try {
    const value: unknown = await ipcRenderer.invoke("collector:identity");
    temporaryKey = identityFromWire(value);
  } catch {
    ipcRenderer.send("collector:unsafe-state", "identity_load_failed");
    return;
  }

  let normalize: Awaited<ReturnType<typeof createFfcNormalizer>>;
  try {
    if (temporaryKey.length !== 32) {
      throw new Error("identity_key_invalid");
    }
    normalize = await createFfcNormalizer(
      temporaryKey,
      Date.now,
      window.crypto,
    );
  } catch {
    ipcRenderer.send("collector:unsafe-state", "normalizer_init_failed");
    return;
  } finally {
    temporaryKey.fill(0);
  }

  let failed = false;
  const failClosed = (reason = "page_message_invalid"): void => {
    if (failed) return;
    failed = true;
    ipcRenderer.send("collector:unsafe-state", reason);
  };

  async function handleMessage(event: MessageEvent): Promise<void> {
    if (failed) return;
    if (event.source !== window || event.origin !== location.origin) return;
    const data = record(event.data);
    if (data?.marker !== MARKER) return;
    if (data.kind === "capture-mode") {
      const payload = record(data.payload);
      const mode = payload?.mode;
      if (
        !payload ||
        Object.keys(payload).join(",") !== "mode" ||
        (mode !== "SDK" && mode !== "DOM")
      ) {
        failClosed("capture_mode_invalid");
        return;
      }
      await ipcRenderer.invoke("collector:capture-mode", { mode });
      return;
    }
    if (data.kind === "history-error") {
      const payload = record(data.payload);
      const requestId = String(payload?.requestId ?? "");
      if (/^history-[1-9]\d{0,9}$/.test(requestId)) {
        ipcRenderer.send("collector:history-error", requestId);
      }
      return;
    }
    if (data.kind === "state") {
      const payload = record(data.payload);
      const issue = payload?.issue;
      const countdownMs = payload?.countdownMs;
      const phase = payload?.phase;
      if (
        typeof issue !== "string" ||
        !/^\d{8,16}$/.test(issue) ||
        typeof countdownMs !== "number" ||
        !Number.isSafeInteger(countdownMs) ||
        countdownMs < 0 ||
        (phase !== "BETTING" && phase !== "CLOSED" && phase !== "UNKNOWN")
      ) {
        failClosed("page_state_invalid");
        return;
      }
      await ipcRenderer.invoke("collector:state", {
        issue,
        countdownMs,
        phase,
        observedAtMs: Date.now(),
      });
      return;
    }
    if (data.kind !== "messages") return;
    const payload = record(data.payload);
    const source = payload?.origin;
    const messages = payload?.messages;
    const requestId = payload?.requestId;
    if (
      (source !== "realtime" && source !== "history") ||
      !Array.isArray(messages) ||
      messages.length > CAPTURE_MESSAGE_LIMIT
    ) {
      failClosed("page_message_invalid");
      return;
    }

    const historySourceTimes: number[] = [];
    if (source === "history") {
      for (const message of messages) {
        const value = record(message)?.time;
        if (
          typeof value !== "number" ||
          !Number.isSafeInteger(value) ||
          value < 0
        ) {
          failClosed("history_time_invalid");
          return;
        }
        historySourceTimes.push(value);
      }
    }

    const strictEvents: CapturedEvent[] = [];
    for (const [index, raw] of messages.entries()) {
      try {
        strictEvents.push(...(await normalize(raw, source)));
      } catch {
        const gap = await gapForMessage(raw, source, index);
        if (gap === null) {
          failClosed("decrypt_failure_unattributed");
          return;
        }
        strictEvents.push(gap);
      }
    }
    if (source === "history") {
      if (
        typeof requestId !== "string" ||
        !/^history-[1-9]\d{0,9}$/.test(requestId)
      ) {
        failClosed("history_request_invalid");
        return;
      }
      const chunks = chunkCaptureEvents(strictEvents);
      const pageChunks = chunks.length > 0 ? chunks : [[]];
      for (const [chunkIndex, events] of pageChunks.entries()) {
        await ipcRenderer.invoke("collector:history-page", {
          requestId,
          chunkIndex,
          chunkCount: pageChunks.length,
          events,
          messageCount: messages.length,
          minSourceMs: historySourceTimes.length
            ? Math.min(...historySourceTimes)
            : null,
        });
      }
    } else if (strictEvents.length > 0) {
      for (const events of chunkCaptureEvents(strictEvents)) {
        await ipcRenderer.invoke("collector:append", events);
      }
    }
  }

  const dispatch = createFifoDispatcher(
    handleMessage,
    () => failClosed("page_dispatch_failed"),
  );
  window.addEventListener("message", (event) => {
    void dispatch(event);
  });
}

void start();
