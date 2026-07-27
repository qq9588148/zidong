import { createHash } from "node:crypto";
import {
  mkdir,
  open,
  readFile,
  rename,
  rm,
} from "node:fs/promises";
import type { FileHandle } from "node:fs/promises";
import { join } from "node:path";

import { z } from "zod";

import { canonicalJson } from "./canonical-json.js";
import {
  capturedEventSchema,
  journalRecordSchema,
  type CapturedEvent,
  type JournalRecord,
} from "./contracts.js";

const cursorSchema = z
  .object({
    ackSeq: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),
    ackEventKey: z.string().regex(/^[0-9a-f]{64}(?::(?:block|close|[0-9]{1,15}))?$/).nullable(),
  })
  .strict()
  .superRefine((cursor, context) => {
    if ((cursor.ackSeq === 0) !== (cursor.ackEventKey === null)) {
      context.addIssue({ code: "custom", message: "cursor mismatch" });
    }
  });

export interface JournalOpenResult {
  repairedTail: boolean;
  lastSeq: number;
  acknowledgedSeq: number;
  acknowledgedEventKey: string | null;
}

function safeError(code: string): Error {
  return new Error(code);
}

function recordDigest(seq: number, event: CapturedEvent): string {
  return createHash("sha256")
    .update(canonicalJson({ seq, event }))
    .digest("hex");
}

export class AppendOnlyJournal {
  private readonly journalPath: string;
  private readonly cursorPath: string;
  private readonly lockPath: string;
  private readonly compactingPath: string;
  private handle: FileHandle | null = null;
  private ownsLock = false;
  private started = false;
  private rows: JournalRecord[] = [];
  private sequence = 0;
  private ackSequence = 0;
  private ackEventKey: string | null = null;
  private tailWasRepaired = false;

  constructor(private readonly root: string) {
    this.journalPath = join(root, "events.ndjson");
    this.cursorPath = join(root, "cursor.json");
    this.lockPath = join(root, "collector.lock");
    this.compactingPath = join(root, "events.compacting");
  }

  get repairedTail(): boolean {
    return this.tailWasRepaired;
  }

  get lastSeq(): number {
    return this.sequence;
  }

  get acknowledgedSeq(): number {
    return this.ackSequence;
  }

  get acknowledgedEventKey(): string | null {
    return this.ackEventKey;
  }

  async start(): Promise<JournalOpenResult> {
    if (this.started) throw safeError("journal_already_started");
    await mkdir(this.root, { recursive: true, mode: 0o700 });
    await this.acquireLock();
    try {
      await rm(this.compactingPath, { force: true });
      await this.loadCursor();
      await this.loadRows();
      this.handle = await open(this.journalPath, "a", 0o600);
      this.started = true;
      return {
        repairedTail: this.tailWasRepaired,
        lastSeq: this.sequence,
        acknowledgedSeq: this.ackSequence,
        acknowledgedEventKey: this.ackEventKey,
      };
    } catch (error) {
      await this.handle?.close().catch(() => undefined);
      this.handle = null;
      await this.releaseLock();
      if ((error as Error).message.startsWith("journal_")) throw error;
      throw safeError("journal_corrupt");
    }
  }

  async append(event: CapturedEvent): Promise<JournalRecord> {
    this.requireStarted();
    const strictEvent = capturedEventSchema.parse(event);
    const seq = this.sequence + 1;
    const record = journalRecordSchema.parse({
      seq,
      event: strictEvent,
      digest: recordDigest(seq, strictEvent),
    });
    try {
      await this.handle!.writeFile(`${canonicalJson(record)}\n`, "utf8");
      await this.handle!.sync();
    } catch {
      throw safeError("journal_write_failed");
    }
    this.rows.push(record);
    this.sequence = seq;
    return record;
  }

  pending(limit = Number.MAX_SAFE_INTEGER): JournalRecord[] {
    this.requireStarted();
    return this.rows
      .filter((record) => record.seq > this.ackSequence)
      .slice(0, Math.max(0, limit));
  }

  replay(): JournalRecord[] {
    this.requireStarted();
    return [...this.rows];
  }

  async advanceAck(seq: number): Promise<void> {
    this.requireStarted();
    if (!Number.isSafeInteger(seq) || seq < this.ackSequence || seq > this.sequence) {
      throw safeError("journal_ack_invalid");
    }
    if (seq === this.ackSequence) return;
    const record = this.rows.find((row) => row.seq === seq);
    if (!record) throw safeError("journal_corrupt");
    await this.writeCursor(seq, record.event.eventKey);
    this.ackSequence = seq;
    this.ackEventKey = record.event.eventKey;
  }

  async compact(): Promise<void> {
    this.requireStarted();
    const pending = this.rows.filter((record) => record.seq > this.ackSequence);
    await this.handle!.close();
    this.handle = null;
    try {
      await rm(this.compactingPath, { force: true });
      const compacting = await open(this.compactingPath, "wx", 0o600);
      try {
        if (pending.length > 0) {
          await compacting.writeFile(
            `${pending.map((record) => canonicalJson(record)).join("\n")}\n`,
            "utf8",
          );
        }
        await compacting.sync();
      } finally {
        await compacting.close();
      }
      await rename(this.compactingPath, this.journalPath);
      this.rows = pending;
    } catch {
      await rm(this.compactingPath, { force: true }).catch(() => undefined);
      throw safeError("journal_write_failed");
    } finally {
      this.handle = await open(this.journalPath, "a", 0o600);
    }
  }

  async close(): Promise<void> {
    if (this.handle) {
      await this.handle.close();
      this.handle = null;
    }
    this.started = false;
    await this.releaseLock();
  }

  private requireStarted(): void {
    if (!this.started || !this.handle) throw safeError("journal_not_started");
  }

  private async acquireLock(): Promise<void> {
    for (;;) {
      try {
        const handle = await open(this.lockPath, "wx", 0o600);
        try {
          await handle.writeFile(String(process.pid), "utf8");
          await handle.sync();
        } finally {
          await handle.close();
        }
        this.ownsLock = true;
        return;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      }

      let pid: number;
      try {
        const value = await readFile(this.lockPath, "utf8");
        if (!/^[1-9]\d*$/.test(value)) throw safeError("journal_locked");
        pid = Number(value);
        if (!Number.isSafeInteger(pid)) throw safeError("journal_locked");
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") continue;
        throw error;
      }

      try {
        process.kill(pid, 0);
        throw safeError("journal_locked");
      } catch (error) {
        const code = (error as NodeJS.ErrnoException).code;
        if (code === "EPERM") throw safeError("journal_locked");
        if (code !== "ESRCH") throw error;
      }
      await rm(this.lockPath, { force: true });
    }
  }

  private async releaseLock(): Promise<void> {
    if (!this.ownsLock) return;
    this.ownsLock = false;
    try {
      if ((await readFile(this.lockPath, "utf8")) === String(process.pid)) {
        await rm(this.lockPath, { force: true });
      }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
  }

  private async loadCursor(): Promise<void> {
    try {
      const cursor = cursorSchema.parse(
        JSON.parse(await readFile(this.cursorPath, "utf8")),
      );
      this.ackSequence = cursor.ackSeq;
      this.ackEventKey = cursor.ackEventKey;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
      throw safeError("journal_corrupt");
    }
  }

  private async loadRows(): Promise<void> {
    let bytes: Buffer;
    try {
      bytes = await readFile(this.journalPath);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        this.sequence = this.ackSequence;
        return;
      }
      throw error;
    }

    if (bytes.length > 0 && bytes[bytes.length - 1] !== 10) {
      const lastNewline = bytes.lastIndexOf(10);
      const handle = await open(this.journalPath, "r+");
      try {
        await handle.truncate(lastNewline + 1);
        await handle.sync();
      } finally {
        await handle.close();
      }
      bytes = bytes.subarray(0, lastNewline + 1);
      this.tailWasRepaired = true;
    }

    const rows: JournalRecord[] = [];
    try {
      const lines = bytes.toString("utf8").split("\n");
      for (const line of lines.slice(0, -1)) {
        if (!line) throw safeError("journal_corrupt");
        const parsed = journalRecordSchema.parse(JSON.parse(line));
        if (parsed.digest !== recordDigest(parsed.seq, parsed.event)) {
          throw safeError("journal_corrupt");
        }
        rows.push(parsed);
      }
    } catch {
      throw safeError("journal_corrupt");
    }

    for (let index = 1; index < rows.length; index += 1) {
      if (rows[index]!.seq !== rows[index - 1]!.seq + 1) {
        throw safeError("journal_corrupt");
      }
    }
    if (rows.length > 0) {
      const first = rows[0]!;
      const last = rows[rows.length - 1]!;
      if (first.seq > this.ackSequence + 1 || last.seq < this.ackSequence) {
        throw safeError("journal_corrupt");
      }
      const acknowledged = rows.find((row) => row.seq === this.ackSequence);
      if (
        acknowledged &&
        acknowledged.event.eventKey !== this.ackEventKey
      ) {
        throw safeError("journal_corrupt");
      }
      this.sequence = Math.max(this.ackSequence, last.seq);
    } else {
      this.sequence = this.ackSequence;
    }
    this.rows = rows;
  }

  private async writeCursor(seq: number, eventKey: string): Promise<void> {
    const temporary = `${this.cursorPath}.new`;
    await rm(temporary, { force: true });
    try {
      const handle = await open(temporary, "wx", 0o600);
      try {
        await handle.writeFile(canonicalJson({ ackSeq: seq, ackEventKey: eventKey }));
        await handle.sync();
      } finally {
        await handle.close();
      }
      await rename(temporary, this.cursorPath);
    } catch {
      await rm(temporary, { force: true }).catch(() => undefined);
      throw safeError("journal_write_failed");
    }
  }
}
