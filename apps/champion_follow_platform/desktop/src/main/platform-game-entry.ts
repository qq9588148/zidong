export type PlatformGameEntryResult =
  | "AUTH_REQUIRED"
  | "CLICKED"
  | "NOT_FOUND";

export type PlatformBetPanelPrimeResult = "READY" | "OPENED" | "NOT_FOUND";

export function clickBtcFfcEntry(document: Document): PlatformGameEntryResult {
  if ((document.body?.innerText ?? "").includes("请先登录或注册")) {
    return "AUTH_REQUIRED";
  }
  const title = Array.from(document.querySelectorAll("p.game-title"))
    .find((element) => (element.textContent ?? "").trim() === "比特分分彩");
  const card = title?.closest(".lottery-game");
  if (card === null || card === undefined ||
      typeof (card as HTMLElement).click !== "function") return "NOT_FOUND";
  (card as HTMLElement).click();
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
