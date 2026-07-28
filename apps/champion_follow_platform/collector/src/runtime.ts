import { createHash } from "node:crypto";

import { canonicalJson } from "./canonical-json.js";
import {
  IssueCompletenessTracker,
  type CompletenessResult,
} from "./completeness.js";
import {
  capturedEventSchema,
  heartbeatSchema,
  type CapturedEvent,
  type Heartbeat,
  type JournalRecord,
} from "./contracts.js";
import type {
  CollectorServerPort,
  CollectorSessionValue,
} from "./server-api.js";
import { ReliableUploader } from "./uploader.js";

export interface RuntimeJournalPort {
  readonly repairedTail: boolean;
  readonly lastSeq: number;
  readonly acknowledgedSeq: number;
  readonly acknowledgedEventKey: string | null;
  append(event: CapturedEvent): Promise<JournalRecord>;
  pending(limit?: number): JournalRecord[];
  replay(): JournalRecord[];
  advanceAck(seq: number): Promise<void>;
}

export interface HistoryPageRequest {
  historyAnchorEventKey: string;
  limit: 100;
}

export interface HistoryPage {
  events: CapturedEvent[];
  crossedUncertainBoundary: boolean;
  uncertainBoundarySourceMs: number | null;
}

export type HistoryPageReader = (
  request: HistoryPageRequest,
) => Promise<HistoryPage>;

export interface CollectorRuntimeOptions {
  collectorId: string;
  journal: RuntimeJournalPort;
  server: CollectorServerPort;
  tracker?: IssueCompletenessTracker;
  stopCapture?: (code: string) => void;
}

export interface HeartbeatObservation {
  issue: string | null;
  phase: "BETTING" | "CLOSED" | "UNKNOWN";
  countdownMs: number;
  observedAtMs: number;
}

function safeError(code: string): Error {
  return new Error(code);
}

function eventFingerprint(event: CapturedEvent): string {
  const { receivedAtMs: _receivedAtMs, source: _source, ...semantic } = event;
  return canonicalJson(semantic);
}

function derivedEventKey(value: {
  kind: "CAPTURE_GAP" | "ISSUE_STATUS";
  issue: string;
  reasonOrStatus:
    | string
    | { complete: boolean; reasons: readonly string[] };
  lastInputEventKey: string;
}): string {
  return createHash("sha256").update(canonicalJson(value)).digest("hex");
}

function commonFrom(
  trigger: CapturedEvent,
  eventKey: string,
): Pick<
  CapturedEvent,
  | "eventKey"
  | "issue"
  | "sourceMs"
  | "receivedAtMs"
  | "source"
  | "parserVersion"
  | "namespaceVersion"
> {
  return {
    eventKey,
    issue: trigger.issue,
    sourceMs: trigger.sourceMs,
    receivedAtMs: trigger.receivedAtMs,
    source: trigger.source,
    parserVersion: trigger.parserVersion,
    namespaceVersion: trigger.namespaceVersion,
  };
}

export class CollectorRuntime {
  private readonly journal: RuntimeJournalPort;
  private readonly server: CollectorServerPort;
  private tracker: IssueCompletenessTracker;
  private readonly stopCapture: (code: string) => void;
  private readonly uploader: ReliableUploader;
  private readonly seen = new Map<string, string | null>();
  private readonly closedIssues = new Set<string>();
  private readonly resultIssues = new Set<string>();
  private readonly historyDecidedIssues = new Set<string>();
  private readonly finalStatusIssues = new Set<string>();
  private readonly recoveredHistoryIssues = new Set<string>();
  private readonly anchorProofEvents = new Map<string, CapturedEvent>();
  private captureHealthy = true;
  private tornTailPending: boolean;
  private currentBoundary: CapturedEvent | null = null;
  private remoteSession: CollectorSessionValue | null = null;
  private backfilling = false;
  private continuitySuspended = false;
  private liveContinuityEstablished = false;
  private ingestTail: Promise<void> = Promise.resolve();
  private lastHeartbeat: HeartbeatObservation = {
    issue: null,
    phase: "UNKNOWN",
    countdownMs: 0,
    observedAtMs: 0,
  };

  constructor(private readonly options: CollectorRuntimeOptions) {
    this.journal = options.journal;
    this.server = options.server;
    this.tracker = options.tracker ?? new IssueCompletenessTracker();
    this.stopCapture = options.stopCapture ?? (() => undefined);
    this.uploader = new ReliableUploader(
      options.collectorId,
      options.journal,
      options.server,
    );
    this.tornTailPending = options.journal.repairedTail;
    for (const row of options.journal.replay()) {
      this.seen.set(row.event.eventKey, eventFingerprint(row.event));
    }
    this.rebuildTracker();
    if (options.journal.acknowledgedEventKey) {
      if (!this.seen.has(options.journal.acknowledgedEventKey)) {
        this.seen.set(options.journal.acknowledgedEventKey, null);
      }
    }
  }

  ingest(events: readonly CapturedEvent[]): Promise<number> {
    let strict: CapturedEvent[];
    try {
      strict = events.map((value) => capturedEventSchema.parse(value));
    } catch {
      return Promise.reject(safeError("collector_capture_invalid"));
    }
    return this.serialized(async () => {
      this.requireCaptureHealthy();
      for (const value of strict) {
        const prior = this.seen.get(value.eventKey);
        if (prior !== undefined) {
          if (prior === eventFingerprint(value)) continue;
          this.failProtocol("collector_event_conflict");
          throw safeError("collector_event_conflict");
        }
        await this.persistCaptured(value);
      }
      return this.journal.lastSeq;
    });
  }

  observeBettingBoundary(trigger: CapturedEvent): Promise<number> {
    let strict: CapturedEvent;
    try {
      strict = capturedEventSchema.parse(trigger);
    } catch {
      return Promise.reject(safeError("collector_capture_invalid"));
    }
    return this.serialized(async () => {
      this.requireCaptureHealthy();
      await this.observeBoundary(strict);
      return this.journal.lastSeq;
    });
  }

  observePageState(observation: HeartbeatObservation): Promise<number> {
    const issue = observation.issue;
    if (
      issue === null ||
      !/^\d{8,16}$/.test(issue) ||
      !Number.isSafeInteger(observation.countdownMs) ||
      observation.countdownMs < 0 ||
      !Number.isSafeInteger(observation.observedAtMs) ||
      observation.observedAtMs < 0
    ) {
      return Promise.reject(safeError("collector_page_state_invalid"));
    }
    this.lastHeartbeat = { ...observation };
    return this.serialized(async () => {
      this.requireCaptureHealthy();
      const trigger = capturedEventSchema.parse({
        kind: "CLOSE",
        eventKey: createHash("sha256")
          .update(
            canonicalJson({
              kind:
                observation.phase === "BETTING"
                  ? "BETTING_BOUNDARY"
                  : "CLOSE",
              issue,
            }),
          )
          .digest("hex"),
        issue,
        sourceMs: observation.observedAtMs,
        receivedAtMs: observation.observedAtMs,
        source: "realtime",
        parserVersion: "btcffc-1",
        namespaceVersion: "actor-hmac-v1",
      });
      if (observation.phase === "BETTING") {
        if (this.currentBoundary?.issue !== issue) {
          await this.observeBoundary(trigger);
        }
      } else if (
        observation.phase === "CLOSED" &&
        !this.seen.has(trigger.eventKey)
      ) {
        await this.persistCaptured(trigger);
      }
      return this.journal.lastSeq;
    });
  }

  markHistoryAnchorRecovered(issue: string): void {
    this.recoveredHistoryIssues.add(issue);
    this.historyDecidedIssues.add(issue);
    this.tracker.markHistoryAnchorRecovered(issue);
  }

  completeness(issue: string): CompletenessResult {
    return this.tracker.evaluate(issue);
  }

  markCaptureUnhealthy(): void {
    this.captureHealthy = false;
  }

  currentHeartbeat(observation = this.lastHeartbeat): Heartbeat {
    return heartbeatSchema.parse({
      collector_id: this.options.collectorId,
      issue: observation.issue,
      phase: observation.phase,
      countdown_ms: observation.countdownMs,
      observed_at_ms: observation.observedAtMs,
      last_journal_seq: this.journal.lastSeq,
      capture_healthy: this.captureHealthy,
    });
  }

  historyRecoveryOpen(): boolean {
    return this.lastHeartbeat.phase !== "CLOSED";
  }

  suspendForReconnect(): Promise<void> {
    return this.serialized(async () => {
      this.requireCaptureHealthy();
      this.suspendContinuity();
      this.remoteSession = null;
      this.backfilling = true;
      this.rebuildTracker();
    });
  }

  async uploadOnce(): Promise<number> {
    try {
      return await this.uploader.tick();
    } catch (error) {
      if (error instanceof Error && error.message === "journal_write_failed") {
        this.failJournal();
      }
      throw error;
    }
  }

  runUploads(signal: AbortSignal): Promise<void> {
    return this.uploader.run(signal).catch((error: unknown) => {
      if (error instanceof Error && error.message === "journal_write_failed") {
        this.failJournal();
        return;
      }
      throw error;
    });
  }

  runHeartbeats(
    signal: AbortSignal,
    current: () => Heartbeat,
  ): Promise<void> {
    return this.uploader.runHeartbeats(signal, current);
  }

  async reconcileSession(): Promise<CollectorSessionValue> {
    const remote = await this.server.session({
      collector_id: this.options.collectorId,
      namespace_version: "actor-hmac-v1",
    });
    return this.serialized(async () => {
      const localSeq = this.journal.acknowledgedSeq;
      const localKey = this.journal.acknowledgedEventKey;
      if (remote.ack_seq < localSeq) {
        throw safeError("collector_sequence_conflict");
      }
      if (remote.ack_seq === localSeq) {
        if (remote.ack_event_key !== localKey) {
          throw safeError("collector_sequence_conflict");
        }
      } else {
        const row = this.journal
          .pending()
          .find((candidate) => candidate.seq === remote.ack_seq);
        if (!row || row.event.eventKey !== remote.ack_event_key) {
          throw safeError("collector_sequence_conflict");
        }
        try {
          await this.journal.advanceAck(remote.ack_seq);
        } catch {
          this.failJournal();
          throw safeError("journal_write_failed");
        }
      }
      if (!this.continuitySuspended) this.suspendContinuity();
      this.remoteSession = remote;
      this.backfilling = true;
      this.rebuildTracker();
      return remote;
    });
  }

  async recoverHistory(readPage: HistoryPageReader): Promise<void> {
    const remote = this.remoteSession;
    if (!remote) throw safeError("collector_session_missing");
    const anchor = remote.history_anchor_event_key;
    if (anchor === null) {
      if (!remote.namespace_empty) {
        this.markCaptureUnhealthy();
        throw safeError("collector_history_anchor_missing");
      }
      if (!this.currentBoundary) {
        throw safeError("collector_history_boundary_missing");
      }
      await this.finishHistoryRecovery(
        this.currentBoundary.issue,
        this.currentBoundary,
        null,
      );
      return;
    }

    const observed = new Set<string>();
    for (;;) {
      let page: HistoryPage;
      try {
        const value = await readPage({
          historyAnchorEventKey: anchor,
          limit: 100,
        });
        if (
          typeof value.crossedUncertainBoundary !== "boolean" ||
          (value.uncertainBoundarySourceMs !== null &&
            (!Number.isSafeInteger(value.uncertainBoundarySourceMs) ||
              value.uncertainBoundarySourceMs < 0))
        ) {
          throw safeError("collector_history_read_failed");
        }
        page = {
          events: value.events.map((event) => capturedEventSchema.parse(event)),
          crossedUncertainBoundary: value.crossedUncertainBoundary,
          uncertainBoundarySourceMs: value.uncertainBoundarySourceMs,
        };
      } catch {
        throw safeError("collector_history_read_failed");
      }
      if (page.events.length === 0) {
        await this.markMissingHistoryAnchor();
        return;
      }

      let madeProgress = false;
      const anchorEvent = page.events.find((value) => value.eventKey === anchor);
      if (
        anchorEvent &&
        anchorEvent.kind !== "BET" &&
        anchorEvent.kind !== "CANCEL"
      ) {
        this.failProtocol("collector_history_anchor_invalid");
        throw safeError("collector_history_anchor_invalid");
      }
      for (const value of page.events) {
        if (value.eventKey === anchor) continue;
        if (observed.has(value.eventKey)) continue;
        observed.add(value.eventKey);
        madeProgress = true;
        await this.ingest([value]);
      }
      if (anchorEvent) {
        const boundaryUncertain =
          page.crossedUncertainBoundary ||
          page.uncertainBoundarySourceMs === anchorEvent.sourceMs;
        if (boundaryUncertain) {
          await this.markMissingHistoryAnchor(
            this.currentBoundary ?? anchorEvent,
          );
          return;
        }
        const issue = this.currentBoundary?.issue ?? anchorEvent.issue;
        await this.finishHistoryRecovery(
          issue,
          this.currentBoundary ?? anchorEvent,
          anchorEvent,
        );
        return;
      }
      if (!madeProgress || observed.size >= 10_000) {
        await this.markMissingHistoryAnchor();
        return;
      }
    }
  }

  async startLiveCollectionWithoutHistory(): Promise<void> {
    const remote = this.remoteSession;
    if (!remote) throw safeError("collector_session_missing");
    if (
      !remote.namespace_empty ||
      remote.history_anchor_event_key !== null ||
      !this.currentBoundary
    ) {
      throw safeError("collector_history_anchor_missing");
    }
    if (!this.historyRecoveryOpen()) return;
    await this.markMissingHistoryAnchor(this.currentBoundary);
  }

  private serialized<T>(operation: () => Promise<T>): Promise<T> {
    const next = this.ingestTail.then(operation, operation);
    this.ingestTail = next.then(
      () => undefined,
      () => undefined,
    );
    return next;
  }

  private async appendInput(value: CapturedEvent): Promise<void> {
    await this.appendCaptured(value);
    this.tracker.ingest(value);
    if (value.kind === "CLOSE") this.closedIssues.add(value.issue);
    if (value.kind === "RESULT") this.resultIssues.add(value.issue);
    if (
      value.kind === "CAPTURE_GAP" &&
      value.reason === "history_anchor_missing"
    ) {
      this.historyDecidedIssues.add(value.issue);
    }
    await this.appendStatus(value);
  }

  private async persistCaptured(value: CapturedEvent): Promise<void> {
    if (this.backfilling) {
      await this.appendCaptured(value);
      return;
    }
    await this.appendInput(value);
  }

  private async appendCaptured(value: CapturedEvent): Promise<void> {
    await this.durableAppend(value);
    this.seen.set(value.eventKey, eventFingerprint(value));
  }

  private async observeBoundary(trigger: CapturedEvent): Promise<void> {
    this.currentBoundary = trigger;
    this.tracker.observeBetting(trigger.issue);
    if (this.liveContinuityEstablished) {
      this.markHistoryAnchorRecovered(trigger.issue);
    }
    if (!this.tornTailPending) return;
    const gap = this.captureGap("journal_torn_tail", trigger);
    await this.persistCaptured(gap);
    this.tornTailPending = false;
  }

  private async appendStatus(trigger: CapturedEvent): Promise<void> {
    if (
      !this.closedIssues.has(trigger.issue) ||
      !this.resultIssues.has(trigger.issue) ||
      !this.historyDecidedIssues.has(trigger.issue) ||
      this.finalStatusIssues.has(trigger.issue)
    ) {
      return;
    }
    const result = this.tracker.evaluate(trigger.issue);
    const reasonOrStatus = {
      complete: result.complete,
      reasons: [...new Set(result.reasons)].sort(),
    };
    const eventKey = derivedEventKey({
      kind: "ISSUE_STATUS",
      issue: trigger.issue,
      reasonOrStatus,
      lastInputEventKey: trigger.eventKey,
    });
    const status = this.tracker.statusTransition(
      trigger.issue,
      commonFrom(trigger, eventKey),
    );
    if (!status || this.seen.has(status.eventKey)) return;
    await this.durableAppend(capturedEventSchema.parse(status));
    this.seen.set(status.eventKey, eventFingerprint(status));
    this.finalStatusIssues.add(status.issue);
  }

  private captureGap(
    reason: "journal_torn_tail" | "history_anchor_missing",
    trigger: CapturedEvent,
  ): CapturedEvent {
    const eventKey = derivedEventKey({
      kind: "CAPTURE_GAP",
      issue: trigger.issue,
      reasonOrStatus: reason,
      lastInputEventKey: trigger.eventKey,
    });
    return capturedEventSchema.parse({
      kind: "CAPTURE_GAP",
      ...commonFrom(trigger, eventKey),
      reason,
    });
  }

  private async markMissingHistoryAnchor(
    trigger: CapturedEvent | null = this.currentBoundary,
  ): Promise<void> {
    if (!trigger) {
      throw safeError("collector_history_anchor_missing");
    }
    const gap = this.captureGap("history_anchor_missing", trigger);
    await this.ingest([gap]);
    await this.finishHistoryRecovery(
      trigger.issue,
      gap,
      null,
      false,
    );
  }

  private async finishHistoryRecovery(
    issue: string,
    trigger: CapturedEvent,
    anchor: CapturedEvent | null,
    recovered = true,
  ): Promise<void> {
    await this.serialized(async () => {
      let recoverySucceeded = recovered;
      let statusTrigger = trigger;
      const closedBeforeRecovery =
        (this.lastHeartbeat.issue === issue &&
          this.lastHeartbeat.phase === "CLOSED") ||
        this.journal
          .replay()
          .some((row) => row.event.kind === "CLOSE" && row.event.issue === issue);
      if (recoverySucceeded && closedBeforeRecovery) {
        const gap = this.captureGap("history_anchor_missing", trigger);
        if (!this.seen.has(gap.eventKey)) await this.appendCaptured(gap);
        recoverySucceeded = false;
        statusTrigger = gap;
      }
      if (anchor) this.anchorProofEvents.set(issue, anchor);
      if (recoverySucceeded) {
        this.recoveredHistoryIssues.add(issue);
        this.liveContinuityEstablished = true;
      } else {
        this.recoveredHistoryIssues.delete(issue);
        this.liveContinuityEstablished = true;
      }
      this.historyDecidedIssues.add(issue);
      this.backfilling = false;
      this.continuitySuspended = false;
      this.rebuildTracker();
      await this.appendStatus(statusTrigger);
    });
  }

  private rebuildTracker(): void {
    this.tracker = new IssueCompletenessTracker();
    this.closedIssues.clear();
    this.resultIssues.clear();
    this.finalStatusIssues.clear();

    const retained = [
      ...this.options.journal.replay().map((row) => row.event),
      ...this.anchorProofEvents.values(),
    ];
    const unique = new Map<string, CapturedEvent>();
    for (const value of retained) {
      const prior = unique.get(value.eventKey);
      if (prior && eventFingerprint(prior) !== eventFingerprint(value)) {
        this.failProtocol("collector_event_conflict");
        throw safeError("collector_event_conflict");
      }
      unique.set(value.eventKey, value);
    }
    const events = [...unique.values()].sort(
      (left, right) =>
        left.sourceMs - right.sourceMs ||
        left.eventKey.localeCompare(right.eventKey),
    );
    for (const value of events) {
      this.tracker.ingest(value);
      if (value.kind === "CLOSE") this.closedIssues.add(value.issue);
      if (value.kind === "RESULT") this.resultIssues.add(value.issue);
      if (
        value.kind === "CAPTURE_GAP" &&
        value.reason === "history_anchor_missing"
      ) {
        this.historyDecidedIssues.add(value.issue);
      }
      if (value.kind === "ISSUE_STATUS") {
        this.finalStatusIssues.add(value.issue);
      }
    }
    if (this.currentBoundary) {
      this.tracker.observeBetting(this.currentBoundary.issue);
    }
    for (const issue of this.recoveredHistoryIssues) {
      this.tracker.markHistoryAnchorRecovered(issue);
    }
  }

  private async durableAppend(value: CapturedEvent): Promise<JournalRecord> {
    try {
      return await this.journal.append(capturedEventSchema.parse(value));
    } catch {
      this.failJournal();
      throw safeError("journal_write_failed");
    }
  }

  private requireCaptureHealthy(): void {
    if (!this.captureHealthy) throw safeError("journal_write_failed");
  }

  private suspendContinuity(): void {
    const suspendedIssue =
      this.currentBoundary?.issue ?? this.lastHeartbeat.issue;
    if (suspendedIssue && !this.finalStatusIssues.has(suspendedIssue)) {
      this.recoveredHistoryIssues.delete(suspendedIssue);
      this.historyDecidedIssues.delete(suspendedIssue);
    }
    this.continuitySuspended = true;
    this.liveContinuityEstablished = false;
    this.currentBoundary = null;
    this.lastHeartbeat = {
      issue: null,
      phase: "UNKNOWN",
      countdownMs: 0,
      observedAtMs: 0,
    };
  }

  private failJournal(): void {
    if (!this.captureHealthy) return;
    this.captureHealthy = false;
    this.stopCapture("journal_write_failed");
  }

  private failProtocol(code: string): void {
    if (!this.captureHealthy) return;
    this.captureHealthy = false;
    this.stopCapture(code);
  }
}

export interface CollectorBootstrapSteps<Credential, Server, Window> {
  loadIdentity(): Promise<unknown>;
  loadCredential(): Promise<Credential>;
  createServer(credential: Credential): Server;
  openJournal(): Promise<void>;
  reconcileSession(server: Server): Promise<void>;
  openWindow(): Promise<Window>;
  startLoops(server: Server, window: Window): void | Promise<void>;
  cleanup?(): void | Promise<void>;
}

const SAFE_STARTUP_CODES = new Set([
  "collector_credential_argument_forbidden",
  "collector_credential_argument_invalid",
  "collector_credential_missing",
  "collector_credential_encryption_unavailable",
  "collector_credential_read_failed",
  "collector_credential_invalid",
  "collector_credential_use_stdin_on_windows",
  "collector_credential_permissions_invalid",
  "collector_credential_source_delete_failed",
  "collector_credential_input_failed",
  "collector_credential_already_initialized",
  "collector_credential_store_failed",
  "identity_encryption_unavailable",
  "identity_key_invalid",
  "journal_locked",
  "journal_corrupt",
  "journal_write_failed",
  "collector_server_https_required",
  "collector_network_error",
  "collector_auth_rejected",
  "collector_sequence_conflict",
  "collector_server_error",
  "collector_history_anchor_missing",
  "collector_history_boundary_missing",
]);

export function startupErrorCode(error: unknown): string {
  const code = error instanceof Error ? error.message : "";
  return SAFE_STARTUP_CODES.has(code) ? code : "collector_start_failed";
}

export async function bootstrapCollector<Credential, Server, Window>(
  steps: CollectorBootstrapSteps<Credential, Server, Window>,
): Promise<{ server: Server; window: Window }> {
  try {
    await steps.openJournal();
    await steps.loadIdentity();
    const credential = await steps.loadCredential();
    const server = steps.createServer(credential);
    await steps.reconcileSession(server);
    const window = await steps.openWindow();
    await steps.startLoops(server, window);
    return { server, window };
  } catch (error) {
    await Promise.resolve(steps.cleanup?.()).catch(() => undefined);
    throw safeError(startupErrorCode(error));
  }
}
