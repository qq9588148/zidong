import { randomUUID } from "node:crypto";

import type { DeviceAuthClient } from "./auth-client";
import type {
  ClientViewState,
  LoginCommand,
  PublicResult,
  RegistrationCommand,
  RuntimeState,
} from "../shared/ipc";

export const initialRuntimeState = (): RuntimeState => ({
  generation: randomUUID(),
  autoBet: "OFF",
  executionBlock: "STARTUP_SYNC_REQUIRED",
  highestTask: null,
});

export class AppController {
  private runtime = initialRuntimeState();

  constructor(private readonly auth: DeviceAuthClient) {}

  async initialize(): Promise<void> {
    await this.auth.initialize();
  }

  getState(): ClientViewState {
    return {
      ...this.runtime,
      connection: this.auth.viewState(),
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
      this.runtime = { ...this.runtime, autoBet: "OFF" };
    }
    return this.getState();
  }
}
