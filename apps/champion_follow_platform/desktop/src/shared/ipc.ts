export type ExecutionBlock =
  | "STARTUP_SYNC_REQUIRED"
  | "SERVER_GLOBAL_STOP"
  | "SAFETY_SYNC_UNAVAILABLE";

export type RuntimeState = {
  generation: string;
  autoBet: "OFF" | "ON";
  executionBlock: ExecutionBlock | null;
  highestTask: null;
};

export type ConnectionViewState = {
  status: "UNREGISTERED" | "CONNECTING" | "ONLINE" | "AUTH_REQUIRED" | "OFFLINE";
  registered: boolean;
  username: string | null;
  deviceLabel: string | null;
  errorCode: string | null;
};

export type SignalTaskView =
  | {
      action: "BET";
      periodId: string;
      revision: number;
      ball: 1 | 2 | 3 | 4 | 5;
      direction: "BIG" | "SMALL" | "ODD" | "EVEN" | "PRIME" | "COMPOSITE";
      signalVersion: number;
      userLevel: "CANDIDATE" | "FORMAL" | "CORE";
    }
  | {
      action: "CANCEL";
      periodId: string;
      revision: number;
      reason:
        | "champion_withdrew"
        | "profile_downgraded"
        | "threshold_changed"
        | "collector_stale"
        | "data_gap"
        | "device_reassigned"
        | "account_disabled"
        | "device_unbound"
        | "global_stop";
    };

export type SignalViewState = {
  status:
    | "WAITING_FOR_AUTH"
    | "AUTH_REQUIRED"
    | "WAITING_FOR_PLATFORM"
    | "CONNECTING"
    | "SYNCED"
    | "OFFLINE";
  periodId: string | null;
  task: SignalTaskView | null;
  errorCode: string | null;
};

export type ClientViewState = RuntimeState & {
  connection: ConnectionViewState;
  signal: SignalViewState;
};

export type RegistrationCommand = {
  authorizationCode: string;
  username: string;
  password: string;
};

export type LoginCommand = {
  username: string;
  password: string;
};

export type PublicResult =
  | { ok: true }
  | {
      ok: false;
      code:
        | "INVALID_INPUT"
        | "SERVER_UNAVAILABLE"
        | "REGISTRATION_REJECTED"
        | "LOGIN_REJECTED"
        | "LOCAL_IDENTITY_UNAVAILABLE";
    };

export const CLIENT_IPC = Object.freeze({
  getState: "champion:get-state",
  register: "champion:register",
  login: "champion:login",
  setAutoBet: "champion:set-auto-bet",
});

export type RendererCommands = {
  getState(): Promise<ClientViewState>;
  register(input: RegistrationCommand): Promise<PublicResult>;
  login(input: LoginCommand): Promise<PublicResult>;
  setAutoBet(enabled: boolean): Promise<ClientViewState>;
};
