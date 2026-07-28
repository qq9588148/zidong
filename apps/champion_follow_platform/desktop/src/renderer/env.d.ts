type ChampionRuntimeState = {
  generation: string;
  autoBet: "OFF" | "ON";
  executionBlock: "STARTUP_SYNC_REQUIRED" | null;
  highestTask: null;
  connection: {
    status: "UNREGISTERED" | "CONNECTING" | "ONLINE" | "AUTH_REQUIRED" | "OFFLINE";
    registered: boolean;
    username: string | null;
    deviceLabel: string | null;
    errorCode: string | null;
  };
};

type ChampionPlatformPageProbe = {
  gameVisible: boolean;
  currentPeriodId: string | null;
  countdownMs: number | null;
  periodCandidateCount: number;
  countdownCandidateCount: number;
  directionTextCounts: Record<
    "BIG" | "SMALL" | "ODD" | "EVEN" | "PRIME" | "COMPOSITE",
    number
  >;
  balanceLabelVisible: boolean;
  balanceValueReadable: boolean;
  publicBetCommandCount: number;
  stakeInputCount: number;
  betControlCount: number;
  contractReady: boolean;
};

type ChampionPlatformSessionState = {
  encryptionAvailable: boolean | null;
  snapshotLoaded: boolean;
  snapshotPresent: boolean;
  pageOriginAllowed: boolean | null;
  captureStatus: "IDLE" | "SAVED" | "UNCHANGED" | "SKIPPED" | "FAILED";
  restoreStatus: "IDLE" | "RESTORED" | "NOT_FOUND" | "SKIPPED" | "FAILED";
  errorCode: string | null;
};

interface Window {
  championFollow: {
    getState(): Promise<ChampionRuntimeState>;
    register(input: {
      authorizationCode: string;
      username: string;
      password: string;
    }): Promise<
      | { ok: true }
      | {
          ok: false;
          code:
            | "INVALID_INPUT"
            | "SERVER_UNAVAILABLE"
            | "REGISTRATION_REJECTED"
            | "LOGIN_REJECTED"
            | "LOCAL_IDENTITY_UNAVAILABLE";
        }
    >;
    login(input: { username: string; password: string }): Promise<
      | { ok: true }
      | {
          ok: false;
          code:
            | "INVALID_INPUT"
            | "SERVER_UNAVAILABLE"
            | "REGISTRATION_REJECTED"
            | "LOGIN_REJECTED"
            | "LOCAL_IDENTITY_UNAVAILABLE";
        }
    >;
    setAutoBet(enabled: boolean): Promise<ChampionRuntimeState>;
    getPlatformWindowState(): Promise<{
      open: boolean;
      probe: ChampionPlatformPageProbe | null;
      session: ChampionPlatformSessionState;
    }>;
    openPlatformLogin(): Promise<{ ok: true; open: true }>;
    quitApp(): Promise<{ ok: true }>;
  };
}
