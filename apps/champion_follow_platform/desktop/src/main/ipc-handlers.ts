import type { AppController } from "./app-controller";
import {
  CLIENT_IPC,
  type LoginCommand,
  type PublicResult,
  type RegistrationCommand,
} from "../shared/ipc";

type IpcMainLike = {
  handle(channel: string, listener: (_event: unknown, input?: unknown) => unknown): void;
};

const INVALID_INPUT: PublicResult = { ok: false, code: "INVALID_INPUT" };

export function registerClientIpc(
  ipc: IpcMainLike,
  controller: AppController,
): void {
  ipc.handle(CLIENT_IPC.getState, () => controller.getState());
  ipc.handle(CLIENT_IPC.register, (_event, input) => {
    const parsed = registrationCommand(input);
    return parsed === null ? INVALID_INPUT : controller.register(parsed);
  });
  ipc.handle(CLIENT_IPC.login, (_event, input) => {
    const parsed = loginCommand(input);
    return parsed === null ? INVALID_INPUT : controller.login(parsed);
  });
  ipc.handle(CLIENT_IPC.setAutoBet, (_event, enabled) =>
    controller.setAutoBet(enabled === false ? false : true));
}

function registrationCommand(value: unknown): RegistrationCommand | null {
  if (!isObject(value) || typeof value.authorizationCode !== "string" ||
      typeof value.username !== "string" || typeof value.password !== "string") {
    return null;
  }
  return {
    authorizationCode: value.authorizationCode,
    username: value.username,
    password: value.password,
  };
}

function loginCommand(value: unknown): LoginCommand | null {
  if (!isObject(value) || typeof value.username !== "string" ||
      typeof value.password !== "string") {
    return null;
  }
  return { username: value.username, password: value.password };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
