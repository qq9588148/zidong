export type PlatformPageProbe = Readonly<{
  gameVisible: boolean;
  periodCandidateCount: number;
  countdownCandidateCount: number;
  odds196Count: number;
  directionTextCounts: Readonly<{
    BIG: number;
    SMALL: number;
    ODD: number;
    EVEN: number;
    PRIME: number;
    COMPOSITE: number;
  }>;
  balanceLabelVisible: boolean;
  stakeInputCount: number;
  betControlCount: number;
  contractReady: boolean;
}>;

const fields = [
  "balanceLabelVisible",
  "betControlCount",
  "contractReady",
  "countdownCandidateCount",
  "directionTextCounts",
  "gameVisible",
  "odds196Count",
  "periodCandidateCount",
  "stakeInputCount",
].sort();

const directionFields = [
  "BIG", "COMPOSITE", "EVEN", "ODD", "PRIME", "SMALL",
].sort();

export function probePlatformDocument(document: Document): PlatformPageProbe {
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
  const directionTextCounts = {
    BIG: countExact("大"),
    SMALL: countExact("小"),
    ODD: countExact("单"),
    EVEN: countExact("双"),
    PRIME: countExact("质"),
    COMPOSITE: countExact("合"),
  };
  const gameVisible = texts.some((text) => text.includes("比特分分彩"));
  const periodCandidateCount = texts.filter(
    (text) => /^\d{8,20}$/.test(text),
  ).length;
  const countdownCandidateCount = texts.filter(
    (text) => /^(?:\d{1,2}:)?\d{1,2}:\d{2}$/.test(text),
  ).length;
  const odds196Count = texts.filter(
    (text) => /^(?:1\.96|1\.960+)$/.test(text),
  ).length;
  const balanceLabelVisible = texts.some(
    (text) => text === "余额" || text.includes("账户余额"),
  );
  const stakeInputCount = Array.from(document.querySelectorAll("input"))
    .filter((input) => {
      const type = input.type.toLowerCase();
      const placeholder = input.placeholder;
      return type === "number" || type === "tel" ||
        /金额|下注|投注/.test(placeholder);
    }).length;
  const betControlCount = Array.from(document.querySelectorAll(
    "button, [role='button'], input[type='button'], input[type='submit'], a",
  )).filter((element) => {
    const label = element instanceof HTMLInputElement
      ? element.value
      : element.textContent ?? "";
    return /^(?:投注|确认投注|下注|确认下注)$/.test(
      label.replace(/\s+/g, "").trim(),
    );
  }).length;
  const contractReady = gameVisible &&
    periodCandidateCount > 0 &&
    countdownCandidateCount > 0 &&
    odds196Count >= 6 &&
    Object.values(directionTextCounts).every((count) => count > 0) &&
    balanceLabelVisible &&
    stakeInputCount > 0 &&
    betControlCount > 0;

  return Object.freeze({
    gameVisible,
    periodCandidateCount,
    countdownCandidateCount,
    odds196Count,
    directionTextCounts: Object.freeze(directionTextCounts),
    balanceLabelVisible,
    stakeInputCount,
    betControlCount,
    contractReady,
  });
}

export function platformPageProbeScript(): string {
  return `(${probePlatformDocument.toString()})(document)`;
}

export function parsePlatformPageProbe(value: unknown): PlatformPageProbe | null {
  if (!isObject(value) || !sameKeys(value, fields) ||
      typeof value.gameVisible !== "boolean" ||
      typeof value.balanceLabelVisible !== "boolean" ||
      typeof value.contractReady !== "boolean" ||
      !isCount(value.periodCandidateCount) ||
      !isCount(value.countdownCandidateCount) ||
      !isCount(value.odds196Count) ||
      !isCount(value.stakeInputCount) ||
      !isCount(value.betControlCount) ||
      !isObject(value.directionTextCounts) ||
      !sameKeys(value.directionTextCounts, directionFields) ||
      !Object.values(value.directionTextCounts).every(isCount)) {
    return null;
  }
  return Object.freeze({
    gameVisible: value.gameVisible,
    periodCandidateCount: value.periodCandidateCount as number,
    countdownCandidateCount: value.countdownCandidateCount as number,
    odds196Count: value.odds196Count as number,
    directionTextCounts: Object.freeze({
      BIG: value.directionTextCounts.BIG as number,
      SMALL: value.directionTextCounts.SMALL as number,
      ODD: value.directionTextCounts.ODD as number,
      EVEN: value.directionTextCounts.EVEN as number,
      PRIME: value.directionTextCounts.PRIME as number,
      COMPOSITE: value.directionTextCounts.COMPOSITE as number,
    }),
    balanceLabelVisible: value.balanceLabelVisible,
    stakeInputCount: value.stakeInputCount as number,
    betControlCount: value.betControlCount as number,
    contractReady: value.contractReady,
  });
}

export function mergePlatformPageProbes(
  probes: readonly PlatformPageProbe[],
): PlatformPageProbe {
  const result = emptyProbe();
  for (const probe of probes) {
    result.gameVisible ||= probe.gameVisible;
    result.periodCandidateCount += probe.periodCandidateCount;
    result.countdownCandidateCount += probe.countdownCandidateCount;
    result.odds196Count += probe.odds196Count;
    result.balanceLabelVisible ||= probe.balanceLabelVisible;
    result.stakeInputCount += probe.stakeInputCount;
    result.betControlCount += probe.betControlCount;
    for (const key of directionFields) {
      const direction = key as keyof PlatformPageProbe["directionTextCounts"];
      result.directionTextCounts[direction] += probe.directionTextCounts[direction];
    }
  }
  result.contractReady = result.gameVisible &&
    result.periodCandidateCount > 0 &&
    result.countdownCandidateCount > 0 &&
    result.odds196Count >= 6 &&
    Object.values(result.directionTextCounts).every((count) => count > 0) &&
    result.balanceLabelVisible &&
    result.stakeInputCount > 0 &&
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
    periodCandidateCount: 0,
    countdownCandidateCount: 0,
    odds196Count: 0,
    directionTextCounts: {
      BIG: 0, SMALL: 0, ODD: 0, EVEN: 0, PRIME: 0, COMPOSITE: 0,
    },
    balanceLabelVisible: false,
    stakeInputCount: 0,
    betControlCount: 0,
    contractReady: false,
  };
}

function isCount(value: unknown): boolean {
  return Number.isSafeInteger(value) && (value as number) >= 0 &&
    (value as number) <= 10_000;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function sameKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  return actual.length === expected.length &&
    actual.every((key, index) => key === expected[index]);
}
