import { describe, expect, it } from "vitest";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  MemoryClientEventOutbox,
  JsonClientEventOutbox,
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

  it("persists signed bytes and the next sequence across restart", async () => {
    const directory = await mkdtemp(join(tmpdir(), "champion-event-outbox-"));
    const path = join(directory, "outbox.json");
    try {
      const first = new JsonClientEventOutbox(path, 1);
      await first.append({ sequence: 1, bytes: Buffer.from("signed-event") });
      const restarted = new JsonClientEventOutbox(path, 1);
      expect(await restarted.nextSequence()).toBe(2);
      expect((await restarted.pending())[0]?.bytes.toString()).toBe("signed-event");
      await restarted.acknowledge(1);
      expect(await new JsonClientEventOutbox(path, 1).pending()).toEqual([]);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
});
