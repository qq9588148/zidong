import { randomUUID } from "node:crypto";

import type { DeviceAuthClient } from "./auth-client";
import type {
  ClientViewState,
  LoginCommand,
  PublicResult,
  RegistrationCommand,
  RuntimeState,
  SignalViewState,
} from "../shared/ipc";

type ExecutionControl = {
  canEnable(): boolean;
  setEnabled(enabled: boolean): void;
  start?(): void;
  stop?(): void;
  isEnabled?(): boolean;
};

const disabledExecutionControl: ExecutionControl = {
  canEnable: () => false,
  setEnabled: () => undefined,
};

export const initialRuntimeState = (): RuntimeState => ({
  generation: randomUUID(),
  autoBet: "OFF",
  executionBlock: "STARTUP_SYNC_REQUIRED",
  highestTask: null,
});

export class AppController {
  private runtime = initialRuntimeState();

  constructor(
    private readonly auth: DeviceAuthClient,
    private readonly signals: { viewState(): SignalViewState } = {
      viewState: () => ({
        status: "WAITING_FOR_AUTH",
        periodId: null,
        task: null,
        errorCode: null,
      }),
    },
    private readonly execution: ExecutionControl = disabledExecutionControl,
  ) {}

  async initialize(): Promise<void> {
    await this.auth.initialize();
    this.execution.start?.();
  }

  getState(): ClientViewState {
    if (this.runtime.autoBet === "ON" &&
        this.execution.isEnabled?.() === false) {
      this.runtime = {
        ...this.runtime,
        autoBet: "OFF",
        executionBlock: "STARTUP_SYNC_REQUIRED",
      };
    }
    return {
      ...this.runtime,
      connection: this.auth.viewState(),
      signal: this.signals.viewState(),
    };
  }

  async register(input: RegistrationCommand): Promise<PublicResult> {
    return this.auth.register(input);
  }

  async login(input: LoginCommand): Promise<PublicResult> {
    return this.auth.login(input);
  }

  setAutoBet(enabled: boolean): ClientViewState {
    if (enabled === false) {
      this.execution.setEnabled(false);
      this.runtime = {
        ...this.runtime,
        autoBet: "OFF",
        executionBlock: "STARTUP_SYNC_REQUIRED",
      };
    } else if (
      this.auth.viewState().status === "ONLINE" &&
      this.execution.canEnable()
    ) {
      this.execution.setEnabled(true);
      if (this.execution.isEnabled?.() !== false) {
        this.runtime = {
          ...this.runtime,
          autoBet: "ON",
          executionBlock: null,
        };
      }
    }
    return this.getState();
  }
}
