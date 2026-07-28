const IDENTITY_KEY_BYTES = 32;

function validByte(value: unknown): value is number {
  return Number.isInteger(value) && (value as number) >= 0 &&
    (value as number) <= 255;
}

export function identityToWire(value: Uint8Array): number[] {
  if (value.length !== IDENTITY_KEY_BYTES) {
    throw new Error("identity_key_invalid");
  }
  return Array.from(value);
}

export function identityFromWire(value: unknown): Uint8Array {
  if (
    !Array.isArray(value) ||
    value.length !== IDENTITY_KEY_BYTES ||
    !value.every(validByte)
  ) {
    throw new Error("identity_key_invalid");
  }
  return Uint8Array.from(value);
}
