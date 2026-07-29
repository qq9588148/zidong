import { describe, expect, it } from "vitest";

import {
  parsePilotMaxConfirmedOrders,
  pilotMaxConfirmedOrdersFromArgs,
} from "../../src/main/pilot-gate";

describe("pilot confirmed-order gate", () => {
  it("keeps normal releases unlimited unless the one-order pilot is explicit", () => {
    expect(parsePilotMaxConfirmedOrders(undefined)).toBeNull();
    expect(parsePilotMaxConfirmedOrders("")).toBeNull();
    expect(parsePilotMaxConfirmedOrders("1")).toBe(1);
    expect(pilotMaxConfirmedOrdersFromArgs(["Champion Follow.exe"])).toBeNull();
    expect(pilotMaxConfirmedOrdersFromArgs([
      "Champion Follow.exe",
      "--pilot-max-confirmed-orders=1",
    ])).toBe(1);
  });

  it("fails closed for an invalid pilot value", () => {
    expect(parsePilotMaxConfirmedOrders("2")).toBe(0);
    expect(parsePilotMaxConfirmedOrders("invalid")).toBe(0);
    expect(pilotMaxConfirmedOrdersFromArgs([
      "Champion Follow.exe",
      "--pilot-max-confirmed-orders=1",
      "--pilot-max-confirmed-orders=1",
    ])).toBe(0);
  });
});
