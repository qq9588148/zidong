export type PlatformLiveState = Readonly<{
  currentPeriodId: string | null;
  countdownMs: number | null;
  phase: "OPEN" | "CLOSED" | "RESULT" | "UNKNOWN";
  publicBetCommandCount: number;
  publicBetSourceAvailable: boolean;
  publicBetSourceComplete: boolean;
}>;

export function readPlatformLiveState(document: Document): PlatformLiveState {
  const object = (value: unknown): Record<string, any> | null =>
    value !== null && typeof value === "object" && !Array.isArray(value)
      ? value as Record<string, any>
      : null;
  const compact = (value: unknown): string =>
    String(value ?? "").replace(/\s+/g, "").trim();
  const period = (value: unknown): string | null => {
    const candidate = String(value ?? "");
    return /^\d{8,20}$/.test(candidate) ? candidate : null;
  };
  const countdownTextToMs = (value: string): number | null => {
    if (!/^(?:\d{1,2}:)?\d{1,2}:\d{2}$/.test(value)) return null;
    const parts = value.split(":").map(Number);
    const milliseconds = (parts.length === 3
      ? (parts[0] ?? 0) * 3_600 + (parts[1] ?? 0) * 60 + (parts[2] ?? 0)
      : (parts[0] ?? 0) * 60 + (parts[1] ?? 0)) * 1_000;
    return Number.isSafeInteger(milliseconds) && milliseconds >= 0
      ? milliseconds
      : null;
  };

  let currentPeriodId: string | null = null;
  let countdownMs: number | null = null;
  let phase: PlatformLiveState["phase"] = "UNKNOWN";
  const appRoot = document.querySelector("#app") as
    | (Element & {
        __vue_app__?: {
          _context?: { provides?: Record<PropertyKey, unknown> };
        };
      })
    | null;
  const providers = appRoot?.__vue_app__?._context?.provides;
  const pinia = providers
    ? Reflect.ownKeys(providers)
        .map((key) => providers[key])
        .find((value) => object(value)?._s instanceof Map) as
          | { _s: Map<unknown, unknown> }
          | undefined
    : undefined;
  for (const rawStore of pinia?._s.values() ?? []) {
    const store = object(rawStore);
    if (!store) continue;
    const parameters = object(store.paramData);
    for (const rawCandidate of [
      store.ffcInfo,
      store.gameInfo,
      store.currentGame,
      store.game28Info,
      rawStore,
    ]) {
      const candidate = object(rawCandidate);
      if (!candidate) continue;
      const model = String(
        candidate.model ||
          store.model ||
          store.gameModel ||
          parameters?.model ||
          "",
      );
      const issue = period(candidate.serial);
      const countdown = Number(candidate.countdown);
      if (
        model !== "Btcffc" ||
        issue === null ||
        !Number.isFinite(countdown) ||
        countdown < 0
      ) {
        continue;
      }
      currentPeriodId = issue;
      countdownMs = Math.round(countdown * 1_000);
      const process = String(candidate.process ?? "");
      phase = process === "1"
        ? "OPEN"
        : process === "0" || process === "2"
          ? "CLOSED"
          : "UNKNOWN";
      break;
    }
    if (currentPeriodId !== null) break;
  }

  const visiblePeriods = Array.from(document.querySelectorAll(
    ".betData .blueTxt",
  )).map((element) => period(compact(element.textContent)))
    .filter((value): value is string => value !== null);
  if (currentPeriodId === null) {
    const uniquePeriods = [...new Set(visiblePeriods)];
    currentPeriodId = uniquePeriods.length === 1
      ? uniquePeriods[0] ?? null
      : null;
  }
  if (countdownMs === null) {
    const visibleCountdowns = Array.from(document.querySelectorAll(
      ".van-count-down",
    )).map((element) => countdownTextToMs(compact(element.textContent)))
      .filter((value): value is number => value !== null);
    const uniqueCountdowns = [...new Set(visibleCountdowns)];
    countdownMs = uniqueCountdowns.length === 1
      ? uniqueCountdowns[0] ?? null
      : null;
  }
  if (phase === "UNKNOWN") {
    const text = document.body?.innerText ?? "";
    const betButton = Array.from(document.querySelectorAll("button"))
      .find((element) => compact(element.textContent) === "投注");
    phase = betButton && !(betButton as HTMLButtonElement).disabled &&
        countdownMs !== null && countdownMs > 0
      ? "OPEN"
      : /封盘开奖中|封盤|已封盘/.test(text)
        ? "CLOSED"
        : "RESULT";
  }

  const host = document.defaultView as
    | (Window & typeof globalThis & Record<string, any>)
    | null;
  const stateKey = "__championFollowSourceCounterV1";
  const room = object(host?.chatroom);
  const callbackOptions = typeof room?.protocol?.options?.onmsgs === "function"
    ? room.protocol.options
    : typeof room?.options?.onmsgs === "function"
      ? room.options
      : null;
  let state = object(host?.[stateKey]);
  if (host && (!state || state.room !== room)) {
    if (state && state.options?.onmsgs === state.wrapper &&
        typeof state.original === "function") {
      state.options.onmsgs = state.original;
    }
    state = {
      room,
      options: null,
      original: null,
      wrapper: null,
      issue: null,
      count: 0,
      complete: false,
      loading: false,
      generation: 0,
      seen: new Set<string>(),
      domSeen: new WeakMap<Element, string>(),
    };
    host[stateKey] = state;
  }

  const supportedItems = (payload: Record<string, any>): Array<{
    itemIndex: number;
    title: string;
    money: string;
  }> => {
    const values = Array.isArray(payload.items) ? payload.items : [];
    const result: Array<{ itemIndex: number; title: string; money: string }> = [];
    let itemIndex = 0;
    for (const rawValue of values) {
      const value = object(rawValue);
      const flatTitle = compact(value?.title);
      const flatMoney = compact(value?.money);
      const flatIndex = itemIndex++;
      if (
        /^猜双面-第[一二三四五]球_[大小单双质合]$/.test(flatTitle) &&
        /^(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(flatMoney)
      ) {
        result.push({ itemIndex: flatIndex, title: flatTitle, money: flatMoney });
        continue;
      }
      const groupTitle = compact(value?.title);
      const nested = Array.isArray(value?.items) ? value.items : [];
      for (const rawItem of nested) {
        const currentIndex = itemIndex++;
        const item = object(rawItem);
        const side = compact(item?.title);
        const money = compact(item?.money);
        if (
          /^第[一二三四五]球$/.test(groupTitle) &&
          /^[大小单双质合]$/.test(side) &&
          /^(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(money)
        ) {
          result.push({
            itemIndex: currentIndex,
            title: `${groupTitle}_${side}`,
            money,
          });
        }
      }
    }
    return result;
  };

  const ingest = (messages: unknown[], expectedIssue: string): boolean => {
    if (!state || state.issue !== expectedIssue) return false;
    let sawOlderIssue = false;
    for (const rawMessage of messages) {
      const message = object(rawMessage);
      const outer = object(object(message?.text)?.ext);
      const payload = object(outer?.ext);
      if (!payload || String(payload.type ?? "") !== "1") continue;
      const messageIssue = period(payload.serial);
      const items = supportedItems(payload);
      if (items.length === 0 || messageIssue === null) continue;
      if (messageIssue !== expectedIssue) {
        sawOlderIssue = true;
        continue;
      }
      const actor = compact(payload.at || outer?.uid);
      const messageId = compact(message?.idClient);
      const sourceTime = Number(message?.time);
      for (const item of items) {
        const key = messageId
          ? `id|${messageId}|${item.itemIndex}`
          : [
              "fallback",
              Number.isSafeInteger(sourceTime) ? sourceTime : "",
              actor,
              expectedIssue,
              item.title,
              item.money,
              item.itemIndex,
            ].join("|");
        if (state.seen.has(key)) continue;
        state.seen.add(key);
        state.count += 1;
      }
    }
    return sawOlderIssue;
  };

  if (state && currentPeriodId !== null && state.issue !== currentPeriodId) {
    state.issue = currentPeriodId;
    state.count = 0;
    state.complete = false;
    state.loading = false;
    state.generation += 1;
    state.seen = new Set<string>();
    state.domSeen = new WeakMap<Element, string>();
  }

  if (state && room && callbackOptions &&
      (state.options !== callbackOptions ||
       callbackOptions.onmsgs !== state.wrapper)) {
    if (state.options?.onmsgs === state.wrapper &&
        typeof state.original === "function") {
      state.options.onmsgs = state.original;
    }
    const original = callbackOptions.onmsgs;
    const wrapper = function (this: unknown, ...args: unknown[]): unknown {
      let result: unknown;
      try {
        result = original.apply(this, args);
      } finally {
        const messages = Array.isArray(args[0]) ? args[0] : [];
        const expectedIssue = String(state?.issue ?? "");
        queueMicrotask(() => ingest(messages, expectedIssue));
      }
      return result;
    };
    state.options = callbackOptions;
    state.original = original;
    state.wrapper = wrapper;
    callbackOptions.onmsgs = wrapper;
  }

  if (
    state &&
    room &&
    currentPeriodId !== null &&
    typeof room.getHistoryMsgs === "function" &&
    typeof state.original === "function" &&
    !state.complete &&
    !state.loading
  ) {
    state.loading = true;
    const expectedIssue = currentPeriodId;
    const generation = state.generation;
    void (async () => {
      let cursor = Date.now() + 1;
      try {
        for (let page = 0; page < 20; page += 1) {
          const messages = await new Promise<unknown[]>((resolve, reject) => {
            const timer = setTimeout(() => reject(new Error("history_timeout")), 3_000);
            room.getHistoryMsgs({
              timetag: cursor,
              limit: 100,
              reverse: false,
              msgTypes: ["text"],
              done(error: unknown, value: { msgs?: unknown[] } | unknown[]) {
                clearTimeout(timer);
                if (error) {
                  reject(new Error("history_failed"));
                  return;
                }
                resolve(Array.isArray(value) ? value : value?.msgs ?? []);
              },
            });
          });
          if (state.generation !== generation || state.issue !== expectedIssue) return;
          if (messages.some((message) => typeof object(message)?.text === "string")) {
            state.original.call(room, messages);
          }
          const sawOlderIssue = ingest(messages, expectedIssue);
          const sourceTimes = messages.map((message) => Number(object(message)?.time))
            .filter((value) => Number.isSafeInteger(value) && value >= 0);
          if (messages.length < 100 || sawOlderIssue || sourceTimes.length === 0) {
            state.complete = true;
            return;
          }
          const nextCursor = Math.min(...sourceTimes) - 1;
          if (nextCursor < 0 || nextCursor >= cursor) throw new Error("history_stalled");
          cursor = nextCursor;
        }
        state.complete = false;
      } catch {
        state.complete = false;
      } finally {
        if (state.generation === generation) state.loading = false;
      }
    })();
  }

  const publicBetSourceAvailable = !!(
    state && room && callbackOptions && typeof room.getHistoryMsgs === "function"
  );
  if (state && currentPeriodId !== null && !publicBetSourceAvailable) {
    const elements = Array.from(document.querySelectorAll(
      ".online-message-details p",
    ));
    for (const element of elements) {
      const command = compact(element.textContent);
      const signature = /^((?:猜双面[:：])?第[1-5一二三四五]球[:：]?[大小单双质合][:：]?(?:0|[1-9]\d*)(?:\.\d{1,2})?)$/.test(command)
        ? command
        : "";
      if (!signature || state.domSeen.get(element) === signature) continue;
      state.domSeen.set(element, signature);
      state.count += 1;
    }
    state.complete = true;
  }

  const publicBetCommandCount = typeof state?.count === "number" &&
      Number.isSafeInteger(state.count) && state.count >= 0
    ? state.count
    : 0;
  return Object.freeze({
    currentPeriodId,
    countdownMs,
    phase,
    publicBetCommandCount,
    publicBetSourceAvailable,
    publicBetSourceComplete: publicBetSourceAvailable
      ? state?.complete === true
      : true,
  });
}

export function platformLiveStateScript(): string {
  return `(${readPlatformLiveState.toString()})(document)`;
}
