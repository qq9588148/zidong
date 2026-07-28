import { chmod, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

export type EntryClickResult =
  | "AUTH_REQUIRED"
  | "LOBBY_OPENED"
  | "CLICKED"
  | "NOT_FOUND";

export function sanitizeCollectorEntryUrl(value: string): string | null {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password ||
        url.search ||
        !/^\/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$/.test(url.pathname) ||
        (url.hash !== "" && !/^#\/[A-Za-z0-9/_-]{0,200}$/.test(url.hash))) {
      return null;
    }
    return `${url.origin}${url.pathname}${url.hash}`;
  } catch {
    return null;
  }
}

export function btcFfcGameUrl(value: string): string | null {
  const sanitized = sanitizeCollectorEntryUrl(value);
  if (sanitized === null) return null;
  const url = new URL(sanitized);
  return `${url.origin}/game`;
}

export class CollectorEntryStore {
  constructor(private readonly path: string) {}

  async load(fallback: string): Promise<string> {
    try {
      const parsed = JSON.parse(await readFile(this.path, "utf8"));
      if (!isObject(parsed) || Object.keys(parsed).length !== 1 ||
          typeof parsed.url !== "string") return fallback;
      return sanitizeCollectorEntryUrl(parsed.url) ?? fallback;
    } catch {
      return fallback;
    }
  }

  async save(value: string): Promise<boolean> {
    const url = sanitizeCollectorEntryUrl(value);
    if (url === null) return false;
    await mkdir(dirname(this.path), { recursive: true, mode: 0o700 });
    const temporary = `${this.path}.new`;
    try {
      await rm(temporary, { force: true });
      await writeFile(temporary, `${JSON.stringify({ url })}\n`, {
        encoding: "utf8",
        mode: 0o600,
        flag: "wx",
      });
      await chmod(temporary, 0o600);
      await rename(temporary, this.path);
      return true;
    } catch {
      await rm(temporary, { force: true }).catch(() => undefined);
      return false;
    }
  }
}

export function clickBtcFfcEntry(document: Document): EntryClickResult {
  const exactTitle = Array.from(document.querySelectorAll("p.game-title"))
    .find((element) => (element.textContent ?? "").trim() === "比特分分彩");
  const fallbackTitle = exactTitle ?? Array.from(document.querySelectorAll("*"))
    .find((element) => (element.children?.length ?? 0) === 0 &&
      (element.textContent ?? "")
      .replace(/\s+/g, "").trim() === "比特分分彩");
  if (fallbackTitle === undefined) {
    const lobbyLabel = Array.from(document.querySelectorAll("*"))
      .find((element) => (element.children?.length ?? 0) === 0 &&
        (element.textContent ?? "")
        .replace(/\s+/g, "").trim() === "大厅");
    const lobbyTarget = lobbyLabel?.closest(
      ".van-tabbar-item, [role='tab'], a, button",
    ) ?? lobbyLabel;
    if (lobbyTarget && typeof (lobbyTarget as HTMLElement).click === "function") {
      (lobbyTarget as HTMLElement).click();
      return "LOBBY_OPENED";
    }
    return (document.body?.innerText ?? "").includes("请先登录或注册")
      ? "AUTH_REQUIRED"
      : "NOT_FOUND";
  }
  const target = fallbackTitle.closest(
    ".lottery-game, a, button, [role='button']",
  ) ?? fallbackTitle;
  if (typeof (target as HTMLElement).click !== "function") return "NOT_FOUND";
  (target as HTMLElement).click();
  return "CLICKED";
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
