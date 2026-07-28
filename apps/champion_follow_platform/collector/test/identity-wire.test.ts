import { describe, expect, it } from "vitest";

import {
  identityFromWire,
  identityToWire,
} from "../src/identity-wire.js";

describe("collector identity IPC wire format", () => {
  it("round-trips exactly 32 bytes through structured clone arrays", () => {
    const source = new Uint8Array(32).map((_value, index) => index + 1);

    const wire = identityToWire(source);
    const restored = identityFromWire(wire);

    expect(wire).toEqual(Array.from(source));
    expect(restored).toEqual(source);
    expect(restored).not.toBe(source);
  });

  it.each([
    null,
    new Uint8Array(31),
    Array(31).fill(1),
    [...Array(31).fill(1), -1],
    [...Array(31).fill(1), 256],
    [...Array(31).fill(1), 1.5],
  ])("rejects an invalid identity value: %j", (value) => {
    expect(() => identityFromWire(value)).toThrow("identity_key_invalid");
  });
});
