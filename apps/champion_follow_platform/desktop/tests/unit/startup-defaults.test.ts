import { describe, expect, it } from "vitest";

import { initialRuntimeState } from "../../src/main/index";

describe("initialRuntimeState", () => {
  it("always starts disarmed and without an executable task", () => {
    expect(initialRuntimeState()).toEqual({
      generation: expect.any(String),
      autoBet: "OFF",
      executionBlock: "STARTUP_SYNC_REQUIRED",
      highestTask: null,
    });
  });

  it("creates a fresh generation for every process start", () => {
    expect(initialRuntimeState().generation).not.toBe(
      initialRuntimeState().generation,
    );
  });
});
