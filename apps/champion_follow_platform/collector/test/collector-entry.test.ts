import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it, vi } from "vitest";

import {
  CollectorEntryStore,
  clickBtcFfcEntry,
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

  it("waits for login and then clicks only the exact Btc FFC card", () => {
    const loggedOut = {
      body: { innerText: "请先登录或注册" },
      querySelectorAll: () => [],
    } as unknown as Document;
    expect(clickBtcFfcEntry(loggedOut)).toBe("AUTH_REQUIRED");

    const exactClick = vi.fn();
    const otherClick = vi.fn();
    const exact = {
      textContent: "比特分分彩",
      closest: () => ({ click: exactClick }),
    };
    const other = {
      textContent: "分分彩",
      closest: () => ({ click: otherClick }),
    };
    const loggedIn = {
      body: { innerText: "大厅" },
      querySelectorAll: () => [exact, other],
    } as unknown as Document;
    expect(clickBtcFfcEntry(loggedIn)).toBe("CLICKED");
    expect(exactClick).toHaveBeenCalledOnce();
    expect(otherClick).not.toHaveBeenCalled();
  });
});
