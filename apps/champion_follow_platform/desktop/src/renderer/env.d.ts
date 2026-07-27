type ChampionRuntimeState = {
  generation: string;
  autoBet: "OFF" | "ON";
  executionBlock: "STARTUP_SYNC_REQUIRED" | null;
  highestTask: null;
};

type ChampionPlatformPageProbe = {
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

interface Window {
  championFollow: {
    getState(): Promise<ChampionRuntimeState>;
    setAutoBet(enabled: boolean): Promise<ChampionRuntimeState>;
    getPlatformWindowState(): Promise<{
      open: boolean;
      probe: ChampionPlatformPageProbe | null;
    }>;
    openPlatformLogin(): Promise<{ ok: true; open: true }>;
    quitApp(): Promise<{ ok: true }>;
  };
}
