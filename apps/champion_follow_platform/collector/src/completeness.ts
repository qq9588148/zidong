import {
  capturedEventSchema,
  type CapturedEvent,
} from "./contracts.js";

const OPPOSITE: Record<string, string> = {
  大: "小",
  小: "大",
  单: "双",
  双: "单",
  质: "合",
  合: "质",
};

type StatusEvent = Extract<CapturedEvent, { kind: "ISSUE_STATUS" }>;
type StatusCommon = Pick<
  CapturedEvent,
  | "eventKey"
  | "sourceMs"
  | "receivedAtMs"
  | "source"
  | "parserVersion"
  | "namespaceVersion"
>;

interface IssueState {
  bettingBoundary: boolean;
  historyAnchor: boolean;
  close: boolean;
  result: boolean;
  faults: Set<string>;
  net: Map<string, bigint>;
  lastStatus: string | null;
}

export interface CompletenessResult {
  complete: boolean;
  reasons: readonly string[];
}

function newState(): IssueState {
  return {
    bettingBoundary: false,
    historyAnchor: false,
    close: false,
    result: false,
    faults: new Set(),
    net: new Map(),
    lastStatus: null,
  };
}

function moneyKey(actorKey: string, position: string, side: string): string {
  return `${actorKey}|${position}|${side}`;
}

export class IssueCompletenessTracker {
  private readonly issues = new Map<string, IssueState>();

  observeBetting(issue: string): void {
    this.state(issue).bettingBoundary = true;
  }

  markHistoryAnchorRecovered(issue: string): void {
    this.state(issue).historyAnchor = true;
  }

  ingest(event: CapturedEvent): void {
    const strict = capturedEventSchema.parse(event);
    const state = this.state(strict.issue);
    switch (strict.kind) {
      case "BET":
      case "CANCEL": {
        const [position, side] = strict.play.split(":");
        const key = moneyKey(strict.actorKey, position!, side!);
        const current = state.net.get(key) ?? 0n;
        const amount = BigInt(strict.amountMinor);
        if (strict.kind === "CANCEL" && amount > current) {
          state.faults.add("cancel_overdraw");
          return;
        }
        state.net.set(
          key,
          strict.kind === "BET" ? current + amount : current - amount,
        );
        const opposite = OPPOSITE[side!];
        const oppositeNet = state.net.get(
          moneyKey(strict.actorKey, position!, opposite!),
        ) ?? 0n;
        if ((state.net.get(key) ?? 0n) > 0n && oppositeNet > 0n) {
          state.faults.add("opposite_net_conflict");
        }
        return;
      }
      case "CANCEL_UNATTRIBUTED":
        state.faults.add("unattributed_cancel");
        return;
      case "CAPTURE_GAP":
        state.faults.add(strict.reason);
        return;
      case "CLOSE":
        state.close = true;
        return;
      case "RESULT":
        state.result = true;
        return;
      case "ISSUE_STATUS":
        return;
    }
  }

  evaluate(issue: string): CompletenessResult {
    const state = this.state(issue);
    const reasons = new Set(state.faults);
    if (!state.historyAnchor) reasons.add("history_anchor_missing");
    if (!state.bettingBoundary) reasons.add("betting_boundary_missing");
    if (!state.close) reasons.add("close_missing");
    if (!state.result) reasons.add("result_missing");
    const ordered = [...reasons].sort();
    return { complete: ordered.length === 0, reasons: ordered };
  }

  statusTransition(
    issue: string,
    common: StatusCommon,
  ): StatusEvent | null {
    const state = this.state(issue);
    const result = this.evaluate(issue);
    const signature = JSON.stringify([result.complete, result.reasons]);
    if (signature === state.lastStatus) return null;
    const event = capturedEventSchema.parse({
      kind: "ISSUE_STATUS",
      issue,
      eventKey: common.eventKey,
      sourceMs: common.sourceMs,
      receivedAtMs: common.receivedAtMs,
      source: common.source,
      parserVersion: common.parserVersion,
      namespaceVersion: common.namespaceVersion,
      complete: result.complete,
      reasons: result.reasons,
    });
    if (event.kind !== "ISSUE_STATUS") throw new Error("status_event_invalid");
    state.lastStatus = signature;
    return event;
  }

  private state(issue: string): IssueState {
    let state = this.issues.get(issue);
    if (!state) {
      state = newState();
      this.issues.set(issue, state);
    }
    return state;
  }
}
