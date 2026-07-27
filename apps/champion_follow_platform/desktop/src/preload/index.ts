import { contextBridge, ipcRenderer } from "electron";

type RuntimeState = {
  generation: string;
  autoBet: "OFF" | "ON";
  executionBlock: "STARTUP_SYNC_REQUIRED" | null;
  highestTask: null;
};

const api = Object.freeze({
  getState: (): Promise<RuntimeState> =>
    ipcRenderer.invoke("champion:get-state") as Promise<RuntimeState>,
  setAutoBet: (enabled: boolean): Promise<RuntimeState> =>
    ipcRenderer.invoke(
      "champion:set-auto-bet",
      enabled,
    ) as Promise<RuntimeState>,
  getPlatformWindowState: (): Promise<{ open: boolean }> =>
    ipcRenderer.invoke("champion:get-platform-window-state") as Promise<{ open: boolean }>,
  openPlatformLogin: (): Promise<{ ok: true; open: true }> =>
    ipcRenderer.invoke("champion:open-platform-login") as Promise<{ ok: true; open: true }>,
});

contextBridge.exposeInMainWorld("championFollow", api);
