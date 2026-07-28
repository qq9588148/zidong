export type PlatformGameEntryResult =
  | "AUTH_REQUIRED"
  | "LOBBY_OPENED"
  | "CLICKED"
  | "NOT_FOUND";

export type PlatformBetPanelPrimeResult = "READY" | "OPENED" | "NOT_FOUND";

export function clickBtcFfcEntry(document: Document): PlatformGameEntryResult {
  const exactTitle = Array.from(document.querySelectorAll("p.game-title"))
    .find((element) => (element.textContent ?? "").trim() === "比特分分彩");
  const fallbackTitle = exactTitle ?? Array.from(document.querySelectorAll("*"))
    .find((element) => element.children.length === 0 &&
      (element.textContent ?? "")
      .replace(/\s+/g, "").trim() === "比特分分彩");
  if (fallbackTitle === undefined) {
    const lobbyLabel = Array.from(document.querySelectorAll("*"))
      .find((element) => element.children.length === 0 &&
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

export function primeBtcFfcBetPanel(
  document: Document,
): PlatformBetPanelPrimeResult {
  const panel = document.querySelector<HTMLElement>(".Game28Follow");
  if (panel && (panel.offsetWidth || panel.offsetHeight ||
      panel.getClientRects().length)) return "READY";
  const compact = (value: string | null) =>
    (value ?? "").replace(/\s+/g, "").trim();
  const button = Array.from(document.querySelectorAll<HTMLButtonElement>("button"))
    .find((element) => !element.disabled &&
      element.closest(".Game28Follow") === null &&
      compact(element.textContent) === "投注");
  if (!button) return "NOT_FOUND";
  button.click();
  return "OPENED";
}
