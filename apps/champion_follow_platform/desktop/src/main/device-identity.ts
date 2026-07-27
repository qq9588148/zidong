import type { NativeHelper } from "./native-helper";

export type DeviceRegistrationProof = {
  public_key_spki_der_b64: string;
  proof_der_b64: string;
};

export function deviceKeyName(localId: string): string {
  return `ChampionFollow/Device/${safeIdentifier(localId)}`;
}

export function appRefreshTarget(deviceId: string): string {
  return `ChampionFollow/AppRefresh/${safeIdentifier(deviceId)}`;
}

export async function createDeviceRegistrationProof(
  helper: NativeHelper,
  localId: string,
  canonicalChallenge: Uint8Array,
): Promise<DeviceRegistrationProof> {
  const keyName = deviceKeyName(localId);
  const publicKey = await helper.publicKeySpkiDerBase64(keyName);
  const proof = await helper.signEcdsaSha256DerBase64(
    keyName,
    canonicalChallenge,
  );
  return {
    public_key_spki_der_b64: publicKey,
    proof_der_b64: proof,
  };
}

function safeIdentifier(value: string): string {
  if (!/^[A-Za-z0-9._-]{1,128}$/.test(value)) {
    throw new Error("device_identifier_invalid");
  }
  return value;
}
