import { describe, expect, it, vi } from "vitest";

import { configureCollectorDiagnostics } from "../src/diagnostic-port.js";

describe("configureCollectorDiagnostics", () => {
  it("is disabled by default", () => {
    const appendSwitch = vi.fn();
    expect(configureCollectorDiagnostics({ appendSwitch }, undefined)).toBe(false);
    expect(appendSwitch).not.toHaveBeenCalled();
  });

  it("binds an explicitly enabled diagnostic port to localhost", () => {
    const appendSwitch = vi.fn();
    expect(configureCollectorDiagnostics({ appendSwitch }, "1")).toBe(true);
    expect(appendSwitch.mock.calls).toEqual([
      ["remote-debugging-address", "127.0.0.1"],
      ["remote-debugging-port", "9223"],
    ]);
  });

  it("rejects unexpected diagnostic settings", () => {
    expect(() =>
      configureCollectorDiagnostics({ appendSwitch: vi.fn() }, "yes")
    ).toThrow("collector_diagnostics_invalid");
  });
});
