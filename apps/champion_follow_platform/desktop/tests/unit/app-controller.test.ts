import { describe, expect, it, vi } from "vitest";

import { AppController } from "../../src/main/app-controller";

const signal = {
  viewState: () => ({
    status: "SYNCED" as const,
    periodId: "2607290001",
    task: null,
    errorCode: null,
  }),
};

describe("AppController automatic execution gate", () => {
  it("arms only when authentication and the execution runtime are ready", () => {
    const setEnabled = vi.fn();
    const auth = {
      initialize: vi.fn(),
      viewState: () => ({
        status: "ONLINE" as const,
        registered: true,
        username: "fixture-user",
        deviceLabel: "fixture",
        errorCode: null,
      }),
    };
    const controller = new AppController(auth as never, signal, {
      canEnable: () => true,
      setEnabled,
    });

    expect(controller.setAutoBet(true).autoBet).toBe("ON");
    expect(controller.getState().executionBlock).toBeNull();
    expect(setEnabled).toHaveBeenCalledWith(true);
    expect(controller.setAutoBet(false).autoBet).toBe("OFF");
    expect(setEnabled).toHaveBeenLastCalledWith(false);
  });

  it("stays disarmed when the execution runtime is not ready", () => {
    const setEnabled = vi.fn();
    const auth = {
      initialize: vi.fn(),
      viewState: () => ({
        status: "ONLINE" as const,
        registered: true,
        username: "fixture-user",
        deviceLabel: "fixture",
        errorCode: null,
      }),
    };
    const controller = new AppController(auth as never, signal, {
      canEnable: () => false,
      setEnabled,
    });

    expect(controller.setAutoBet(true).autoBet).toBe("OFF");
    expect(controller.getState().executionBlock).toBe("STARTUP_SYNC_REQUIRED");
    expect(setEnabled).not.toHaveBeenCalledWith(true);
  });
});
