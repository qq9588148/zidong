import { CAPTURE_EVENT_CHUNK_LIMIT } from "./capture-pipeline.js";
import { capturedEventSchema, type CapturedEvent } from "./contracts.js";

export const HISTORY_MESSAGE_LIMIT = 100;
const MAX_EVENTS_PER_MESSAGE = 30;
const MAX_HISTORY_PAGE_EVENTS = HISTORY_MESSAGE_LIMIT * MAX_EVENTS_PER_MESSAGE;
const MAX_HISTORY_CHUNKS = Math.ceil(
  MAX_HISTORY_PAGE_EVENTS / CAPTURE_EVENT_CHUNK_LIMIT,
);

export interface HistoryPageEnvelope {
  requestId: string;
  events: CapturedEvent[];
  messageCount: number;
  minSourceMs: number | null;
}

interface HistoryPageChunk extends HistoryPageEnvelope {
  chunkIndex: number;
  chunkCount: number;
}

function invalidHistoryPage(): never {
  throw new Error("collector_history_response_invalid");
}

function strictHistoryPageChunk(value: unknown): HistoryPageChunk {
  const row =
    value !== null && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  const requestId = row?.requestId;
  const chunkIndex = row?.chunkIndex;
  const chunkCount = row?.chunkCount;
  const messageCount = row?.messageCount;
  const minSourceMs = row?.minSourceMs;
  if (
    !row ||
    Object.keys(row).sort().join(",") !==
      "chunkCount,chunkIndex,events,messageCount,minSourceMs,requestId" ||
    typeof requestId !== "string" ||
    !/^history-[1-9]\d{0,9}$/.test(requestId) ||
    typeof chunkIndex !== "number" ||
    !Number.isSafeInteger(chunkIndex) ||
    typeof chunkCount !== "number" ||
    !Number.isSafeInteger(chunkCount) ||
    chunkCount < 1 ||
    chunkCount > MAX_HISTORY_CHUNKS ||
    chunkIndex < 0 ||
    chunkIndex >= chunkCount ||
    typeof messageCount !== "number" ||
    !Number.isSafeInteger(messageCount) ||
    messageCount < 0 ||
    messageCount > HISTORY_MESSAGE_LIMIT ||
    (messageCount === 0) !== (minSourceMs === null) ||
    (minSourceMs !== null &&
      (typeof minSourceMs !== "number" ||
        !Number.isSafeInteger(minSourceMs) ||
        minSourceMs < 0)) ||
    !Array.isArray(row.events) ||
    row.events.length > CAPTURE_EVENT_CHUNK_LIMIT
  ) {
    invalidHistoryPage();
  }
  try {
    return {
      requestId,
      chunkIndex,
      chunkCount,
      messageCount,
      minSourceMs,
      events: row.events.map((item) => capturedEventSchema.parse(item)),
    };
  } catch {
    invalidHistoryPage();
  }
}

export class HistoryPageChunkAssembler {
  private nextIndex = 0;
  private chunkCount: number | null = null;
  private messageCount: number | null = null;
  private minSourceMs: number | null | undefined;
  private readonly events: CapturedEvent[] = [];

  constructor(private readonly requestId: string) {}

  push(value: unknown): HistoryPageEnvelope | null {
    const chunk = strictHistoryPageChunk(value);
    if (
      chunk.requestId !== this.requestId ||
      chunk.chunkIndex !== this.nextIndex
    ) {
      invalidHistoryPage();
    }
    if (this.chunkCount === null) {
      this.chunkCount = chunk.chunkCount;
      this.messageCount = chunk.messageCount;
      this.minSourceMs = chunk.minSourceMs;
    } else if (
      chunk.chunkCount !== this.chunkCount ||
      chunk.messageCount !== this.messageCount ||
      chunk.minSourceMs !== this.minSourceMs
    ) {
      invalidHistoryPage();
    }

    this.events.push(...chunk.events);
    if (
      this.events.length > MAX_HISTORY_PAGE_EVENTS ||
      this.events.length > chunk.messageCount * MAX_EVENTS_PER_MESSAGE
    ) {
      invalidHistoryPage();
    }
    this.nextIndex += 1;
    if (this.nextIndex < chunk.chunkCount) return null;

    return {
      requestId: chunk.requestId,
      events: [...this.events],
      messageCount: chunk.messageCount,
      minSourceMs: chunk.minSourceMs,
    };
  }
}

export interface HistoryBoundaryState {
  crossedUncertainBoundary: boolean;
  uncertainBoundarySourceMs: number | null;
}

export class HistoryBoundaryTracker {
  private crossedUncertainBoundary = false;

  observe(
    messageCount: number,
    minSourceMs: number | null,
  ): HistoryBoundaryState {
    const uncertainBoundarySourceMs =
      messageCount === HISTORY_MESSAGE_LIMIT ? minSourceMs : null;
    const state = {
      crossedUncertainBoundary: this.crossedUncertainBoundary,
      uncertainBoundarySourceMs,
    };
    if (uncertainBoundarySourceMs !== null) {
      this.crossedUncertainBoundary = true;
    }
    return state;
  }

  reset(): void {
    this.crossedUncertainBoundary = false;
  }
}
