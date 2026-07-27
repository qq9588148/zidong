import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { freshBankroll, settleLoss } from "../../src/main/bankroll";
import { BankrollStore } from "../../src/main/bankroll-store";

const directories: string[] = [];

afterEach(async () => {
  await Promise.all(directories.splice(0).map((directory) =>
    rm(directory, { recursive: true, force: true })));
});

async function fixtureStore() {
  const directory = await mkdtemp(join(tmpdir(), "champion-bankroll-"));
  directories.push(directory);
  return new BankrollStore(join(directory, "bankroll.json"));
}

describe("BankrollStore", () => {
  it("round trips bigint money and rejects stale writers", async () => {
    const store = await fixtureStore();
    const initial = freshBankroll({
      baseFen: 100n,
      capFen: 50_000n,
      stakeUnitFen: 100n,
    });
    await store.save(initial, null);
    expect(await store.load()).toEqual(initial);

    const next = settleLoss(initial, { orderId: "o1", stakeFen: 100n });
    await store.save(next, initial.version);
    await expect(store.save(next, initial.version))
      .rejects.toThrow("bankroll_version_conflict");
  });

  it("recovers the highest valid temporary journal after a torn write", async () => {
    const store = await fixtureStore();
    const initial = freshBankroll({
      baseFen: 100n,
      capFen: 50_000n,
      stakeUnitFen: 100n,
    });
    await store.save(initial, null);
    const next = settleLoss(initial, { orderId: "o1", stakeFen: 100n });
    await store.save(next, initial.version);

    const valid = await readFile(store.path, "utf8");
    await writeFile(store.temporaryPath, valid, "utf8");
    await writeFile(store.path, "{torn", "utf8");
    expect(await store.load()).toEqual(next);
    expect(JSON.parse(await readFile(store.path, "utf8"))).toHaveProperty("checksum");
  });
});
