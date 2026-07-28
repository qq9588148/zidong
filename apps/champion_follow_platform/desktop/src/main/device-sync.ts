export type DeviceSyncSafety = Readonly<{
  deviceId: string;
  bindingEpoch: number;
  acknowledgedClientSeq: number;
  globalStopEnabled: boolean;
}>;

export function parseDeviceSyncSafety(
  value: unknown,
  expected: Readonly<{ deviceId: string; bindingEpoch: number }>,
): DeviceSyncSafety {
  if (!isObject(value) ||
      typeof value.device_id !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
        .test(value.device_id) ||
      !Number.isSafeInteger(value.binding_epoch) ||
      (value.binding_epoch as number) < 1 ||
      !Number.isSafeInteger(value.acknowledged_client_seq) ||
      (value.acknowledged_client_seq as number) < 0 ||
      typeof value.global_stop_enabled !== "boolean") {
    throw new Error("device_sync_invalid");
  }
  if (value.device_id !== expected.deviceId ||
      value.binding_epoch !== expected.bindingEpoch) {
    throw new Error("device_sync_identity_mismatch");
  }
  return Object.freeze({
    deviceId: value.device_id,
    bindingEpoch: value.binding_epoch as number,
    acknowledgedClientSeq: value.acknowledged_client_seq as number,
    globalStopEnabled: value.global_stop_enabled,
  });
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
