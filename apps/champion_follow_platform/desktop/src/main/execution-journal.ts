import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

import type { ExecutionRecord, ExecutionStore } from "./execution-machine";

type WireRecord = Omit<ExecutionRecord, "order"> & {
  order: Omit<ExecutionRecord["order"], "stakeFen"> & { stakeFen: string };
};

type Envelope = {
  schemaVersion: 1;
  records: WireRecord[];
  checksum: string;
};

export class JsonExecutionStore implements ExecutionStore {
  private loaded = false;
  private readonly records = new Map<string, ExecutionRecord>();

  constructor(private readonly path: string) {}

  async get(periodId: string): Promise<ExecutionRecord | null> {
    await this.load();
    const record = this.records.get(periodId);
    return record ? structuredClone(record) : null;
  }

  async put(record: ExecutionRecord): Promise<void> {
    await this.load();
    const existing = this.records.get(record.order.periodId);
    if (existing && existing.order.clientOrderId !== record.order.clientOrderId) {
      throw new Error("execution_journal_period_conflict");
    }
    this.records.set(record.order.periodId, structuredClone(record));
    await this.save();
  }

  async hasUnknown(): Promise<boolean> {
    await this.load();
    return [...this.records.values()].some((record) => record.state === "UNKNOWN");
  }

  async unsettledConfirmed(): Promise<ExecutionRecord | null> {
    await this.load();
    const record = [...this.records.values()].find((item) =>
      item.state === "CONFIRMED");
    return record ? structuredClone(record) : null;
  }

  async pendingRecovery(): Promise<ExecutionRecord | null> {
    await this.load();
    const record = [...this.records.values()].find((item) =>
      item.state === "SUBMITTING" || item.state === "CONFIRMED");
    return record ? structuredClone(record) : null;
  }

  private async load(): Promise<void> {
    if (this.loaded) return;
    let text: string;
    try {
      text = await readFile(this.path, "utf8");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        this.loaded = true;
        return;
      }
      throw new Error("execution_journal_read_failed");
    }
    try {
      const envelope = JSON.parse(text) as Envelope;
      if (envelope.schemaVersion !== 1 || !Array.isArray(envelope.records) ||
          envelope.checksum !== digest(envelope.records)) {
        throw new Error();
      }
      for (const wire of envelope.records) {
        const record = decode(wire);
        if (this.records.has(record.order.periodId)) throw new Error();
        this.records.set(record.order.periodId, record);
      }
      this.loaded = true;
    } catch {
      throw new Error("execution_journal_invalid");
    }
  }

  private async save(): Promise<void> {
    const records = [...this.records.values()].map(encode);
    const envelope: Envelope = {
      schemaVersion: 1,
      records,
      checksum: digest(records),
    };
    await mkdir(dirname(this.path), { recursive: true });
    const temporary = `${this.path}.${randomUUID()}.tmp`;
    try {
      await writeFile(temporary, `${JSON.stringify(envelope)}\n`, {
        encoding: "utf8",
        mode: 0o600,
        flag: "wx",
      });
      await rename(temporary, this.path);
    } finally {
      await rm(temporary, { force: true }).catch(() => undefined);
    }
  }
}

function encode(record: ExecutionRecord): WireRecord {
  return {
    ...structuredClone(record),
    order: {
      ...structuredClone(record.order),
      stakeFen: record.order.stakeFen.toString(),
    },
  };
}

function decode(record: WireRecord): ExecutionRecord {
  if (!record || typeof record !== "object" ||
      !["SUBMITTING", "CONFIRMED", "SETTLED", "REJECTED", "UNKNOWN", "CANCELED"]
        .includes(record.state) ||
      !record.order || typeof record.order !== "object" ||
      typeof record.order.periodId !== "string" ||
      typeof record.order.clientOrderId !== "string" ||
      typeof record.order.stakeFen !== "string" ||
      !/^[1-9][0-9]*$/.test(record.order.stakeFen)) {
    throw new Error("execution_journal_invalid");
  }
  return {
    ...structuredClone(record),
    order: {
      ...structuredClone(record.order),
      stakeFen: BigInt(record.order.stakeFen),
    },
  } as ExecutionRecord;
}

function digest(records: WireRecord[]): string {
  return createHash("sha256").update(JSON.stringify(records)).digest("hex");
}
