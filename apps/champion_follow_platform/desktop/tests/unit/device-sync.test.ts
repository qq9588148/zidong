import { describe, expect, it } from "vitest";

import { parseDeviceSyncSafety } from "../../src/main/device-sync";

const identity = {
  deviceId: "00000000-0000-4000-8000-000000000007",
  bindingEpoch: 3,
};

describe("device sync safety", () => {
  it("accepts the bound device and preserves the server global stop", () => {
    expect(parseDeviceSyncSafety({
      device_id: identity.deviceId,
      binding_epoch: 3,
      acknowledged_client_seq: 9,
      global_stop_enabled: true,
      last_order: null,
    }, identity)).toEqual({
      deviceId: identity.deviceId,
      bindingEpoch: 3,
      acknowledgedClientSeq: 9,
      globalStopEnabled: true,
    });
  });

  it("fails closed on missing stop state or a different binding", () => {
    expect(() => parseDeviceSyncSafety({
      device_id: identity.deviceId,
      binding_epoch: 3,
      acknowledged_client_seq: 0,
    }, identity)).toThrow("device_sync_invalid");
    expect(() => parseDeviceSyncSafety({
      device_id: identity.deviceId,
      binding_epoch: 4,
      acknowledged_client_seq: 0,
      global_stop_enabled: false,
    }, identity)).toThrow("device_sync_identity_mismatch");
  });
});
