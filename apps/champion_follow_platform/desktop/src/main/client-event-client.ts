import type { ClientEventType } from "./client-event-contract";
import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

export type QueuedClientEvent = { sequence: number; bytes: Buffer };

export interface ClientEventOutbox {
  nextSequence(): Promise<number>;
  append(item: QueuedClientEvent): Promise<void>;
  pending(): Promise<QueuedClientEvent[]>;
  acknowledge(sequence: number): Promise<void>;
}

export class MemoryClientEventOutbox implements ClientEventOutbox {
  private next = 1;
  private readonly items: QueuedClientEvent[] = [];

  constructor(readonly bindingEpoch: number) {
    if (!Number.isSafeInteger(bindingEpoch) || bindingEpoch < 1) {
      throw new Error("client_event_binding_invalid");
    }
  }

  async nextSequence(): Promise<number> {
    return this.next;
  }

  async append(item: QueuedClientEvent): Promise<void> {
    if (item.sequence !== this.next) throw new Error("client_event_sequence_conflict");
    this.items.push({ sequence: item.sequence, bytes: Buffer.from(item.bytes) });
    this.next += 1;
  }

  async pending(): Promise<QueuedClientEvent[]> {
    return this.items.map((item) => ({
      sequence: item.sequence,
      bytes: Buffer.from(item.bytes),
    }));
  }

  async acknowledge(sequence: number): Promise<void> {
    const index = this.items.findIndex((item) => item.sequence === sequence);
    if (index < 0) throw new Error("client_event_ack_conflict");
    this.items.splice(0, index + 1);
  }
}

type WireOutbox = {
  schemaVersion: 1;
  bindingEpoch: number;
  nextSequence: number;
  items: Array<{ sequence: number; bytesBase64: string }>;
  checksum: string;
};

export class JsonClientEventOutbox implements ClientEventOutbox {
  private loaded = false;
  private next = 1;
  private readonly items: QueuedClientEvent[] = [];

  constructor(
    private readonly path: string,
    readonly bindingEpoch: number,
  ) {
    if (!Number.isSafeInteger(bindingEpoch) || bindingEpoch < 1) {
      throw new Error("client_event_binding_invalid");
    }
  }

  async nextSequence(): Promise<number> {
    await this.load();
    return this.next;
  }

  async append(item: QueuedClientEvent): Promise<void> {
    await this.load();
    if (item.sequence !== this.next) throw new Error("client_event_sequence_conflict");
    this.items.push({ sequence: item.sequence, bytes: Buffer.from(item.bytes) });
    this.next += 1;
    await this.save();
  }

  async pending(): Promise<QueuedClientEvent[]> {
    await this.load();
    return this.items.map((item) => ({
      sequence: item.sequence,
      bytes: Buffer.from(item.bytes),
    }));
  }

  async acknowledge(sequence: number): Promise<void> {
    await this.load();
    const index = this.items.findIndex((item) => item.sequence === sequence);
    if (index < 0) throw new Error("client_event_ack_conflict");
    this.items.splice(0, index + 1);
    await this.save();
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
      throw new Error("client_event_outbox_read_failed");
    }
    try {
      const wire = JSON.parse(text) as WireOutbox;
      const unsigned = {
        schemaVersion: wire.schemaVersion,
        bindingEpoch: wire.bindingEpoch,
        nextSequence: wire.nextSequence,
        items: wire.items,
      };
      if (wire.schemaVersion !== 1 || wire.bindingEpoch !== this.bindingEpoch ||
          !Number.isSafeInteger(wire.nextSequence) || wire.nextSequence < 1 ||
          !Array.isArray(wire.items) || wire.checksum !== checksum(unsigned)) {
        throw new Error();
      }
      for (const item of wire.items) {
        if (!Number.isSafeInteger(item.sequence) || item.sequence < 1 ||
            typeof item.bytesBase64 !== "string") throw new Error();
        const bytes = Buffer.from(item.bytesBase64, "base64");
        if (bytes.toString("base64") !== item.bytesBase64) throw new Error();
        this.items.push({ sequence: item.sequence, bytes });
      }
      if (this.items.some((item, index) => index > 0 &&
          item.sequence !== this.items[index - 1]!.sequence + 1) ||
          this.items.some((item) => item.sequence >= wire.nextSequence)) {
        throw new Error();
      }
      this.next = wire.nextSequence;
      this.loaded = true;
    } catch {
      throw new Error("client_event_outbox_invalid");
    }
  }

  private async save(): Promise<void> {
    const unsigned = {
      schemaVersion: 1 as const,
      bindingEpoch: this.bindingEpoch,
      nextSequence: this.next,
      items: this.items.map((item) => ({
        sequence: item.sequence,
        bytesBase64: item.bytes.toString("base64"),
      })),
    };
    const wire: WireOutbox = { ...unsigned, checksum: checksum(unsigned) };
    await mkdir(dirname(this.path), { recursive: true });
    const temporary = `${this.path}.${randomUUID()}.tmp`;
    try {
      await writeFile(temporary, `${JSON.stringify(wire)}\n`, {
        encoding: "utf8",
        flag: "wx",
        mode: 0o600,
      });
      await rename(temporary, this.path);
    } finally {
      await rm(temporary, { force: true }).catch(() => undefined);
    }
  }
}

type ReliableClientEventOptions = {
  outbox: ClientEventOutbox;
  build: (
    sequence: number,
    type: ClientEventType,
    payload: Record<string, unknown>,
  ) => Promise<QueuedClientEvent>;
  transport: (bytes: Buffer) => Promise<{ ack_seq: number }>;
};

export class ReliableClientEventClient {
  constructor(private readonly options: ReliableClientEventOptions) {}

  async enqueue(
    type: ClientEventType,
    payload: Record<string, unknown>,
  ): Promise<void> {
    const sequence = await this.options.outbox.nextSequence();
    const event = await this.options.build(sequence, type, payload);
    if (event.sequence !== sequence) throw new Error("client_event_sequence_conflict");
    await this.options.outbox.append(event);
  }

  async flush(): Promise<void> {
    for (const item of await this.options.outbox.pending()) {
      let response: { ack_seq: number };
      try {
        response = await this.options.transport(Buffer.from(item.bytes));
      } catch {
        throw new Error("client_event_transport_failed");
      }
      if (response.ack_seq !== item.sequence) {
        throw new Error("client_event_ack_conflict");
      }
      await this.options.outbox.acknowledge(response.ack_seq);
    }
  }
}

function checksum(value: object): string {
  return createHash("sha256").update(JSON.stringify(value)).digest("hex");
}
