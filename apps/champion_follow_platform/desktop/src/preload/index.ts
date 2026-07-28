import { contextBridge, ipcRenderer } from "electron";

import type {
  ClientViewState,
  LoginCommand,
  PublicResult,
  RegistrationCommand,
} from "../shared/ipc";

const CLIENT_IPC = Object.freeze({
  getState: "champion:get-state",
  register: "champion:register",
  login: "champion:login",
  setAutoBet: "champion:set-auto-bet",
});

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

type PlatformSessionPersistenceState = {
  encryptionAvailable: boolean | null;
  snapshotLoaded: boolean;
  snapshotPresent: boolean;
  pageOriginAllowed: boolean | null;
  captureStatus: "IDLE" | "SAVED" | "UNCHANGED" | "SKIPPED" | "FAILED";
  restoreStatus: "IDLE" | "RESTORED" | "NOT_FOUND" | "SKIPPED" | "FAILED";
  errorCode: string | null;
};

const api = Object.freeze({
  getState: (): Promise<ClientViewState> =>
    ipcRenderer.invoke(CLIENT_IPC.getState) as Promise<ClientViewState>,
  register: (input: RegistrationCommand): Promise<PublicResult> =>
    ipcRenderer.invoke(CLIENT_IPC.register, input) as Promise<PublicResult>,
  login: (input: LoginCommand): Promise<PublicResult> =>
    ipcRenderer.invoke(CLIENT_IPC.login, input) as Promise<PublicResult>,
  setAutoBet: (enabled: boolean): Promise<ClientViewState> =>
    ipcRenderer.invoke(
      CLIENT_IPC.setAutoBet,
      enabled,
    ) as Promise<ClientViewState>,
  getPlatformWindowState: (): Promise<{
    open: boolean;
    probe: PlatformPageProbe | null;
    session: PlatformSessionPersistenceState;
  }> => ipcRenderer.invoke("champion:get-platform-window-state") as Promise<{
    open: boolean;
    probe: PlatformPageProbe | null;
    session: PlatformSessionPersistenceState;
  }>,
  openPlatformLogin: (): Promise<{ ok: true; open: true }> =>
    ipcRenderer.invoke("champion:open-platform-login") as Promise<{ ok: true; open: true }>,
  quitApp: (): Promise<{ ok: true }> =>
    ipcRenderer.invoke("champion:quit-app") as Promise<{ ok: true }>,
});

contextBridge.exposeInMainWorld("championFollow", api);
