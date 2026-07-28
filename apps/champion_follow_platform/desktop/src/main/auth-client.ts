import { randomUUID as nodeRandomUUID } from "node:crypto";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

import {
  appRefreshTarget,
  createDeviceRegistrationProof,
  deviceKeyName,
} from "./device-identity";
import type { NativeHelper } from "./native-helper";

export type DeviceAuthStatus =
  | "UNREGISTERED"
  | "CONNECTING"
  | "ONLINE"
  | "AUTH_REQUIRED"
  | "OFFLINE";

export type DeviceAuthViewState = {
  status: DeviceAuthStatus;
  registered: boolean;
  username: string | null;
  deviceLabel: string | null;
  errorCode: string | null;
};

export type DeviceIdentityMetadata = {
  version: 1;
  serverBaseUrl: string;
  accountId: string;
  deviceId: string;
  localId: string;
  username: string;
};

export type DeviceRuntimeIdentity = Readonly<{
  deviceId: string;
  localId: string;
  bindingEpoch: number;
}>;

export interface DeviceIdentityStore {
  load(): Promise<DeviceIdentityMetadata | null>;
  save(value: DeviceIdentityMetadata): Promise<void>;
}

export type RegistrationInput = {
  authorizationCode: string;
  username: string;
  password: string;
};

export type LoginInput = {
  username: string;
  password: string;
};

export type PublicAuthResult =
  | { ok: true }
  | {
      ok: false;
      code:
        | "INVALID_INPUT"
        | "SERVER_UNAVAILABLE"
        | "REGISTRATION_REJECTED"
        | "LOGIN_REJECTED"
        | "LOCAL_IDENTITY_UNAVAILABLE";
    };

type FetchLike = (
  input: string | URL,
  init?: RequestInit,
) => Promise<Response>;

type DeviceAuthClientOptions = {
  baseUrl: string;
  helper: NativeHelper;
  store: DeviceIdentityStore;
  fetch?: FetchLike;
  randomUUID?: () => string;
  timeoutMs?: number;
};

type ChallengeResponse = { challenge_id: string; nonce: string };
type EnrollmentResponse = {
  account_id: string;
  device_id: string;
  public_key_fingerprint: string;
};
type SessionResponse = {
  access_token: string;
  refresh_token: string;
  access_expires_at: string;
  device_id: string;
};

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const TOKEN_PATTERN = /^[\x21-\x7e]{40,4096}$/;
const MAX_RESPONSE_BYTES = 256 * 1024;

export class JsonDeviceIdentityStore implements DeviceIdentityStore {
  constructor(private readonly path: string) {}

  async load(): Promise<DeviceIdentityMetadata | null> {
    let text: string;
    try {
      text = await readFile(this.path, "utf8");
    } catch (error) {
      if (isNodeError(error) && error.code === "ENOENT") return null;
      throw new Error("device_identity_read_failed");
    }
    if (Buffer.byteLength(text, "utf8") > 16 * 1024) {
      throw new Error("device_identity_invalid");
    }
    try {
      return parseMetadata(JSON.parse(text) as unknown);
    } catch {
      throw new Error("device_identity_invalid");
    }
  }

  async save(value: DeviceIdentityMetadata): Promise<void> {
    const metadata = parseMetadata(value);
    await mkdir(dirname(this.path), { recursive: true });
    const temporary = `${this.path}.${nodeRandomUUID()}.tmp`;
    try {
      await writeFile(temporary, `${JSON.stringify(metadata)}\n`, {
        encoding: "utf8",
        flag: "wx",
        mode: 0o600,
      });
      try {
        await rename(temporary, this.path);
      } catch (error) {
        if (!isNodeError(error) ||
            (error.code !== "EEXIST" && error.code !== "EPERM")) {
          throw error;
        }
        await rm(this.path, { force: true });
        await rename(temporary, this.path);
      }
    } catch {
      throw new Error("device_identity_write_failed");
    } finally {
      await rm(temporary, { force: true }).catch(() => undefined);
    }
  }
}

export class DeviceAuthClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: FetchLike;
  private readonly makeUUID: () => string;
  private readonly timeoutMs: number;
  private identity: DeviceIdentityMetadata | null = null;
  private access: string | null = null;
  private accessExpiresAt = 0;
  private state: DeviceAuthViewState = unregisteredState();

  constructor(private readonly options: DeviceAuthClientOptions) {
    this.baseUrl = normalizeServerBaseUrl(options.baseUrl);
    this.fetchImpl = options.fetch ?? globalThis.fetch.bind(globalThis);
    this.makeUUID = options.randomUUID ?? nodeRandomUUID;
    this.timeoutMs = options.timeoutMs ?? 8_000;
  }

  viewState(): DeviceAuthViewState {
    return { ...this.state };
  }

  deviceId(): string | null {
    return this.identity?.deviceId ?? null;
  }

  runtimeIdentity(): DeviceRuntimeIdentity | null {
    return this.identity === null ? null : {
      deviceId: this.identity.deviceId,
      localId: this.identity.localId,
      bindingEpoch: 1,
    };
  }

  async initialize(): Promise<void> {
    try {
      const identity = await this.options.store.load();
      if (identity === null) {
        this.identity = null;
        this.state = unregisteredState();
        return;
      }
      if (identity.serverBaseUrl !== this.baseUrl) {
        throw new AuthClientError("LOCAL_IDENTITY_UNAVAILABLE");
      }
      this.identity = identity;
      this.state = stateFor(identity, "CONNECTING", null);
      const refreshToken = await this.options.helper.readCredential(
        appRefreshTarget(identity.deviceId),
      );
      if (refreshToken === null) {
        this.state = stateFor(identity, "AUTH_REQUIRED", null);
        return;
      }
      await this.refresh(identity, refreshToken);
      this.state = stateFor(identity, "ONLINE", null);
    } catch (error) {
      const code = publicCode(error, "LOGIN_REJECTED");
      if (this.identity === null) {
        this.state = {
          ...unregisteredState(),
          errorCode: code,
        };
        return;
      }
      this.state = stateFor(
        this.identity,
        code === "SERVER_UNAVAILABLE" ? "OFFLINE" : "AUTH_REQUIRED",
        code,
      );
    }
  }

  async register(input: RegistrationInput): Promise<PublicAuthResult> {
    const authorizationCode = input.authorizationCode.trim();
    const username = input.username.trim();
    if (!validAuthorizationCode(authorizationCode) ||
        !validUsername(username) || !validPassword(input.password)) {
      return { ok: false, code: "INVALID_INPUT" };
    }
    this.state = {
      status: "CONNECTING",
      registered: false,
      username,
      deviceLabel: null,
      errorCode: null,
    };
    try {
      const localId = requiredUuid(this.makeUUID());
      const challenge = parseChallenge(await this.postJson(
        "/api/v1/enrollment/challenge",
        { authorization_code: authorizationCode },
        "REGISTRATION_REJECTED",
      ));
      const proof = await createDeviceRegistrationProof(
        this.options.helper,
        localId,
        enrollmentMessage(challenge.challenge_id, challenge.nonce),
      );
      const enrolled = parseEnrollment(await this.postJson(
        "/api/v1/enrollment/register",
        {
          authorization_code: authorizationCode,
          challenge_id: challenge.challenge_id,
          username,
          password: input.password,
          public_key_spki_der: proof.public_key_spki_der_b64,
          proof_der: proof.proof_der_b64,
        },
        "REGISTRATION_REJECTED",
      ));
      const identity: DeviceIdentityMetadata = {
        version: 1,
        serverBaseUrl: this.baseUrl,
        accountId: enrolled.account_id,
        deviceId: enrolled.device_id,
        localId,
        username,
      };
      await this.options.store.save(identity);
      this.identity = identity;
      await this.authenticate(identity, username, input.password);
      this.state = stateFor(identity, "ONLINE", null);
      return { ok: true };
    } catch (error) {
      const code = publicCode(error, "REGISTRATION_REJECTED");
      this.access = null;
      this.accessExpiresAt = 0;
      this.state = this.identity === null
        ? { ...unregisteredState(), username, errorCode: code }
        : stateFor(this.identity, "AUTH_REQUIRED", code);
      return { ok: false, code };
    }
  }

  async login(input: LoginInput): Promise<PublicAuthResult> {
    const username = input.username.trim();
    if (!validUsername(username) || !validPassword(input.password)) {
      return { ok: false, code: "INVALID_INPUT" };
    }
    if (this.identity === null) {
      return { ok: false, code: "LOCAL_IDENTITY_UNAVAILABLE" };
    }
    this.state = stateFor(this.identity, "CONNECTING", null);
    try {
      await this.authenticate(this.identity, username, input.password);
      this.state = stateFor(this.identity, "ONLINE", null);
      return { ok: true };
    } catch (error) {
      const code = publicCode(error, "LOGIN_REJECTED");
      this.state = stateFor(
        this.identity,
        code === "SERVER_UNAVAILABLE" ? "OFFLINE" : "AUTH_REQUIRED",
        code,
      );
      return { ok: false, code };
    }
  }

  async accessToken(): Promise<string> {
    if (this.identity === null) throw new Error("device_auth_required");
    if (this.access !== null && this.accessExpiresAt > Date.now() + 30_000) {
      return this.access;
    }
    const refreshToken = await this.options.helper.readCredential(
      appRefreshTarget(this.identity.deviceId),
    );
    if (refreshToken === null) throw new Error("device_auth_required");
    await this.refresh(this.identity, refreshToken);
    this.state = stateFor(this.identity, "ONLINE", null);
    if (this.access === null) throw new Error("device_auth_required");
    return this.access;
  }

  async taskSigningKeys(): Promise<unknown> {
    const accessToken = await this.accessToken();
    let response: Response;
    try {
      response = await this.fetchImpl(new URL(
        "/api/v1/auth/task-signing-keys",
        this.baseUrl,
      ), {
        method: "GET",
        headers: {
          "Accept": "application/json",
          "Authorization": `Bearer ${accessToken}`,
        },
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.timeout(this.timeoutMs),
      });
    } catch {
      throw new AuthClientError("SERVER_UNAVAILABLE");
    }
    if (!response.ok ||
        !response.headers.get("content-type")?.toLowerCase()
          .includes("application/json")) {
      throw new AuthClientError("SERVER_UNAVAILABLE");
    }
    const text = await response.text();
    if (Buffer.byteLength(text, "utf8") > MAX_RESPONSE_BYTES) {
      throw new AuthClientError("SERVER_UNAVAILABLE");
    }
    try {
      return JSON.parse(text) as unknown;
    } catch {
      throw new AuthClientError("SERVER_UNAVAILABLE");
    }
  }

  async deviceSync(): Promise<unknown> {
    return this.authorizedJson("/v1/device/sync", { method: "GET" });
  }

  async platformEndpointConfig(): Promise<unknown> {
    return this.authorizedJson("/api/v1/auth/platform-endpoint", { method: "GET" });
  }

  async sendClientEvent(bytes: Buffer): Promise<{ ack_seq: number }> {
    let parsed: unknown;
    try {
      parsed = JSON.parse(bytes.toString("utf8")) as unknown;
    } catch {
      throw new AuthClientError("SERVER_UNAVAILABLE");
    }
    const response = await this.authorizedJson("/v1/device/events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(parsed),
    });
    if (!isObject(response) || !Number.isSafeInteger(response.ack_seq) ||
        (response.ack_seq as number) < 1) {
      throw new AuthClientError("SERVER_UNAVAILABLE");
    }
    return { ack_seq: response.ack_seq as number };
  }

  private async authorizedJson(path: string, init: RequestInit): Promise<unknown> {
    const accessToken = await this.accessToken();
    let response: Response;
    try {
      response = await this.fetchImpl(new URL(path, this.baseUrl), {
        ...init,
        headers: {
          "Accept": "application/json",
          "Authorization": `Bearer ${accessToken}`,
          ...(init.headers ?? {}),
        },
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.timeout(this.timeoutMs),
      });
    } catch {
      throw new AuthClientError("SERVER_UNAVAILABLE");
    }
    if (!response.ok || !response.headers.get("content-type")?.toLowerCase()
      .includes("application/json")) {
      throw new AuthClientError("SERVER_UNAVAILABLE");
    }
    const text = await response.text();
    if (Buffer.byteLength(text, "utf8") > MAX_RESPONSE_BYTES) {
      throw new AuthClientError("SERVER_UNAVAILABLE");
    }
    try {
      return JSON.parse(text) as unknown;
    } catch {
      throw new AuthClientError("SERVER_UNAVAILABLE");
    }
  }

  private async authenticate(
    identity: DeviceIdentityMetadata,
    username: string,
    password: string,
  ): Promise<void> {
    const challenge = parseChallenge(await this.postJson(
      "/api/v1/auth/device/challenge",
      { username },
      "LOGIN_REJECTED",
    ));
    const proof = await this.options.helper.signEcdsaSha256DerBase64(
      deviceKeyName(identity.localId),
      deviceLoginMessage(challenge.challenge_id, challenge.nonce),
    );
    const session = parseSession(await this.postJson(
      "/api/v1/auth/device/login",
      {
        challenge_id: challenge.challenge_id,
        username,
        password,
        proof_der: proof,
      },
      "LOGIN_REJECTED",
    ));
    if (session.device_id !== identity.deviceId) {
      throw new AuthClientError("LOGIN_REJECTED");
    }
    await this.acceptSession(identity, session);
  }

  private async refresh(
    identity: DeviceIdentityMetadata,
    refreshToken: string,
  ): Promise<void> {
    if (!TOKEN_PATTERN.test(refreshToken)) {
      throw new AuthClientError("LOCAL_IDENTITY_UNAVAILABLE");
    }
    const session = parseSession(await this.postJson(
      "/api/v1/auth/refresh",
      { refresh_token: refreshToken },
      "LOGIN_REJECTED",
    ));
    if (session.device_id !== identity.deviceId) {
      throw new AuthClientError("LOGIN_REJECTED");
    }
    await this.acceptSession(identity, session);
  }

  private async acceptSession(
    identity: DeviceIdentityMetadata,
    session: SessionResponse,
  ): Promise<void> {
    await this.options.helper.writeCredential(
      appRefreshTarget(identity.deviceId),
      session.refresh_token,
    );
    this.access = session.access_token;
    this.accessExpiresAt = Date.parse(session.access_expires_at);
  }

  private async postJson(
    path: string,
    body: Record<string, unknown>,
    rejectedCode: "REGISTRATION_REJECTED" | "LOGIN_REJECTED",
  ): Promise<unknown> {
    let response: Response;
    try {
      response = await this.fetchImpl(new URL(path, this.baseUrl), {
        method: "POST",
        headers: {
          "Accept": "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.timeout(this.timeoutMs),
      });
    } catch {
      throw new AuthClientError("SERVER_UNAVAILABLE");
    }
    if (!response.ok) throw new AuthClientError(rejectedCode);
    const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
    if (!contentType.includes("application/json")) {
      throw new AuthClientError("SERVER_UNAVAILABLE");
    }
    const text = await response.text();
    if (Buffer.byteLength(text, "utf8") > MAX_RESPONSE_BYTES) {
      throw new AuthClientError("SERVER_UNAVAILABLE");
    }
    try {
      return JSON.parse(text) as unknown;
    } catch {
      throw new AuthClientError("SERVER_UNAVAILABLE");
    }
  }
}

export function enrollmentMessage(
  challengeId: string,
  nonceBase64: string,
): Uint8Array {
  return canonicalDeviceMessage(
    "champion-follow-device-bind-v1\0",
    challengeId,
    nonceBase64,
  );
}

export function deviceLoginMessage(
  challengeId: string,
  nonceBase64: string,
): Uint8Array {
  return canonicalDeviceMessage(
    "champion-follow-device-login-v1\0",
    challengeId,
    nonceBase64,
  );
}

function canonicalDeviceMessage(
  prefix: string,
  challengeId: string,
  nonceBase64: string,
): Uint8Array {
  const nonce = Buffer.from(nonceBase64, "base64");
  if (nonce.length !== 32 || nonce.toString("base64") !== nonceBase64) {
    throw new AuthClientError("SERVER_UNAVAILABLE");
  }
  const uuid = Buffer.from(requiredUuid(challengeId).replaceAll("-", ""), "hex");
  return Buffer.concat([
    Buffer.from(prefix, "utf8"),
    uuid,
    Buffer.from([0]),
    nonce,
  ]);
}

function normalizeServerBaseUrl(value: string): string {
  const url = new URL(value);
  if (url.protocol !== "https:") throw new Error("auth_server_tls_required");
  if (url.username || url.password || url.search || url.hash ||
      (url.pathname !== "/" && url.pathname !== "")) {
    throw new Error("auth_server_url_invalid");
  }
  return url.origin;
}

function parseChallenge(value: unknown): ChallengeResponse {
  if (!isObject(value) || typeof value.challenge_id !== "string" ||
      typeof value.nonce !== "string") {
    throw new AuthClientError("SERVER_UNAVAILABLE");
  }
  requiredUuid(value.challenge_id);
  const nonce = Buffer.from(value.nonce, "base64");
  if (nonce.length !== 32 || nonce.toString("base64") !== value.nonce) {
    throw new AuthClientError("SERVER_UNAVAILABLE");
  }
  return { challenge_id: value.challenge_id, nonce: value.nonce };
}

function parseEnrollment(value: unknown): EnrollmentResponse {
  if (!isObject(value) || typeof value.account_id !== "string" ||
      typeof value.device_id !== "string" ||
      typeof value.public_key_fingerprint !== "string" ||
      !/^[0-9a-f]{64}$/i.test(value.public_key_fingerprint)) {
    throw new AuthClientError("SERVER_UNAVAILABLE");
  }
  requiredUuid(value.account_id);
  requiredUuid(value.device_id);
  return {
    account_id: value.account_id,
    device_id: value.device_id,
    public_key_fingerprint: value.public_key_fingerprint,
  };
}

function parseSession(value: unknown): SessionResponse {
  if (!isObject(value) || typeof value.access_token !== "string" ||
      typeof value.refresh_token !== "string" ||
      typeof value.access_expires_at !== "string" ||
      typeof value.device_id !== "string" ||
      !TOKEN_PATTERN.test(value.access_token) ||
      !TOKEN_PATTERN.test(value.refresh_token) ||
      !Number.isFinite(Date.parse(value.access_expires_at))) {
    throw new AuthClientError("SERVER_UNAVAILABLE");
  }
  requiredUuid(value.device_id);
  return {
    access_token: value.access_token,
    refresh_token: value.refresh_token,
    access_expires_at: value.access_expires_at,
    device_id: value.device_id,
  };
}

function parseMetadata(value: unknown): DeviceIdentityMetadata {
  if (!isObject(value) || value.version !== 1 ||
      typeof value.serverBaseUrl !== "string" ||
      typeof value.accountId !== "string" ||
      typeof value.deviceId !== "string" ||
      typeof value.localId !== "string" || typeof value.username !== "string") {
    throw new Error("device_identity_invalid");
  }
  const serverBaseUrl = normalizeServerBaseUrl(value.serverBaseUrl);
  const accountId = requiredUuid(value.accountId);
  const deviceId = requiredUuid(value.deviceId);
  const localId = requiredUuid(value.localId);
  if (!validUsername(value.username)) throw new Error("device_identity_invalid");
  return {
    version: 1,
    serverBaseUrl,
    accountId,
    deviceId,
    localId,
    username: value.username,
  };
}

function requiredUuid(value: string): string {
  if (!UUID_PATTERN.test(value)) throw new AuthClientError("SERVER_UNAVAILABLE");
  return value.toLowerCase();
}

function validAuthorizationCode(value: string): boolean {
  return value.length >= 40 && value.length <= 100 && !/[\r\n\0]/.test(value);
}

function validUsername(value: string): boolean {
  return value.length >= 3 && value.length <= 80 && !/[\r\n\0]/.test(value);
}

function validPassword(value: string): boolean {
  return value.length >= 12 && value.length <= 128 && !/[\r\n\0]/.test(value);
}

function stateFor(
  identity: DeviceIdentityMetadata,
  status: Exclude<DeviceAuthStatus, "UNREGISTERED">,
  errorCode: string | null,
): DeviceAuthViewState {
  return {
    status,
    registered: true,
    username: identity.username,
    deviceLabel: identity.deviceId.slice(-8),
    errorCode,
  };
}

function unregisteredState(): DeviceAuthViewState {
  return {
    status: "UNREGISTERED",
    registered: false,
    username: null,
    deviceLabel: null,
    errorCode: null,
  };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNodeError(error: unknown): error is NodeJS.ErrnoException {
  return error instanceof Error && "code" in error;
}

class AuthClientError extends Error {
  constructor(readonly publicCode: Exclude<PublicAuthResult, { ok: true }>["code"]) {
    super("device_auth_failed");
  }
}

function publicCode(
  error: unknown,
  fallback: Exclude<PublicAuthResult, { ok: true }>["code"],
): Exclude<PublicAuthResult, { ok: true }>["code"] {
  if (error instanceof AuthClientError) return error.publicCode;
  if (error instanceof Error && error.message.startsWith("native_helper_")) {
    return "LOCAL_IDENTITY_UNAVAILABLE";
  }
  if (error instanceof Error && error.message.startsWith("device_identity_")) {
    return "LOCAL_IDENTITY_UNAVAILABLE";
  }
  return fallback;
}
