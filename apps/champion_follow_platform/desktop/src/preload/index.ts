import { contextBridge, ipcRenderer } from "electron";

type RuntimeState = {
  generation: string;
  autoBet: "OFF" | "ON";
  executionBlock: "STARTUP_SYNC_REQUIRED" | null;
  highestTask: null;
};

type PlatformPageProbe = {
  gameVisible: boolean;
  periodCandidateCount: number;
  countdownCandidateCount: number;
  odds196Count: number;
  directionTextCounts: Record<
    "BIG" | "SMALL" | "ODD" | "EVEN" | "PRIME" | "COMPOSITE",
    number
  >;
  balanceLabelVisible: boolean;
  stakeInputCount: number;
  betControlCount: number;
  contractReady: boolean;
};

const api = Object.freeze({
  getState: (): Promise<RuntimeState> =>
    ipcRenderer.invoke("champion:get-state") as Promise<RuntimeState>,
  setAutoBet: (enabled: boolean): Promise<RuntimeState> =>
    ipcRenderer.invoke(
      "champion:set-auto-bet",
      enabled,
    ) as Promise<RuntimeState>,
  getPlatformWindowState: (): Promise<{
    open: boolean;
    probe: PlatformPageProbe | null;
  }> => ipcRenderer.invoke("champion:get-platform-window-state") as Promise<{
    open: boolean;
    probe: PlatformPageProbe | null;
  }>,
  openPlatformLogin: (): Promise<{ ok: true; open: true }> =>
    ipcRenderer.invoke("champion:open-platform-login") as Promise<{ ok: true; open: true }>,
  quitApp: (): Promise<{ ok: true }> =>
    ipcRenderer.invoke("champion:quit-app") as Promise<{ ok: true }>,
});

contextBridge.exposeInMainWorld("championFollow", api);
