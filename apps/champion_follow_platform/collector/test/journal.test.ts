import { readFile, writeFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { AppendOnlyJournal } from "../src/journal.js";
import { capturedEventSchema } from "../src/contracts.js";

const roots: string[] = [];

async function temporaryRoot(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "collector-journal-"));
  roots.push(root);
  return root;
}

function closeEvent(issue: string, marker: string) {
  return capturedEventSchema.parse({
    kind: "CLOSE",
    eventKey: marker.repeat(64),
    issue,
    sourceMs: 1000,
    receivedAtMs: 1001,
    source: "realtime",
    parserVersion: "btcffc-1",
    namespaceVersion: "actor-hmac-v1",
  });
}

afterEach(async () => {
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true })));
});

describe("AppendOnlyJournal", () => {
  it("continues with the next sequence after a clean reopen", async () => {
    const root = await temporaryRoot();
    const first = new AppendOnlyJournal(root);
    expect(await first.start()).toMatchObject({ lastSeq: 0, repairedTail: false });
    expect((await first.append(closeEvent("2607270001", "1"))).seq).toBe(1);
    expect((await first.append(closeEvent("2607270001", "2"))).seq).toBe(2);
    await first.close();

    const reopened = new AppendOnlyJournal(root);
    expect(await reopened.start()).toMatchObject({ lastSeq: 2 });
    expect((await reopened.append(closeEvent("2607270002", "3"))).seq).toBe(3);
    await reopened.close();
  });

  it("compacts only acknowledged records and preserves pending replay", async () => {
    const root = await temporaryRoot();
    const journal = new AppendOnlyJournal(root);
    await journal.start();
    await journal.append(closeEvent("2607270001", "1"));
    const second = await journal.append(closeEvent("2607270001", "2"));
    await journal.advanceAck(1);
    await journal.compact();
    expect(journal.pending()).toEqual([second]);
    await journal.close();

    const reopened = new AppendOnlyJournal(root);
    expect(await reopened.start()).toMatchObject({
      lastSeq: 2,
      acknowledgedSeq: 1,
    });
    expect(reopened.pending()).toEqual([second]);
    expect(reopened.replay()).toEqual([second]);
    await reopened.close();
  });

  it("repairs only an unterminated final line", async () => {
    const root = await temporaryRoot();
    const journal = new AppendOnlyJournal(root);
    await journal.start();
    await journal.append(closeEvent("2607270001", "1"));
    await journal.close();
    const path = join(root, "events.ndjson");
    await writeFile(path, Buffer.concat([await readFile(path), Buffer.from('{"seq":2')]));

    const reopened = new AppendOnlyJournal(root);
    expect(await reopened.start()).toMatchObject({
      repairedTail: true,
      lastSeq: 1,
    });
    expect(reopened.pending()).toHaveLength(1);
    await reopened.close();
  });

  it("fails closed when a complete middle record is corrupted", async () => {
    const root = await temporaryRoot();
    const journal = new AppendOnlyJournal(root);
    await journal.start();
    await journal.append(closeEvent("2607270001", "1"));
    await journal.append(closeEvent("2607270001", "2"));
    await journal.append(closeEvent("2607270001", "3"));
    await journal.close();

    const path = join(root, "events.ndjson");
    const lines = (await readFile(path, "utf8")).trimEnd().split("\n");
    const middle = JSON.parse(lines[1]!) as { digest: string };
    middle.digest = `${middle.digest[0] === "0" ? "1" : "0"}${middle.digest.slice(1)}`;
    lines[1] = JSON.stringify(middle);
    await writeFile(path, `${lines.join("\n")}\n`);

    const reopened = new AppendOnlyJournal(root);
    await expect(reopened.start()).rejects.toThrow("journal_corrupt");
  });

  it("treats a blank complete middle line as corruption", async () => {
    const root = await temporaryRoot();
    const journal = new AppendOnlyJournal(root);
    await journal.start();
    await journal.append(closeEvent("2607270001", "1"));
    await journal.append(closeEvent("2607270001", "2"));
    await journal.close();
    const path = join(root, "events.ndjson");
    const lines = (await readFile(path, "utf8")).trimEnd().split("\n");
    await writeFile(path, `${lines[0]}\n\n${lines[1]}\n`);

    const reopened = new AppendOnlyJournal(root);
    await expect(reopened.start()).rejects.toThrow("journal_corrupt");
  });

  it("allows only one live writer", async () => {
    const root = await temporaryRoot();
    const first = new AppendOnlyJournal(root);
    const second = new AppendOnlyJournal(root);
    await first.start();
    await expect(second.start()).rejects.toThrow("journal_locked");
    await first.close();
  });
});
