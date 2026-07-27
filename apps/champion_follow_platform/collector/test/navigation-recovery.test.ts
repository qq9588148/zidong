import { describe, expect, it } from "vitest";

import { NavigationRecoveryCoordinator } from "../src/navigation-recovery.js";

function deferred(): { promise: Promise<void>; resolve(): void } {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("NavigationRecoveryCoordinator", () => {
  it("injects a reload before session reconciliation and restarts loops only after history", async () => {
    const order: string[] = [];
    const coordinator = new NavigationRecoveryCoordinator({
      async pause() {
        order.push("pause");
      },
      resetHistory(generation) {
        order.push(`reset:${generation}`);
      },
      async reconcileSession() {
        order.push("session");
      },
      async injectPageHook(generation) {
        order.push(`inject:${generation}`);
      },
      async waitForSessionRetry() {},
      sessionReady(generation) {
        order.push(`ready:${generation}`);
      },
      startLoops() {
        order.push("loops");
      },
      failClosed() {
        order.push("failed");
      },
    });

    const first = coordinator.committedMainFrame();
    await first.done;
    expect(order).toEqual(["reset:1", "inject:1", "ready:1"]);

    const reload = coordinator.committedMainFrame();
    await reload.done;
    expect(order).toEqual([
      "reset:1",
      "inject:1",
      "ready:1",
      "pause",
      "reset:2",
      "inject:2",
      "session",
      "ready:2",
    ]);
    expect(coordinator.acceptsPageState(reload.generation)).toBe(true);

    coordinator.historyRecovered(reload.generation);
    coordinator.historyRecovered(reload.generation);
    expect(order.at(-1)).toBe("loops");
    expect(order.filter((value) => value === "loops")).toHaveLength(1);
  });

  it("lets only the newest rapid reload inject and restart loops", async () => {
    const order: string[] = [];
    const paused = deferred();
    let pauseCalls = 0;
    const coordinator = new NavigationRecoveryCoordinator({
      async pause() {
        pauseCalls += 1;
        order.push(`pause:${pauseCalls}`);
        if (pauseCalls === 1) await paused.promise;
      },
      resetHistory(generation) {
        order.push(`reset:${generation}`);
      },
      async reconcileSession() {
        order.push("session");
      },
      async injectPageHook(generation) {
        order.push(`inject:${generation}`);
      },
      async waitForSessionRetry() {},
      sessionReady(generation) {
        order.push(`ready:${generation}`);
      },
      startLoops() {
        order.push("loops");
      },
      failClosed() {
        order.push("failed");
      },
    });

    await coordinator.committedMainFrame().done;
    const older = coordinator.committedMainFrame();
    await Promise.resolve();
    const newer = coordinator.committedMainFrame();
    paused.resolve();
    await Promise.all([older.done, newer.done]);

    expect(coordinator.acceptsPageState(older.generation)).toBe(false);
    expect(coordinator.acceptsPageState(newer.generation)).toBe(true);
    expect(order).not.toContain(`inject:${older.generation}`);
    expect(order).toContain(`inject:${newer.generation}`);

    coordinator.historyRecovered(older.generation);
    expect(order).not.toContain("loops");
    coordinator.historyRecovered(newer.generation);
    expect(order.at(-1)).toBe("loops");
  });

  it("ignores a superseded page injection failure", async () => {
    const order: string[] = [];
    const oldInjection = deferred();
    let rejectOld!: (error: Error) => void;
    const oldFailure = new Promise<void>((_resolve, reject) => {
      rejectOld = reject;
    });
    const coordinator = new NavigationRecoveryCoordinator({
      async pause() {
        order.push("pause");
      },
      resetHistory(generation) {
        order.push(`reset:${generation}`);
      },
      async reconcileSession() {
        order.push("session");
      },
      async injectPageHook(generation) {
        order.push(`inject:${generation}`);
        if (generation === 2) {
          oldInjection.resolve();
          await oldFailure;
        }
      },
      async waitForSessionRetry() {},
      sessionReady(generation) {
        order.push(`ready:${generation}`);
      },
      startLoops() {
        order.push("loops");
      },
      failClosed() {
        order.push("failed");
      },
    });

    await coordinator.committedMainFrame().done;
    const older = coordinator.committedMainFrame();
    await oldInjection.promise;
    const newer = coordinator.committedMainFrame();
    rejectOld(new Error("frame destroyed"));
    await Promise.all([older.done, newer.done]);

    expect(order).not.toContain("failed");
    expect(coordinator.acceptsPageState(newer.generation)).toBe(true);
  });

  it("fails closed when reload reconciliation fails", async () => {
    const order: string[] = [];
    const coordinator = new NavigationRecoveryCoordinator({
      async pause() {
        order.push("pause");
      },
      resetHistory() {
        order.push("reset");
      },
      async reconcileSession() {
        throw new Error("private-detail");
      },
      async injectPageHook() {
        order.push("inject");
      },
      async waitForSessionRetry() {},
      sessionReady() {
        order.push("ready");
      },
      startLoops() {
        order.push("loops");
      },
      failClosed() {
        order.push("failed");
      },
    });

    await coordinator.committedMainFrame().done;
    const reload = coordinator.committedMainFrame();
    await expect(reload.done).rejects.toThrow("collector_reload_failed");
    expect(order).toEqual([
      "reset",
      "inject",
      "ready",
      "pause",
      "reset",
      "inject",
      "failed",
    ]);
    expect(coordinator.acceptsPageState(reload.generation)).toBe(false);
  });

  it("keeps the reloaded page accepted while a network session retry is pending", async () => {
    const retry = deferred();
    const retryStarted = deferred();
    let attempts = 0;
    let failed = false;
    const coordinator = new NavigationRecoveryCoordinator({
      async pause() {},
      resetHistory() {},
      async reconcileSession() {
        attempts += 1;
        if (attempts === 1) throw new Error("collector_network_error");
      },
      async injectPageHook() {},
      async waitForSessionRetry() {
        retryStarted.resolve();
        await retry.promise;
      },
      sessionReady() {},
      startLoops() {},
      failClosed() {
        failed = true;
      },
    });

    await coordinator.committedMainFrame().done;
    const reload = coordinator.committedMainFrame();
    await retryStarted.promise;

    expect(coordinator.acceptsPageState(reload.generation)).toBe(true);
    expect(coordinator.historyReady(reload.generation)).toBe(false);
    expect(failed).toBe(false);

    retry.resolve();
    await reload.done;
    expect(attempts).toBe(2);
    expect(coordinator.historyReady(reload.generation)).toBe(true);
  });

  it("cancels a pending reconnect without failing closed during shutdown", async () => {
    const retryStarted = deferred();
    let failed = false;
    const coordinator = new NavigationRecoveryCoordinator({
      async pause() {},
      resetHistory() {},
      async reconcileSession() {
        throw new Error("collector_network_error");
      },
      async injectPageHook() {},
      async waitForSessionRetry(signal) {
        retryStarted.resolve();
        await new Promise<void>((resolve) => {
          signal.addEventListener("abort", () => resolve(), { once: true });
        });
      },
      sessionReady() {},
      startLoops() {},
      failClosed() {
        failed = true;
      },
    });

    await coordinator.committedMainFrame().done;
    const reload = coordinator.committedMainFrame();
    await retryStarted.promise;
    await coordinator.stop();
    await reload.done;
    expect(failed).toBe(false);
    expect(coordinator.acceptsPageState(reload.generation)).toBe(false);
  });
});
