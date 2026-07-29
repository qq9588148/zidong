import {
  publicBetCommandElements,
  publicBetMessageFromElement,
} from "./dom-public-room.js";

const MARKER = "champion-follow-public-room-v1";

type MessageHandler = (messages: unknown[], ...args: unknown[]) => unknown;
type CallbackOptions = { onmsgs?: MessageHandler };
type Room = {
  options?: CallbackOptions;
  protocol?: { options?: CallbackOptions };
  getHistoryMsgs?: (options: Record<string, unknown>) => void;
};
type EmitMessages = (payload: {
  origin: "realtime";
  messages: unknown[];
}) => void;

export interface BtcffcPageState {
  issue: string;
  countdownMs: number;
  phase: "BETTING" | "CLOSED" | "UNKNOWN";
}

let mounted: Room | null = null;
let mountedOptions: CallbackOptions | null = null;
let mountedOriginal: MessageHandler | null = null;
let mountedWrapper: MessageHandler | null = null;

export class LiveCaptureMode {
  private mode: "UNDECIDED" | "SDK" | "DOM" = "UNDECIDED";

  claimSdk(): boolean {
    if (this.mode === "DOM") return false;
    this.mode = "SDK";
    return true;
  }

  claimDom(): boolean {
    if (this.mode === "SDK") return false;
    this.mode = "DOM";
    return true;
  }

  sdkSelected(): boolean {
    return this.mode === "SDK";
  }
}

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function readBtcffcPageState(
  stores: Iterable<unknown>,
): BtcffcPageState | null {
  for (const rawStore of stores) {
    const store = object(rawStore);
    if (!store) continue;
    const parameters = object(store.paramData);
    const candidates = [
      store.ffcInfo,
      store.gameInfo,
      store.currentGame,
      store.game28Info,
      rawStore,
    ];
    for (const rawCandidate of candidates) {
      const candidate = object(rawCandidate);
      if (!candidate) continue;
      const model = String(
        candidate.model ||
          store.model ||
          store.gameModel ||
          parameters?.model ||
          "",
      );
      const issue = String(candidate.serial ?? "");
      const countdown = Number(candidate.countdown);
      if (
        model !== "Btcffc" ||
        !/^\d{8,16}$/.test(issue) ||
        !Number.isFinite(countdown) ||
        countdown < 0
      ) {
        continue;
      }
      const process = String(candidate.process ?? "");
      return {
        issue,
        countdownMs: Math.round(countdown * 1_000),
        phase:
          process === "1"
            ? "BETTING"
            : process === "0" || process === "2"
              ? "CLOSED"
              : "UNKNOWN",
      };
    }
  }
  return null;
}

export function installRoomHook(room: Room | undefined, emit: EmitMessages): boolean {
  const callbackOptions =
    typeof room?.protocol?.options?.onmsgs === "function"
      ? room.protocol.options
      : room?.options;
  if (!room || !callbackOptions || typeof callbackOptions.onmsgs !== "function") {
    return false;
  }
  const current = callbackOptions.onmsgs;
  if (
    room === mounted &&
    callbackOptions === mountedOptions &&
    current === mountedWrapper
  ) {
    return false;
  }

  if (
    mountedOptions &&
    mountedOriginal &&
    mountedWrapper &&
    mountedOptions.onmsgs === mountedWrapper
  ) {
    mountedOptions.onmsgs = mountedOriginal;
  }

  const original = current;
  const wrapper: MessageHandler = function (this: unknown, ...args): unknown {
    let result: unknown;
    try {
      result = original.apply(this, args);
    } finally {
      const messages = Array.isArray(args[0]) ? args[0] : [];
      queueMicrotask(() => emit({ origin: "realtime", messages }));
    }
    return result;
  };
  callbackOptions.onmsgs = wrapper;
  mounted = room;
  mountedOptions = callbackOptions;
  mountedOriginal = original;
  mountedWrapper = wrapper;
  return true;
}

function emit(kind: string, payload: unknown): void {
  window.postMessage({ marker: MARKER, kind, payload }, location.origin);
}

function currentRoom(): Room | undefined {
  return (window as unknown as { chatroom?: Room }).chatroom;
}

function installCurrentRoom(onMessages: EmitMessages): void {
  installRoomHook(currentRoom(), onMessages);
}

function currentPageState(): BtcffcPageState | null {
  const root = document.querySelector("#app") as
    | (Element & {
        __vue_app__?: {
          _context?: { provides?: Record<PropertyKey, unknown> };
        };
      })
    | null;
  const providers = root?.__vue_app__?._context?.provides;
  if (!providers) return null;
  const pinia = Reflect.ownKeys(providers)
    .map((key) => providers[key])
    .find(
      (value) =>
        object(value)?._s instanceof Map,
    ) as { _s: Map<unknown, unknown> } | undefined;
  return readBtcffcPageState(pinia?._s.values() ?? []);
}

if (typeof window !== "undefined") {
  let lastState = "";
  let ticksWithoutRoom = 0;
  let domObserver: MutationObserver | null = null;
  let domNonce = 0;
  const domSeen = new WeakSet<Element>();
  const liveCaptureMode = new LiveCaptureMode();

  const stopDomFallback = (): void => {
    domObserver?.disconnect();
    domObserver = null;
  };
  const captureNewDomBets = (): void => {
    const state = currentPageState();
    if (!state) return;
    const messages = [];
    for (const element of publicBetCommandElements(document)) {
      if (domSeen.has(element)) continue;
      const message = publicBetMessageFromElement(
        element,
        state.issue,
        Date.now(),
        ++domNonce,
      );
      if (message === null) continue;
      domSeen.add(element);
      messages.push(message);
    }
    for (let offset = 0; offset < messages.length; offset += 100) {
      emit("messages", {
        origin: "realtime",
        messages: messages.slice(offset, offset + 100),
      });
    }
  };
  const startDomFallback = (): void => {
    if (domObserver !== null || !document.body ||
        !liveCaptureMode.claimDom()) return;
    for (const element of publicBetCommandElements(document)) domSeen.add(element);
    domObserver = new MutationObserver(() => queueMicrotask(captureNewDomBets));
    domObserver.observe(document.body, {
      childList: true,
      characterData: true,
      subtree: true,
    });
  };

  const emitSdkMessages: EmitMessages = (payload): void => {
    if (!liveCaptureMode.claimSdk()) return;
    stopDomFallback();
    emit("messages", payload);
  };

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.origin !== location.origin) return;
    const data = event.data as {
      marker?: unknown;
      kind?: unknown;
      timetag?: unknown;
      requestId?: unknown;
    };
    if (data.marker !== MARKER || data.kind !== "pull-history") return;
    const requestId = String(data.requestId ?? "");
    const timetag = Number(data.timetag);
    if (
      !/^history-[1-9]\d{0,9}$/.test(requestId) ||
      !Number.isSafeInteger(timetag) ||
      timetag < 0
    ) {
      emit("history-error", { requestId });
      return;
    }
    const room = currentRoom();
    if (typeof room?.getHistoryMsgs !== "function") {
      emit("history-error", { requestId });
      return;
    }
    room.getHistoryMsgs({
      timetag,
      limit: 100,
      reverse: false,
      msgTypes: ["text"],
      done(error: unknown, result: { msgs?: unknown[] } | unknown[]) {
        if (error) {
          emit("history-error", { requestId });
          return;
        }
        emit("messages", {
          origin: "history",
          requestId,
          messages: Array.isArray(result) ? result : (result.msgs ?? []),
        });
      },
    });
  });
  setInterval(() => {
    installCurrentRoom(emitSdkMessages);
    if (liveCaptureMode.sdkSelected()) {
      ticksWithoutRoom = 0;
      stopDomFallback();
    } else {
      ticksWithoutRoom += 1;
      if (ticksWithoutRoom >= 20) startDomFallback();
    }
    const state = currentPageState();
    const serialized = state ? JSON.stringify(state) : "";
    if (!state || serialized === lastState) return;
    lastState = serialized;
    emit("state", state);
  }, 100);
  installCurrentRoom(emitSdkMessages);
}
