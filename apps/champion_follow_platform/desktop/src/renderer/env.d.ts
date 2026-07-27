type ChampionRuntimeState = {
  generation: string;
  autoBet: "OFF" | "ON";
  executionBlock: "STARTUP_SYNC_REQUIRED" | null;
  highestTask: null;
};

interface Window {
  championFollow: {
    getState(): Promise<ChampionRuntimeState>;
    setAutoBet(enabled: boolean): Promise<ChampionRuntimeState>;
  };
}
