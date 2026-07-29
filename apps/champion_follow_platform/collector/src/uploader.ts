import type { Heartbeat, JournalRecord } from "./contracts.js";
import type { CollectorServerPort } from "./server-api.js";

const RETRY_DELAYS = [250, 500, 1000, 2000, 5000] as const;

interface JournalPort {
  readonly acknowledgedSeq: number;
  pending(limit?: number): JournalRecord[];
  advanceAck(seq: number): Promise<void>;
}

type Wait = (milliseconds: number, signal: AbortSignal) => Promise<void>;

function wait(milliseconds: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const timeout = setTimeout(done, milliseconds);
    function done(): void {
      clearTimeout(timeout);
      signal.removeEventListener("abort", done);
      resolve();
    }
    signal.addEventListener("abort", done, { once: true });
  });
}

export class ReliableUploader {
  private heartbeatIsHealthy = false;

  constructor(
    private readonly collectorId: string,
    private readonly journal: JournalPort,
    private readonly server: CollectorServerPort,
    private readonly waitFor: Wait = wait,
  ) {}

  get heartbeatHealthy(): boolean {
    return this.heartbeatIsHealthy;
  }

  async tick(): Promise<number> {
    const pending = this.journal.pending(200);
    if (!pending.length) return this.journal.acknowledgedSeq;
    const issue = pending[0]!.event.issue;
    const boundary = pending.findIndex((record) => record.event.issue !== issue);
    const records = boundary < 0 ? pending : pending.slice(0, boundary);
    const response = await this.server.append({
      collector_id: this.collectorId,
      namespace_version: "actor-hmac-v1",
      from_seq: records[0]!.seq,
      to_seq: records.at(-1)!.seq,
      records,
    });
    if (response.ack_seq <= this.journal.acknowledgedSeq) {
      return this.journal.acknowledgedSeq;
    }
    if (response.ack_seq > records.at(-1)!.seq) {
      throw new Error("collector_ack_invalid");
    }
    try {
      await this.journal.advanceAck(response.ack_seq);
    } catch {
      throw new Error("journal_write_failed");
    }
    return response.ack_seq;
  }

  async run(signal: AbortSignal): Promise<void> {
    let failures = 0;
    while (!signal.aborted) {
      try {
        const hadPending = this.journal.pending(1).length > 0;
        await this.tick();
        failures = 0;
        await this.waitFor(hadPending ? 0 : 50, signal);
      } catch (error) {
        if (error instanceof Error && error.message === "journal_write_failed") {
          throw error;
        }
        let recovered = false;
        try {
          recovered = await this.reconcileCommittedBatch();
        } catch {
          recovered = false;
        }
        if (recovered) {
          failures = 0;
          await this.waitFor(0, signal);
          continue;
        }
        const delay = RETRY_DELAYS[Math.min(failures, RETRY_DELAYS.length - 1)]!;
        failures += 1;
        await this.waitFor(delay, signal);
      }
    }
  }

  private async reconcileCommittedBatch(): Promise<boolean> {
    const localAck = this.journal.acknowledgedSeq;
    const remote = await this.server.session({
      collector_id: this.collectorId,
      namespace_version: "actor-hmac-v1",
    });
    if (remote.ack_seq <= localAck) return false;
    const row = this.journal.pending(200)
      .find((candidate) => candidate.seq === remote.ack_seq);
    if (!row || row.event.eventKey !== remote.ack_event_key) {
      throw new Error("collector_sequence_conflict");
    }
    await this.journal.advanceAck(remote.ack_seq);
    return true;
  }

  async runHeartbeats(
    signal: AbortSignal,
    current: () => Heartbeat,
  ): Promise<void> {
    while (!signal.aborted) {
      try {
        await this.server.heartbeat(current());
        this.heartbeatIsHealthy = true;
      } catch {
        this.heartbeatIsHealthy = false;
      }
      await this.waitFor(250, signal);
    }
  }

}
