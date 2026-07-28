export type RuntimeState = {
  generation: string;
  autoBet: "OFF" | "ON";
  executionBlock: "STARTUP_SYNC_REQUIRED" | null;
  highestTask: null;
};

export type ConnectionViewState = {
  status: "UNREGISTERED" | "CONNECTING" | "ONLINE" | "AUTH_REQUIRED" | "OFFLINE";
  registered: boolean;
  username: string | null;
  deviceLabel: string | null;
  errorCode: string | null;
};

export type ClientViewState = RuntimeState & {
  connection: ConnectionViewState;
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
