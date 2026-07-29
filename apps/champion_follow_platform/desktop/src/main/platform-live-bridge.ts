import { performance } from "node:perf_hooks";

import type {
  FrozenOrder,
  PlatformBridge,
  RawConfirmation,
  RawSubmission,
} from "./platform-adapter";
import type { ChromeBrowserController } from "./chrome-controller";
import { getPlatformPageController } from "./platform-window";

type PageState = {
  periodId: string;
  countdownMs: number;
  phase: "OPEN" | "CLOSED" | "RESULT";
  currentBalanceFen: string | null;
};

const directionLabels = {
  BIG: "大",
  SMALL: "小",
  ODD: "单",
  EVEN: "双",
  PRIME: "质",
  COMPOSITE: "合",
} as const;

const ballLabels = ["", "第一球", "第二球", "第三球", "第四球", "第五球"] as const;

export class NgPlatformBridge implements PlatformBridge {
  monotonicNow(): number {
    return performance.now();
  }

  async readState(): Promise<unknown> {
    const pageController = requiredPage();
    const page = await evaluate<PageState>(pageController, readPageStateScript());
    const odds: Record<string, 1_960_000> = {};
    for (let position = 1; position <= 5; position += 1) {
      for (const direction of Object.keys(directionLabels)) {
        odds[`P${position}:${direction}`] = 1_960_000;
      }
    }
    return {
      periodId: page.periodId,
      countdownMs: page.countdownMs,
      phase: page.phase,
      oddsMicrosByDirection: odds,
      minStakeFen: "1",
      currentBalanceFen: page.currentBalanceFen,
      receivedMonotonicMs: this.monotonicNow(),
    };
  }

  async submit(order: FrozenOrder): Promise<RawSubmission> {
    const pageController = requiredPage();
    const started = this.monotonicNow();
    const baseline = await evaluate<number>(pageController, publicCommandCountScript());

    const opened = await evaluate<boolean>(pageController, openBetPanelScript());
    if (!opened || !await waitFor(pageController, betPanelReadyScript(), Boolean, 800)) {
      return { status: "REJECTED", reasonCode: "BET_PANEL_UNAVAILABLE" };
    }

    const selected = await selectOrder(pageController, order);
    if (!selected) {
      return { status: "REJECTED", reasonCode: "BET_CONTROLS_MISMATCH" };
    }

    const current = await evaluate<PageState>(pageController, readPageStateScript());
    if (current.periodId !== order.periodId || current.phase !== "OPEN" ||
        current.countdownMs <= 0) {
      return { status: "REJECTED", reasonCode: "PERIOD_CLOSED" };
    }

    const sent = await evaluate<boolean>(pageController, clickFinalBetScript());
    if (!sent) return { status: "REJECTED", reasonCode: "SUBMIT_CONTROL_MISMATCH" };

    const confirmation = await waitFor<ConfirmationProbe | null>(
      pageController,
      confirmationScript(order, baseline),
      (value: ConfirmationProbe | null) => value !== null,
      3_500,
    );
    if (confirmation === null) return { status: "TIMEOUT_AFTER_SEND" };
    if (confirmation.kind === "REJECTED") {
      return { status: "REJECTED", reasonCode: confirmation.reasonCode };
    }
    return rawConfirmation(order, confirmation.reference, started, this.monotonicNow());
  }

  async findOrder(order: FrozenOrder): Promise<RawConfirmation | null> {
    const pageController = requiredPage();
    const reference = await evaluate<string | null>(
      pageController,
      findOrderScript(order),
    );
    return reference === null
      ? null
      : rawConfirmation(order, reference, this.monotonicNow(), this.monotonicNow());
  }

  async readIssueResult(periodId: string): Promise<readonly number[] | null> {
    if (!/^\d{8,20}$/.test(periodId)) return null;
    const pageController = requiredPage();
    return evaluate<readonly number[] | null>(
      pageController,
      issueResultScript(periodId),
    );
  }
}

async function selectOrder(
  pageController: ChromeBrowserController,
  order: FrozenOrder,
): Promise<boolean> {
  const ballDialog = await evaluate<boolean>(pageController, openBallDialogScript());
  if (!ballDialog || !await waitFor(
    pageController,
    ballDialogReadyScript(),
    Boolean,
    500,
  )) {
    return false;
  }
  const ball = ballLabels[order.position];
  if (!await evaluate<boolean>(pageController, selectBallScript(ball))) return false;
  await delay(40);
  return evaluate<boolean>(pageController, selectDirectionAndAmountScript(
    directionLabels[order.direction],
    fenToYuan(order.stakeFen),
    ball,
  ));
}

function requiredPage(): ChromeBrowserController {
  const pageController = getPlatformPageController();
  if (pageController === null || !pageController.isReady()) {
    throw new Error("platform_window_unavailable");
  }
  return pageController;
}

async function evaluate<T>(
  pageController: ChromeBrowserController,
  code: string,
): Promise<T> {
  return await pageController.evaluate<T>(code);
}

async function waitFor<T>(
  pageController: ChromeBrowserController,
  code: string,
  accept: (value: T) => boolean,
  timeoutMs: number,
): Promise<T | null> {
  const deadline = performance.now() + timeoutMs;
  do {
    const value = await evaluate<T>(pageController, code);
    if (accept(value)) return value;
    await delay(50);
  } while (performance.now() < deadline);
  return null;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function readPageStateScript(): string {
  return `(() => {
    const visible = (element) => !!element &&
      (element.offsetWidth || element.offsetHeight || element.getClientRects().length);
    const compact = (value) => String(value || "").replace(/\\s+/g, "").trim();
    const text = document.body ? document.body.innerText : "";
    const selected = [...document.querySelectorAll(".betData .blueTxt")]
      .map((element) => compact(element.textContent))
      .filter((value) => /^\\d{8,20}$/.test(value));
    const fallback = text.match(/第(\\d{8,20})期开奖/);
    const periods = [...new Set(selected.length ? selected : fallback ? [fallback[1]] : [])];
    const countdowns = [...document.querySelectorAll(".van-count-down")]
      .filter(visible).map((element) => compact(element.textContent))
      .filter((value) => /^(?:\\d{1,2}:)?\\d{1,2}:\\d{2}$/.test(value));
    const countdownText = countdowns.length === 1 ? countdowns[0] :
      (text.match(/(?:^|\\n)((?:\\d{1,2}:)?\\d{1,2}:\\d{2})(?:\\n|$)/m) || [])[1];
    let countdownMs = 0;
    if (countdownText) {
      const parts = countdownText.split(":").map(Number);
      countdownMs = (parts.length === 3
        ? parts[0] * 3600 + parts[1] * 60 + parts[2]
        : parts[0] * 60 + parts[1]) * 1000;
    }
    const betButton = [...document.querySelectorAll("button")]
      .find((element) => compact(element.innerText) === "投注");
    const phase = betButton && !betButton.disabled && countdownMs > 0
      ? "OPEN" : /封盘开奖中|封盤|已封盘/.test(text) ? "CLOSED" : "RESULT";
    let balance = null;
    for (const label of [...document.querySelectorAll("*")]) {
      if (label.children.length || compact(label.textContent) !== "账户余额") continue;
      const value = label.closest(".left")?.querySelector(".pt-1")?.textContent
        ?.replace(/,/g, "").trim();
      if (value && /^(?:0|[1-9]\\d{0,15})(?:\\.\\d{1,2})?$/.test(value)) {
        const [yuan, fraction = ""] = value.split(".");
        balance = (BigInt(yuan) * 100n + BigInt((fraction + "00").slice(0, 2))).toString();
      }
    }
    return {
      periodId: periods.length === 1 ? periods[0] : "",
      countdownMs,
      phase,
      currentBalanceFen: balance,
    };
  })()`;
}

function publicCommandCountScript(): string {
  return `document.querySelectorAll(".online-message-details p").length`;
}

function openBetPanelScript(): string {
  return `(() => {
    const compact = (value) => String(value || "").replace(/\\s+/g, "").trim();
    const button = [...document.querySelectorAll("button")]
      .find((element) => compact(element.innerText) === "投注" && !element.disabled);
    if (!button) return false;
    button.click();
    return true;
  })()`;
}

function betPanelReadyScript(): string {
  return `(() => {
    const panel = document.querySelector(".Game28Follow");
    return !!panel && !!(panel.offsetWidth || panel.offsetHeight || panel.getClientRects().length) &&
      (panel.querySelector(".bethead-title")?.textContent || "").trim() === "猜双面";
  })()`;
}

function openBallDialogScript(): string {
  return `(() => {
    const element = document.querySelector(".Game28Follow .BaccaraText");
    if (!element) return false;
    element.click();
    return true;
  })()`;
}

function ballDialogReadyScript(): string {
  return `(() => {
    const dialog = document.querySelector(".Game28Follow .Baccaraselect");
    return !!dialog && !!(dialog.offsetWidth || dialog.offsetHeight || dialog.getClientRects().length) &&
      dialog.querySelectorAll("p").length === 5;
  })()`;
}

function selectBallScript(ball: string): string {
  return `(() => {
    const expected = ${JSON.stringify(ball)};
    const items = [...document.querySelectorAll(".Game28Follow .Baccaraselect p")];
    const item = items.find((element) => (element.textContent || "").trim() === expected);
    if (!item || items.length !== 5) return false;
    item.click();
    return true;
  })()`;
}

function selectDirectionAndAmountScript(
  direction: string,
  amount: string,
  ball: string,
): string {
  return `(() => {
    const compact = (value) => String(value || "").replace(/\\s+/g, "").trim();
    const panel = document.querySelector(".Game28Follow");
    if (!panel || compact(panel.querySelector(".BaccaraText")?.textContent) !== ${JSON.stringify(ball)}) {
      return false;
    }
    const options = [...panel.querySelectorAll(".select-right .grid > span")];
    if (options.length !== 6 || options.some((option) =>
      compact(option.querySelector("i:last-child")?.textContent) !== "1:1.96")) return false;
    const choice = options.find((option) =>
      compact(option.querySelector(".word")?.textContent) === ${JSON.stringify(direction)});
    if (!choice) return false;
    choice.click();
    const input = panel.querySelector("input.betInput");
    if (!(input instanceof HTMLInputElement) || input.disabled) return false;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    if (!setter) return false;
    setter.call(input, ${JSON.stringify(amount)});
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return Number(input.value) === Number(${JSON.stringify(amount)}) && Number(input.value) > 0;
  })()`;
}

function clickFinalBetScript(): string {
  return `(() => {
    const compact = (value) => String(value || "").replace(/\\s+/g, "").trim();
    const panel = document.querySelector(".Game28Follow");
    const button = panel?.querySelector(".select-bottom .buttonactive");
    if (!button || compact(button.textContent) !== "投注") return false;
    button.click();
    return true;
  })()`;
}

type ConfirmationProbe =
  | { kind: "CONFIRMED"; reference: string }
  | { kind: "REJECTED"; reasonCode: string };

function confirmationScript(order: FrozenOrder, baseline: number): string {
  const direction = directionLabels[order.direction];
  const ball = ballLabels[order.position];
  const amount = fenToYuan(order.stakeFen);
  return `(() => {
    const visible = (element) => !!element &&
      (element.offsetWidth || element.offsetHeight || element.getClientRects().length);
    const compact = (value) => String(value || "").replace(/\\s+/g, "").trim();
    const notices = [...document.querySelectorAll(".van-toast,.van-notify,[role=status]")]
      .filter(visible).map((element) => compact(element.textContent));
    const failure = notices.find((text) => /余额不足|投注失败|下注失败|封盘|已截止|金额无效/.test(text));
    if (failure) return { kind: "REJECTED", reasonCode: /余额不足/.test(failure)
      ? "BALANCE_INSUFFICIENT" : "PLATFORM_REJECTED" };
    const success = notices.find((text) => /投注成功|下注成功|已受理|投注已提交/.test(text));
    const messages = [...document.querySelectorAll(".online-message-details p")];
    const expectedBall = ${JSON.stringify(ball)};
    const expectedDirection = ${JSON.stringify(direction)};
    const expectedAmount = Number(${JSON.stringify(amount)});
    const matchingIndex = messages.findIndex((element, index) => {
      if (index < ${baseline}) return false;
      const text = compact(element.textContent);
      const match = text.match(/^猜双面:(第[一二三四五]球):([大小单双质合]):(\\d+(?:\\.\\d{1,2})?)$/);
      return !!match && match[1] === expectedBall && match[2] === expectedDirection &&
        Number(match[3]) === expectedAmount;
    });
    if (success || matchingIndex >= 0) {
      return { kind: "CONFIRMED", reference: [
        ${JSON.stringify(order.periodId)}, expectedBall, expectedDirection,
        String(expectedAmount), String(matchingIndex >= 0 ? matchingIndex : messages.length),
      ].join("|") };
    }
    return null;
  })()`;
}

function findOrderScript(order: FrozenOrder): string {
  const direction = directionLabels[order.direction];
  const ball = ballLabels[order.position];
  const amount = fenToYuan(order.stakeFen);
  return `(() => {
    const compact = (value) => String(value || "").replace(/\\s+/g, "").trim();
    const body = document.body ? document.body.innerText : "";
    if (!body.includes(${JSON.stringify(order.periodId)})) return null;
    const messages = [...document.querySelectorAll(".online-message-details p")];
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const match = compact(messages[index].textContent)
        .match(/^猜双面:(第[一二三四五]球):([大小单双质合]):(\\d+(?:\\.\\d{1,2})?)$/);
      if (match && match[1] === ${JSON.stringify(ball)} &&
          match[2] === ${JSON.stringify(direction)} &&
          Number(match[3]) === Number(${JSON.stringify(amount)})) {
        return [${JSON.stringify(order.periodId)}, match[1], match[2], match[3], String(index)].join("|");
      }
    }
    return null;
  })()`;
}

function issueResultScript(periodId: string): string {
  return `(() => {
    const text = document.body ? document.body.innerText : "";
    const compact = text.replace(/\\s+/g, "");
    const pattern = new RegExp("第" + ${JSON.stringify(periodId)} +
      "期(?:开奖)?结果(?:为)?([0-9])\\\\+([0-9])\\\\+([0-9])\\\\+([0-9])\\\\+([0-9])=");
    const match = compact.match(pattern);
    return match ? match.slice(1, 6).map(Number) : null;
  })()`;
}

function rawConfirmation(
  order: FrozenOrder,
  reference: string,
  started: number,
  finished: number,
): RawConfirmation {
  return {
    status: "CONFIRMED",
    platformOrderReference: reference,
    periodId: order.periodId,
    position: order.position,
    direction: order.direction,
    stakeFen: order.stakeFen,
    oddsMicros: order.expectedOddsMicros,
    confirmedAt: utcMicros(new Date()),
    durationMs: Math.max(0, Math.round(finished - started)),
  };
}

function fenToYuan(value: bigint): string {
  if (value <= 0n) throw new Error("platform_stake_invalid");
  const yuan = value / 100n;
  const fraction = (value % 100n).toString().padStart(2, "0")
    .replace(/0+$/, "");
  return fraction ? `${yuan}.${fraction}` : yuan.toString();
}

function utcMicros(value: Date): string {
  return value.toISOString().replace(/\.(\d{3})Z$/, (_match, millis: string) =>
    `.${millis}000Z`);
}
