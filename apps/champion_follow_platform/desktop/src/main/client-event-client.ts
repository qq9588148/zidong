import type { ClientEventType } from "./client-event-contract";

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
