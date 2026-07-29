import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { JsonExecutionStore } from "../../src/main/execution-journal";

describe("JsonExecutionStore", () => {
  it("restores a confirmed order without losing bigint stake", async () => {
    const directory = await mkdtemp(join(tmpdir(), "champion-execution-"));
    const path = join(directory, "orders.json");
    try {
      const store = new JsonExecutionStore(path);
      await store.put({
        state: "CONFIRMED",
        order: {
          clientOrderId: "00000000-0000-4000-8000-000000000001",
          generation: "00000000-0000-4000-8000-000000000002",
          taskId: "00000000-0000-4000-8000-000000000003",
          deviceId: "00000000-0000-4000-8000-000000000004",
          periodId: "2607290001",
          taskRevision: 4,
          position: 2,
          direction: "SMALL",
          stakeFen: 100n,
          expectedOddsMicros: 1_960_000,
        },
        result: {
          state: "CONFIRMED",
          platformOrderRef: `sha256:${"a".repeat(64)}`,
          confirmedAt: "2026-07-29T04:00:00.000000Z",
          durationMs: 120,
        },
      });

      const restarted = new JsonExecutionStore(path);
      expect((await restarted.get("2607290001"))?.order.stakeFen).toBe(100n);
      expect((await restarted.pendingRecovery())?.state).toBe("CONFIRMED");
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("persists the confirmed-order count across restarts", async () => {
    const directory = await mkdtemp(join(tmpdir(), "champion-follow-execution-count-"));
    const path = join(directory, "executions.json");
    try {
      const store = new JsonExecutionStore(path);
      await store.put({
        state: "CONFIRMED",
        order: {
          clientOrderId: "00000000-0000-4000-8000-000000000011",
          generation: "00000000-0000-4000-8000-000000000012",
          taskId: "00000000-0000-4000-8000-000000000013",
          deviceId: "00000000-0000-4000-8000-000000000014",
          periodId: "2607290002",
          taskRevision: 1,
          position: 1,
          direction: "ODD",
          stakeFen: 100n,
          expectedOddsMicros: 1_960_000,
        },
        result: {
          state: "CONFIRMED",
          platformOrderRef: `sha256:${"b".repeat(64)}`,
          confirmedAt: "2026-07-29T04:01:00.000000Z",
          durationMs: 100,
        },
      });

      expect(await new JsonExecutionStore(path).confirmedOrderCount()).toBe(1);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
});
