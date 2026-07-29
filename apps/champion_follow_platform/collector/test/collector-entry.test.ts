import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  collectorStartupEntryUrl,
  CollectorEntryStore,
  sanitizeCollectorEntryUrl,
} from "../src/collector-entry.js";

describe("collector entry recovery", () => {
  it("persists only credential-free HTTPS page locations", async () => {
    expect(sanitizeCollectorEntryUrl("https://random.example/game#/room/ffc"))
      .toBe("https://random.example/game#/room/ffc");
    expect(sanitizeCollectorEntryUrl("https://random.example/?token=secret"))
      .toBeNull();
    expect(sanitizeCollectorEntryUrl("http://random.example/game")).toBeNull();

    const root = await mkdtemp(join(tmpdir(), "collector-entry-"));
    const path = join(root, "entry.json");
    const store = new CollectorEntryStore(path);
    expect(await store.load("https://ng888.com/")).toBe("https://ng888.com/");
    expect(await store.save("https://random.example/game#/room/ffc")).toBe(true);
    expect(await store.load("https://ng888.com/")).toBe(
      "https://random.example/game#/room/ffc",
    );
    expect(await readFile(path, "utf8")).not.toContain("token");
    await rm(root, { recursive: true });
  });

  it("always starts from the configured platform entry", () => {
    expect(collectorStartupEntryUrl(
      "https://random.example/game",
      "https://ng888.com/",
    )).toBe("https://ng888.com/");
    expect(collectorStartupEntryUrl(
      "https://random.example/home",
      "https://ng888.com/",
    )).toBe("https://ng888.com/");
    expect(collectorStartupEntryUrl(
      "not-a-url",
      "https://ng888.com/",
    )).toBe("https://ng888.com/");
  });

});
