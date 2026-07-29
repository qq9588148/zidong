type PublicBetMessage = {
  idClient: string;
  time: number;
  text: {
    ext: {
      isRobot: "1";
      uid: string;
      ext: {
        model: "Btcffc";
        type: "1";
        serial: string;
        items: Array<{
          title: string;
          items: Array<{ title: string; money: string }>;
        }>;
      };
    };
  };
};

type PublicResultMessage = {
  idClient: string;
  time: number;
  text: {
    ext: {
      isRobot: "1";
      ext: {
        model: "Btcffc";
        type: "4";
        serial: string;
        result: number[];
      };
    };
  };
};

const positionLabels: Record<string, string> = {
  "1": "第一球",
  "一": "第一球",
  "2": "第二球",
  "二": "第二球",
  "3": "第三球",
  "三": "第三球",
  "4": "第四球",
  "四": "第四球",
  "5": "第五球",
  "五": "第五球",
};

const commandPattern = /^(?:猜双面[:：])?第([1-5一二三四五])球[:：]?([大小单双质合])[:：]?(0|[1-9]\d*)(?:\.(\d{1,2}))?$/;

export function publicBetCommandElements(document: Document): Element[] {
  return Array.from(document.querySelectorAll(".online-message-details p"));
}

export function publicBetMessageFromElement(
  element: Element,
  issue: string,
  observedAtMs: number,
  nonce: number,
): PublicBetMessage | null {
  if (!/^\d{8,16}$/.test(issue) ||
      !Number.isSafeInteger(observedAtMs) || observedAtMs < 0 ||
      !Number.isSafeInteger(nonce) || nonce < 1) return null;
  const command = compact(element.textContent);
  const match = command.match(commandPattern);
  if (!match) return null;
  const container = element.closest(".online-message");
  const details = element.closest(".online-message-details");
  if (!container || !details) return null;
  const actorElement = Array.from(container.querySelectorAll("p"))
    .find((candidate) => !details.contains(candidate));
  const actor = compact(actorElement?.textContent);
  const stableId = messageIdentifier(element, container);
  return publicBetMessageFromValues({
    actor,
    command,
    stableId,
    issue,
    observedAtMs,
    nonce,
  });
}

export function publicBetMessageFromValues(input: Readonly<{
  actor: string;
  command: string;
  stableId: string | null;
  issue: string;
  observedAtMs: number;
  nonce: number;
}>): PublicBetMessage | null {
  if (!/^\d{8,16}$/.test(input.issue) ||
      !Number.isSafeInteger(input.observedAtMs) || input.observedAtMs < 0 ||
      !Number.isSafeInteger(input.nonce) || input.nonce < 1) return null;
  const actor = compact(input.actor);
  const match = compact(input.command).match(commandPattern);
  if (!actor || actor.length > 256 || !match) return null;
  const position = positionLabels[match[1] ?? ""];
  const direction = match[2];
  const whole = match[3];
  const fraction = match[4];
  if (!position || !direction || !whole) return null;
  const amount = fraction === undefined ? whole : `${whole}.${fraction}`;
  return {
    idClient: input.stableId
      ? `${input.stableId}|${input.issue}`
      : `dom-live|${input.issue}|${input.observedAtMs}|${input.nonce}`,
    time: input.observedAtMs,
    text: {
      ext: {
        isRobot: "1",
        uid: actor,
        ext: {
          model: "Btcffc",
          type: "1",
          serial: input.issue,
          items: [{
            title: position,
            items: [{ title: direction, money: amount }],
          }],
        },
      },
    },
  };
}

export function publicResultMessageFromDocument(
  document: Document,
  observedAtMs: number,
): PublicResultMessage | null {
  const container = document.querySelector(".betResult-game");
  if (!container) return null;
  const label = compact(
    container.querySelector(".betResult-span")?.textContent,
  );
  const issue = label.match(/^第(\d{8,16})期开奖$/)?.[1] ?? "";
  const digitTexts = Array.from(
    container.querySelectorAll(".betResult-style-game .bluestyle"),
  ).map((element) => compact(element.textContent));
  if (digitTexts.length !== 5 || digitTexts.some((value) => !/^\d$/.test(value))) {
    return null;
  }
  return publicResultMessageFromValues({
    issue,
    digits: digitTexts.map(Number),
    observedAtMs,
  });
}

export function publicResultMessageFromValues(input: Readonly<{
  issue: string;
  digits: readonly number[];
  observedAtMs: number;
}>): PublicResultMessage | null {
  if (
    !/^\d{8,16}$/.test(input.issue) ||
    input.digits.length !== 5 ||
    input.digits.some(
      (digit) => !Number.isInteger(digit) || digit < 0 || digit > 9,
    ) ||
    !Number.isSafeInteger(input.observedAtMs) ||
    input.observedAtMs < 0
  ) {
    return null;
  }
  return {
    idClient: `dom-result|${input.issue}`,
    time: input.observedAtMs,
    text: {
      ext: {
        isRobot: "1",
        ext: {
          model: "Btcffc",
          type: "4",
          serial: input.issue,
          result: [...input.digits],
        },
      },
    },
  };
}

function messageIdentifier(element: Element, container: Element): string | null {
  for (const candidate of [element, container]) {
    for (const name of ["data-message-id", "data-msg-id", "data-id", "id"]) {
      const value = candidate.getAttribute(name)?.trim();
      if (value && value.length <= 256) return `dom-id|${value}`;
    }
  }
  return null;
}

function compact(value: string | null | undefined): string {
  return String(value ?? "").replace(/\s+/g, "").trim();
}
