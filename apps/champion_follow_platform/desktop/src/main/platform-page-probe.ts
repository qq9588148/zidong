import {
  platformLiveStateScript,
  readPlatformLiveState,
} from "./platform-live-state";

export type PlatformPageProbe = Readonly<{
  gameVisible: boolean;
  currentPeriodId: string | null;
  countdownMs: number | null;
  periodCandidateCount: number;
  countdownCandidateCount: number;
  directionTextCounts: Readonly<{
    BIG: number;
    SMALL: number;
    ODD: number;
    EVEN: number;
    PRIME: number;
    COMPOSITE: number;
  }>;
  balanceLabelVisible: boolean;
  balanceValueReadable: boolean;
  publicBetCommandCount: number;
  publicBetSourceAvailable: boolean;
  publicBetSourceComplete: boolean;
  stakeInputCount: number;
  betControlCount: number;
  contractReady: boolean;
}>;

const fields = [
  "balanceLabelVisible",
  "balanceValueReadable",
  "betControlCount",
  "contractReady",
  "countdownMs",
  "countdownCandidateCount",
  "currentPeriodId",
  "directionTextCounts",
  "gameVisible",
  "periodCandidateCount",
  "publicBetCommandCount",
  "publicBetSourceAvailable",
  "publicBetSourceComplete",
  "stakeInputCount",
].sort();

const directionFields = [
  "BIG", "COMPOSITE", "EVEN", "ODD", "PRIME", "SMALL",
].sort();

export function probePlatformDocument(
  document: Document,
  providedLiveState?: ReturnType<typeof readPlatformLiveState>,
): PlatformPageProbe {
  const liveState = providedLiveState ?? readPlatformLiveState(document);
  const texts: string[] = [];
  const walker = document.createTreeWalker(
    document.body ?? document.documentElement,
    NodeFilter.SHOW_TEXT,
  );
  while (walker.nextNode()) {
    const parent = walker.currentNode.parentElement;
    if (!parent || ["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE"]
      .includes(parent.tagName)) continue;
    const text = (walker.currentNode.nodeValue ?? "")
      .replace(/\s+/g, " ")
      .trim();
    if (text) texts.push(text);
  }

  const countExact = (value: string): number =>
    texts.filter((text) => text === value).length;
  const publicBetCommands = Array.from(document.querySelectorAll(
    ".online-message-details p",
  )).flatMap((element) => {
    const value = (element.textContent ?? "").replace(/\s+/g, "").trim();
    return value.match(
      /第(?:[1-5]|一|二|三|四|五)球[：:]?(?:大|小|单|双|质|合)[：:]?(?:0|[1-9]\d*)(?:\.\d{1,2})?/g,
    ) ?? [];
  });
  const countCommandDirection = (value: string): number =>
    publicBetCommands.filter((command) =>
      new RegExp(`球[：:]?${value}[：:]`).test(command)).length;
  const directionTextCounts = {
    BIG: countExact("大") + countCommandDirection("大"),
    SMALL: countExact("小") + countCommandDirection("小"),
    ODD: countExact("单") + countCommandDirection("单"),
    EVEN: countExact("双") + countCommandDirection("双"),
    PRIME: countExact("质") + countCommandDirection("质"),
    COMPOSITE: countExact("合") + countCommandDirection("合"),
  };
  const gameVisible = texts.some((text) => text.includes("比特分分彩"));
  const periodCandidates = texts.filter(
    (text) => /^\d{8,20}$/.test(text),
  );
  const periodCandidateCount = Math.max(
    periodCandidates.length,
    liveState.currentPeriodId === null ? 0 : 1,
  );
  const selectedPeriods = Array.from(document.querySelectorAll(
    ".betData .blueTxt",
  )).map((element) => (element.textContent ?? "").trim())
    .filter((value) => /^\d{8,20}$/.test(value));
  const uniquePeriods = [...new Set(
    selectedPeriods.length > 0 ? selectedPeriods :
      periodCandidates.length === 1 ? periodCandidates : [],
  )];
  const currentPeriodId = liveState.currentPeriodId ??
    (uniquePeriods.length === 1 ? uniquePeriods[0] ?? null : null);
  const countdownCandidates = texts.filter(
    (text) => /^(?:\d{1,2}:)?\d{1,2}:\d{2}$/.test(text),
  );
  const countdownCandidateCount = Math.max(
    countdownCandidates.length,
    liveState.countdownMs === null ? 0 : 1,
  );
  const selectedCountdowns = Array.from(document.querySelectorAll(
    ".van-count-down",
  )).map((element) => (element.textContent ?? "").replace(/\s+/g, "").trim())
    .filter((value) => /^(?:\d{1,2}:)?\d{1,2}:\d{2}$/.test(value));
  const countdownValues = [...new Set(
    (selectedCountdowns.length > 0 ? selectedCountdowns : countdownCandidates)
      .map((value) => {
        const parts = value.split(":").map(Number);
        const [first = 0, second = 0, third = 0] = parts;
        const seconds = parts.length === 3
          ? first * 3_600 + second * 60 + third
          : first * 60 + second;
        return seconds * 1_000;
      })
      .filter((value) => Number.isSafeInteger(value) && value >= 0),
  )];
  const countdownMs = liveState.countdownMs ??
    (countdownValues.length === 1 ? countdownValues[0] ?? null : null);
  const balanceLabelVisible = texts.some(
    (text) => text === "余额" || text.includes("账户余额"),
  );
  const balanceValueReadable = Array.from(document.querySelectorAll("*"))
    .filter((element) => element.children.length === 0 &&
      (element.textContent ?? "").replace(/\s+/g, "").trim() === "账户余额")
    .some((label) => {
      const container = label.closest(".left");
      const value = container?.querySelector(".pt-1")?.textContent
        ?.replace(/,/g, "")
        .trim();
      return typeof value === "string" &&
        /^(?:0|[1-9]\d{0,15})(?:\.\d{1,2})?$/.test(value);
    });
  const stakeInputCount = Array.from(document.querySelectorAll("input"))
    .filter((input) => {
      const type = input.type.toLowerCase();
      const placeholder = input.placeholder;
      return !input.disabled && !/不支持.*下注/.test(placeholder) &&
        (type === "number" || type === "tel" ||
         /金额|下注|投注/.test(placeholder));
    }).length;
  const betControlCount = Array.from(document.querySelectorAll(
    "button, [role='button'], input[type='button'], input[type='submit'], a",
  )).filter((element) => {
    if (element instanceof HTMLButtonElement && element.disabled) return false;
    if (element instanceof HTMLInputElement && element.disabled) return false;
    const label = element instanceof HTMLInputElement
      ? element.value
      : element.textContent ?? "";
    return /^(?:投注|确认投注|下注|确认下注)$/.test(
      label.replace(/\s+/g, "").trim(),
    );
  }).length;
  const contractReady = gameVisible &&
    currentPeriodId !== null &&
    countdownMs !== null &&
    periodCandidateCount > 0 &&
    countdownCandidateCount > 0 &&
    balanceLabelVisible &&
    balanceValueReadable &&
    betControlCount > 0;

  return Object.freeze({
    gameVisible,
    currentPeriodId,
    countdownMs,
    periodCandidateCount,
    countdownCandidateCount,
    directionTextCounts: Object.freeze(directionTextCounts),
    balanceLabelVisible,
    balanceValueReadable,
    publicBetCommandCount: liveState.publicBetCommandCount,
    publicBetSourceAvailable: liveState.publicBetSourceAvailable,
    publicBetSourceComplete: liveState.publicBetSourceComplete,
    stakeInputCount,
    betControlCount,
    contractReady,
  });
}

export function platformPageProbeScript(): string {
  return `(${probePlatformDocument.toString()})(document, ${platformLiveStateScript()})`;
}

export function parsePlatformPageProbe(value: unknown): PlatformPageProbe | null {
  if (!isObject(value) || !sameKeys(value, fields) ||
      typeof value.gameVisible !== "boolean" ||
      typeof value.balanceLabelVisible !== "boolean" ||
      typeof value.balanceValueReadable !== "boolean" ||
      typeof value.contractReady !== "boolean" ||
      typeof value.publicBetSourceAvailable !== "boolean" ||
      typeof value.publicBetSourceComplete !== "boolean" ||
      !isNullablePeriod(value.currentPeriodId) ||
      !isNullableCountdown(value.countdownMs) ||
      !isCount(value.periodCandidateCount) ||
      !isCount(value.countdownCandidateCount) ||
      !isCount(value.stakeInputCount) ||
      !isCount(value.betControlCount) ||
      !isCount(value.publicBetCommandCount) ||
      !isObject(value.directionTextCounts) ||
      !sameKeys(value.directionTextCounts, directionFields) ||
      !Object.values(value.directionTextCounts).every(isCount)) {
    return null;
  }
  return Object.freeze({
    gameVisible: value.gameVisible,
    currentPeriodId: value.currentPeriodId as string | null,
    countdownMs: value.countdownMs as number | null,
    periodCandidateCount: value.periodCandidateCount as number,
    countdownCandidateCount: value.countdownCandidateCount as number,
    directionTextCounts: Object.freeze({
      BIG: value.directionTextCounts.BIG as number,
      SMALL: value.directionTextCounts.SMALL as number,
      ODD: value.directionTextCounts.ODD as number,
      EVEN: value.directionTextCounts.EVEN as number,
      PRIME: value.directionTextCounts.PRIME as number,
      COMPOSITE: value.directionTextCounts.COMPOSITE as number,
    }),
    balanceLabelVisible: value.balanceLabelVisible,
    balanceValueReadable: value.balanceValueReadable,
    publicBetCommandCount: value.publicBetCommandCount as number,
    publicBetSourceAvailable: value.publicBetSourceAvailable,
    publicBetSourceComplete: value.publicBetSourceComplete,
    stakeInputCount: value.stakeInputCount as number,
    betControlCount: value.betControlCount as number,
    contractReady: value.contractReady,
  });
}

export function mergePlatformPageProbes(
  probes: readonly PlatformPageProbe[],
): PlatformPageProbe {
  const result = emptyProbe();
  const periods = new Set<string>();
  const countdowns = new Set<number>();
  for (const probe of probes) {
    result.gameVisible ||= probe.gameVisible;
    if (probe.currentPeriodId !== null) periods.add(probe.currentPeriodId);
    if (probe.countdownMs !== null) countdowns.add(probe.countdownMs);
    result.periodCandidateCount += probe.periodCandidateCount;
    result.countdownCandidateCount += probe.countdownCandidateCount;
    result.balanceLabelVisible ||= probe.balanceLabelVisible;
    result.balanceValueReadable ||= probe.balanceValueReadable;
    result.publicBetCommandCount += probe.publicBetCommandCount;
    result.publicBetSourceAvailable ||= probe.publicBetSourceAvailable;
    result.publicBetSourceComplete ||= probe.publicBetSourceComplete;
    result.stakeInputCount += probe.stakeInputCount;
    result.betControlCount += probe.betControlCount;
    for (const key of directionFields) {
      const direction = key as keyof PlatformPageProbe["directionTextCounts"];
      result.directionTextCounts[direction] += probe.directionTextCounts[direction];
    }
  }
  result.currentPeriodId = periods.size === 1 ? [...periods][0] ?? null : null;
  result.countdownMs = countdowns.size === 1 ? [...countdowns][0] ?? null : null;
  result.contractReady = result.gameVisible &&
    result.currentPeriodId !== null &&
    result.countdownMs !== null &&
    result.periodCandidateCount > 0 &&
    result.countdownCandidateCount > 0 &&
    result.balanceLabelVisible &&
    result.balanceValueReadable &&
    result.betControlCount > 0;
  return parsePlatformPageProbe(result)!;
}

function emptyProbe(): {
  -readonly [Key in keyof PlatformPageProbe]: Key extends "directionTextCounts"
    ? { -readonly [Direction in keyof PlatformPageProbe["directionTextCounts"]]: number }
    : PlatformPageProbe[Key];
} {
  return {
    gameVisible: false,
    currentPeriodId: null,
    countdownMs: null,
    periodCandidateCount: 0,
    countdownCandidateCount: 0,
    directionTextCounts: {
      BIG: 0, SMALL: 0, ODD: 0, EVEN: 0, PRIME: 0, COMPOSITE: 0,
    },
    balanceLabelVisible: false,
    balanceValueReadable: false,
    publicBetCommandCount: 0,
    publicBetSourceAvailable: false,
    publicBetSourceComplete: false,
    stakeInputCount: 0,
    betControlCount: 0,
    contractReady: false,
  };
}

function isCount(value: unknown): boolean {
  return Number.isSafeInteger(value) && (value as number) >= 0 &&
    (value as number) <= 10_000;
}

function isNullablePeriod(value: unknown): boolean {
  return value === null || (typeof value === "string" && /^\d{8,20}$/.test(value));
}

function isNullableCountdown(value: unknown): boolean {
  return value === null || (Number.isSafeInteger(value) &&
    (value as number) >= 0 && (value as number) <= 7 * 24 * 60 * 60 * 1_000);
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function sameKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  return actual.length === expected.length &&
    actual.every((key, index) => key === expected[index]);
}
