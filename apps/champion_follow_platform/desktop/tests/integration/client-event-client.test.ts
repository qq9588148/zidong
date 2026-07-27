import { describe, expect, it } from "vitest";

import {
  MemoryClientEventOutbox,
  ReliableClientEventClient,
} from "../../src/main/client-event-client";

describe("ReliableClientEventClient", () => {
  it("replays exactly the same signed bytes after a timeout", async () => {
    const outbox = new MemoryClientEventOutbox(1);
    const signedBytes = Buffer.from("{\"signed\":true}");
    const seen: Buffer[] = [];
    let attempt = 0;
    const client = new ReliableClientEventClient({
      outbox,
      build: async (sequence) => ({ sequence, bytes: signedBytes }),
      transport: async (bytes) => {
        seen.push(Buffer.from(bytes));
        attempt += 1;
        if (attempt === 1) throw new Error("timeout");
        return { ack_seq: 1 };
      },
    });

    await client.enqueue("EXECUTION_STATE", {});
    await expect(client.flush()).rejects.toThrow("client_event_transport_failed");
    await client.flush();
    expect(seen).toHaveLength(2);
    expect(seen[0]?.equals(seen[1]!)).toBe(true);
    expect(await outbox.pending()).toEqual([]);
  });
});
