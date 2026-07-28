import { Buffer } from "node:buffer";
import { describe, expect, it } from "vitest";

import {
  DeviceAuthClient,
  deviceLoginMessage,
  enrollmentMessage,
  type DeviceIdentityMetadata,
  type DeviceIdentityStore,
} from "../../src/main/auth-client";
import { appRefreshTarget, type DeviceRegistrationProof } from "../../src/main/device-identity";
import type { NativeHelper } from "../../src/main/native-helper";
import { signingKeysResponse } from "../helpers/signed-task";

const BIND_CHALLENGE = "00112233-4455-4677-8899-aabbccddeeff";
const LOGIN_CHALLENGE = "11112222-3333-4444-aaaa-bbbbccccdddd";
const ACCOUNT_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
const DEVICE_ID = "12345678-90ab-4def-9234-567890abcdef";
const LOCAL_ID = "77777777-8888-4999-aaaa-bbbbbbbbbbbb";
const NONCE = Buffer.from(Array.from({ length: 32 }, (_, index) => index));
const ACCESS_TOKEN = `access-${"a".repeat(48)}`;
const REFRESH_TOKEN = `refresh-${"b".repeat(48)}`;
const ROTATED_ACCESS_TOKEN = `access-${"c".repeat(48)}`;
const ROTATED_REFRESH_TOKEN = `refresh-${"d".repeat(48)}`;

class MemoryIdentityStore implements DeviceIdentityStore {
  saved: DeviceIdentityMetadata | null;

  constructor(initial: DeviceIdentityMetadata | null = null) {
    this.saved = initial;
  }

  async load(): Promise<DeviceIdentityMetadata | null> {
    return this.saved === null ? null : structuredClone(this.saved);
  }

  async save(value: DeviceIdentityMetadata): Promise<void> {
    this.saved = structuredClone(value);
  }
}

class FakeNativeHelper implements NativeHelper {
  readonly signed: Uint8Array[] = [];
  readonly credentials = new Map<string, string>();

  async publicKeySpkiDerBase64(): Promise<string> {
    return Buffer.alloc(91, 7).toString("base64");
  }

  async signEcdsaSha256DerBase64(
    _keyName: string,
    payload: Uint8Array,
  ): Promise<string> {
    this.signed.push(Uint8Array.from(payload));
    return Buffer.alloc(70, 9).toString("base64");
  }

  async writeCredential(target: string, value: string): Promise<void> {
    this.credentials.set(target, value);
  }

  async readCredential(target: string): Promise<string | null> {
    return this.credentials.get(target) ?? null;
  }

  async deleteCredential(target: string): Promise<void> {
    this.credentials.delete(target);
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });
}

describe("DeviceAuthClient", () => {
  it("registers, binds the CNG key, logs in, and persists only non-secret metadata", async () => {
    const requests: Array<{ url: string; body: Record<string, unknown> }> = [];
    const responses = [
      jsonResponse({ challenge_id: BIND_CHALLENGE, nonce: NONCE.toString("base64") }),
      jsonResponse({
        account_id: ACCOUNT_ID,
        device_id: DEVICE_ID,
        public_key_fingerprint: "ab".repeat(32),
      }),
      jsonResponse({ challenge_id: LOGIN_CHALLENGE, nonce: NONCE.toString("base64") }),
      jsonResponse({
        access_token: ACCESS_TOKEN,
        refresh_token: REFRESH_TOKEN,
        access_expires_at: "2030-07-28T14:00:00Z",
        device_id: DEVICE_ID,
      }),
    ];
    const store = new MemoryIdentityStore();
    const helper = new FakeNativeHelper();
    const client = new DeviceAuthClient({
      baseUrl: "https://server.example.test:8443",
      helper,
      store,
      randomUUID: () => LOCAL_ID,
      fetch: async (input, init) => {
        requests.push({
          url: String(input),
          body: JSON.parse(String(init?.body)) as Record<string, unknown>,
        });
        return responses.shift() ?? jsonResponse({}, 500);
      },
    });

    await expect(client.register({
      authorizationCode: `CF1-${"A".repeat(48)}`,
      username: "first-user",
      password: "a-local-password-with-16-chars",
    })).resolves.toEqual({ ok: true });

    expect(client.viewState()).toEqual({
      status: "ONLINE",
      registered: true,
      username: "first-user",
      deviceLabel: DEVICE_ID.slice(-8),
      errorCode: null,
    });
    expect(Buffer.from(helper.signed[0] ?? [])).toEqual(
      Buffer.from(enrollmentMessage(BIND_CHALLENGE, NONCE.toString("base64"))),
    );
    expect(Buffer.from(helper.signed[1] ?? [])).toEqual(
      Buffer.from(deviceLoginMessage(LOGIN_CHALLENGE, NONCE.toString("base64"))),
    );
    expect(helper.credentials.get(appRefreshTarget(DEVICE_ID))).toBe(
      REFRESH_TOKEN,
    );
    expect(store.saved).toMatchObject({
      version: 1,
      accountId: ACCOUNT_ID,
      deviceId: DEVICE_ID,
      localId: LOCAL_ID,
      username: "first-user",
    });
    expect(JSON.stringify(store.saved)).not.toContain("CF1-");
    expect(JSON.stringify(store.saved)).not.toContain("a-local-password");
    expect(requests.map((request) => new URL(request.url).pathname)).toEqual([
      "/api/v1/enrollment/challenge",
      "/api/v1/enrollment/register",
      "/api/v1/auth/device/challenge",
      "/api/v1/auth/device/login",
    ]);
    expect(await client.accessToken()).toBe(ACCESS_TOKEN);
  });

  it("rotates a Credential Manager refresh token on restart", async () => {
    const metadata: DeviceIdentityMetadata = {
      version: 1,
      serverBaseUrl: "https://server.example.test:8443",
      accountId: ACCOUNT_ID,
      deviceId: DEVICE_ID,
      localId: LOCAL_ID,
      username: "first-user",
    };
    const store = new MemoryIdentityStore(metadata);
    const helper = new FakeNativeHelper();
    helper.credentials.set(appRefreshTarget(DEVICE_ID), `refresh-${"e".repeat(48)}`);
    const bodies: unknown[] = [];
    const client = new DeviceAuthClient({
      baseUrl: metadata.serverBaseUrl,
      helper,
      store,
      fetch: async (_input, init) => {
        bodies.push(JSON.parse(String(init?.body)));
        return jsonResponse({
          access_token: ROTATED_ACCESS_TOKEN,
          refresh_token: ROTATED_REFRESH_TOKEN,
          access_expires_at: "2030-07-28T14:00:00Z",
          device_id: DEVICE_ID,
        });
      },
    });

    await client.initialize();

    expect(client.viewState().status).toBe("ONLINE");
    expect(bodies).toEqual([{ refresh_token: `refresh-${"e".repeat(48)}` }]);
    expect(helper.credentials.get(appRefreshTarget(DEVICE_ID))).toBe(
      ROTATED_REFRESH_TOKEN,
    );
    expect(await client.accessToken()).toBe(ROTATED_ACCESS_TOKEN);
  });

  it("rejects a non-TLS server before transmitting registration data", () => {
    expect(() => new DeviceAuthClient({
      baseUrl: "http://server.example.test:8080",
      helper: new FakeNativeHelper(),
      store: new MemoryIdentityStore(),
    })).toThrow("auth_server_tls_required");
  });

  it("fetches public task keys with an authorization header, never a URL token", async () => {
    const metadata: DeviceIdentityMetadata = {
      version: 1,
      serverBaseUrl: "https://server.example.test:8443",
      accountId: ACCOUNT_ID,
      deviceId: DEVICE_ID,
      localId: LOCAL_ID,
      username: "first-user",
    };
    const store = new MemoryIdentityStore(metadata);
    const helper = new FakeNativeHelper();
    helper.credentials.set(appRefreshTarget(DEVICE_ID), REFRESH_TOKEN);
    const requests: Array<{ url: string; authorization: string | null }> = [];
    const client = new DeviceAuthClient({
      baseUrl: metadata.serverBaseUrl,
      helper,
      store,
      fetch: async (input, init) => {
        const url = String(input);
        requests.push({
          url,
          authorization: new Headers(init?.headers).get("authorization"),
        });
        if (new URL(url).pathname === "/api/v1/auth/refresh") {
          return jsonResponse({
            access_token: ACCESS_TOKEN,
            refresh_token: ROTATED_REFRESH_TOKEN,
            access_expires_at: "2030-07-28T14:00:00Z",
            device_id: DEVICE_ID,
          });
        }
        return jsonResponse(signingKeysResponse);
      },
    });

    await client.initialize();
    await expect(client.taskSigningKeys()).resolves.toEqual(signingKeysResponse);

    const request = requests.at(-1)!;
    expect(new URL(request.url).pathname).toBe("/api/v1/auth/task-signing-keys");
    expect(new URL(request.url).search).toBe("");
    expect(request.authorization).toBe(`Bearer ${ACCESS_TOKEN}`);
  });
});
