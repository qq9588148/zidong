import { describe, expect, it, vi } from "vitest";

import type { AppController } from "../../src/main/app-controller";
import { registerClientIpc } from "../../src/main/ipc-handlers";
import { CLIENT_IPC } from "../../src/shared/ipc";

describe("registerClientIpc", () => {
  it("rejects malformed renderer input before it reaches the auth client", async () => {
    const handlers = new Map<string, (_event: unknown, input?: unknown) => unknown>();
    const register = vi.fn(async () => ({ ok: true as const }));
    const controller = {
      getState: vi.fn(),
      register,
      login: vi.fn(),
      setAutoBet: vi.fn(),
    } as unknown as AppController;
    registerClientIpc({
      handle: (channel, listener) => handlers.set(channel, listener),
    }, controller);

    const result = await handlers.get(CLIENT_IPC.register)?.({}, {
      username: "client-user",
      password: 123,
      authorizationCode: "not-a-string-password",
    });

    expect(result).toEqual({ ok: false, code: "INVALID_INPUT" });
    expect(register).not.toHaveBeenCalled();
  });

  it("passes a narrow registration command without echoing a result payload", async () => {
    const handlers = new Map<string, (_event: unknown, input?: unknown) => unknown>();
    const register = vi.fn(async () => ({ ok: true as const }));
    const controller = {
      getState: vi.fn(),
      register,
      login: vi.fn(),
      setAutoBet: vi.fn(),
    } as unknown as AppController;
    registerClientIpc({
      handle: (channel, listener) => handlers.set(channel, listener),
    }, controller);
    const command = {
      username: "client-user",
      password: "a-password-that-stays-in-main-memory",
      authorizationCode: `CF1-${"A".repeat(48)}`,
    };

    const result = await handlers.get(CLIENT_IPC.register)?.({}, command);

    expect(result).toEqual({ ok: true });
    expect(register).toHaveBeenCalledWith(command);
  });
});
