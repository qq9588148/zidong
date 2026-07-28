import { EventEmitter } from "node:events";

import { describe, expect, it } from "vitest";

import { ignoreBrokenPipe } from "../src/process-output.js";

describe("collector process output", () => {
  it("does not crash a packaged GUI when its launch pipe has closed", () => {
    const stream = new EventEmitter();
    ignoreBrokenPipe(stream);

    expect(() =>
      stream.emit("error", Object.assign(new Error("broken pipe"), {
        code: "EPIPE",
      })),
    ).not.toThrow();
  });

  it("does not hide unrelated output failures", () => {
    const stream = new EventEmitter();
    ignoreBrokenPipe(stream);

    expect(() =>
      stream.emit("error", Object.assign(new Error("unexpected"), {
        code: "EIO",
      })),
    ).toThrow("unexpected");
  });
});
