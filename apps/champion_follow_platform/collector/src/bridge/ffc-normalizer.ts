import { canonicalJson } from "../canonical-json.js";
import {
  capturedEventSchema,
  type CapturedEvent,
} from "../contracts.js";

type Source = "realtime" | "history";
type UnknownRecord = Record<string, unknown>;

const POSITIONS: Record<string, number> = {
  第一球: 1,
  第二球: 2,
  第三球: 3,
  第四球: 4,
  第五球: 5,
};
const SIDES = new Set(["大", "小", "单", "双", "质", "合"]);
const FLAT_DOUBLE_PATTERN = /^猜双面-(第[一二三四五]球)_([大小单双质合])$/;

function record(value: unknown): UnknownRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as UnknownRecord)
    : null;
}

function sourceMilliseconds(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value
    : null;
}

function toMinor(value: unknown): string | null {
  const match = String(value ?? "").match(/^(0|[1-9]\d*)(?:\.(\d{1,2}))?$/);
  if (!match) return null;
  const minor =
    BigInt(match[1] ?? "0") * 100n +
    BigInt((match[2] ?? "").padEnd(2, "0") || "0");
  return minor > 0n ? minor.toString() : null;
}

function hex(value: ArrayBuffer): string {
  return [...new Uint8Array(value)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function normalizedItems(payload: UnknownRecord): Array<{
  itemIndex: number;
  play: string;
  amountMinor: string;
}> {
  const groups = Array.isArray(payload.items) ? payload.items : [];
  const normalized = [];
  let itemIndex = 0;
  for (const rawGroup of groups) {
    const group = record(rawGroup);
    const flat = String(group?.title ?? "").match(FLAT_DOUBLE_PATTERN);
    const flatAmountMinor = toMinor(group?.money);
    const items = Array.isArray(group?.items) ? group.items : null;
    if (items === null) {
      const currentIndex = itemIndex++;
      if (flat && flatAmountMinor !== null) {
        const position = POSITIONS[flat[1] ?? ""];
        const side = flat[2] ?? "";
        if (position !== undefined && SIDES.has(side)) {
          normalized.push({
            itemIndex: currentIndex,
            play: `P${position}:${side}`,
            amountMinor: flatAmountMinor,
          });
        }
      }
      continue;
    }
    const position = POSITIONS[String(group?.title ?? "")];
    for (const rawItem of items) {
      const nestedIndex = itemIndex++;
      const item = record(rawItem);
      const side = String(item?.title ?? "");
      const amountMinor = toMinor(item?.money);
      if (position === undefined || !SIDES.has(side) || amountMinor === null) {
        continue;
      }
      normalized.push({
        itemIndex: nestedIndex,
        play: `P${position}:${side}`,
        amountMinor,
      });
    }
  }
  return normalized;
}

function resultFromPayload(payload: UnknownRecord): {
  issue: string;
  digits: number[];
} | null {
  const directIssue = String(payload.serial ?? "");
  if (Array.isArray(payload.result)) {
    if (
      !/^\d{8,16}$/.test(directIssue) ||
      payload.result.length !== 5 ||
      payload.result.some(
        (digit) =>
          typeof digit !== "number" ||
          !Number.isInteger(digit) ||
          digit < 0 ||
          digit > 9,
      )
    ) {
      return null;
    }
    return { issue: directIssue, digits: [...payload.result] };
  }
  const liveResult = record(payload.result);
  const issue = String(liveResult?.serial ?? "");
  const value = String(liveResult?.value ?? "").replace(/\s+/g, "");
  const match = value.match(/^([0-9])\+([0-9])\+([0-9])\+([0-9])\+([0-9])=/);
  return /^\d{8,16}$/.test(issue) && match
    ? { issue, digits: match.slice(1, 6).map(Number) }
    : null;
}

export async function createFfcNormalizer(
  namespaceKey: Uint8Array,
  now: () => number = Date.now,
  cryptoApi: Crypto = globalThis.crypto,
): Promise<(message: unknown, source: Source) => Promise<CapturedEvent[]>> {
  const keyBytes = Uint8Array.from(namespaceKey);
  let key: CryptoKey;
  try {
    key = await cryptoApi.subtle.importKey(
      "raw",
      keyBytes,
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"],
    );
  } finally {
    keyBytes.fill(0);
  }
  const encoder = new TextEncoder();
  const digest = async (domain: string, value: string): Promise<string> =>
    hex(
      await cryptoApi.subtle.sign(
        "HMAC",
        key,
        encoder.encode(`${domain}|${value}`),
      ),
    );

  return async (message: unknown, source: Source): Promise<CapturedEvent[]> => {
    const root = record(message);
    const text = record(root?.text);
    const outer = record(text?.ext);
    const payload = record(outer?.ext);
    if (outer?.isRobot !== "1" || !payload) {
      return [];
    }

    const type = String(payload.type ?? "");
    if (!["1", "2", "4"].includes(type)) return [];
    const items = normalizedItems(payload);
    const result = type === "4" ? resultFromPayload(payload) : null;
    const model = String(payload.model ?? "");
    if (
      (type === "1" && model !== "Btcffc" && items.length === 0) ||
      (type === "2" && model !== "Btcffc") ||
      (type === "4" && model !== "Btcffc" && result === null)
    ) {
      return [];
    }
    const issue = result?.issue ?? String(payload.serial ?? "");
    const sourceMs = sourceMilliseconds(root?.time);
    const receivedAtMs = sourceMilliseconds(now());
    if (!/^\d{8,16}$/.test(issue) || sourceMs === null || receivedAtMs === null) {
      return [];
    }

    const actorCandidate = outer.uid ?? payload.at;
    const actor = typeof actorCandidate === "string" &&
        actorCandidate.length > 0 && actorCandidate.length <= 512
      ? actorCandidate
      : null;
    const stableMessageId =
      typeof root?.idClient === "string" && root.idClient
        ? root.idClient
        : canonicalJson({ sourceMs, type, actor, items });
    const eventKey = (itemIndex: number): Promise<string> =>
      digest("event", `${stableMessageId}|${itemIndex}`);
    const common = {
      issue,
      sourceMs,
      receivedAtMs,
      source,
      parserVersion: "btcffc-1" as const,
      namespaceVersion: "actor-hmac-v1" as const,
    };

    if (type === "1") {
      if (actor === null) return [];
      const [actorKey, eventKeys] = await Promise.all([
        digest("actor", actor),
        Promise.all(items.map((item) => eventKey(item.itemIndex))),
      ]);
      return items.map((item, index) =>
        capturedEventSchema.parse({
          kind: "BET",
          eventKey: eventKeys[index],
          actorKey,
          play: item.play,
          amountMinor: item.amountMinor,
          ...common,
        }),
      );
    }

    if (type === "2") {
      if (actor === null || items.length === 0) {
        return [
          capturedEventSchema.parse({
            kind: "CANCEL_UNATTRIBUTED",
            eventKey: await eventKey(0),
            ...common,
          }),
        ];
      }
      const [actorKey, eventKeys] = await Promise.all([
        digest("actor", actor),
        Promise.all(items.map((item) => eventKey(item.itemIndex))),
      ]);
      return items.map((item, index) =>
        capturedEventSchema.parse({
          kind: "CANCEL",
          eventKey: eventKeys[index],
          actorKey,
          play: item.play,
          amountMinor: item.amountMinor,
          ...common,
        }),
      );
    }

    if (result === null) return [];
    return [
      capturedEventSchema.parse({
        kind: "RESULT",
        eventKey: await digest(
          "event",
          `result|${result.issue}|${result.digits.join(",")}`,
        ),
        digits: result.digits,
        ...common,
      }),
    ];
  };
}
