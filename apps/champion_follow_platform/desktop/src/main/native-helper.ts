import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { spawn } from "node:child_process";

type NativeCommand =
  | { command: "public_key_spki_der"; keyName: string }
  | { command: "sign_ecdsa_sha256_der"; keyName: string; payloadBase64: string }
  | { command: "credential_write"; target: string; value: string }
  | { command: "credential_read"; target: string }
  | { command: "credential_delete"; target: string };

type NativeResponse = Record<string, unknown> & {
  ok?: boolean;
  error?: string;
};

type SpawnJson = (
  executablePath: string,
  command: NativeCommand,
  timeoutMs: number,
) => Promise<NativeResponse>;

type NativeHelperDependencies = {
  readBytes: (path: string) => Promise<Uint8Array>;
  spawnJson: SpawnJson;
};

export interface NativeHelper {
  publicKeySpkiDerBase64(keyName: string): Promise<string>;
  signEcdsaSha256DerBase64(
    keyName: string,
    payload: Uint8Array,
  ): Promise<string>;
  writeCredential(target: string, value: string): Promise<void>;
  readCredential(target: string): Promise<string | null>;
  deleteCredential(target: string): Promise<void>;
}

const MAX_OUTPUT_BYTES = 64 * 1024;

async function defaultSpawnJson(
  executablePath: string,
  command: NativeCommand,
  timeoutMs: number,
): Promise<NativeResponse> {
  return new Promise((resolve, reject) => {
    const child = spawn(executablePath, [], {
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    const stdout: Buffer[] = [];
    let stdoutBytes = 0;
    let settled = false;

    const finishReject = (code: string) => {
      if (settled) return;
      settled = true;
      reject(new Error(code));
    };
    const timer = setTimeout(() => {
      child.kill();
      finishReject("native_helper_timeout");
    }, timeoutMs);

    child.stdout.on("data", (chunk: Buffer) => {
      stdoutBytes += chunk.length;
      if (stdoutBytes > MAX_OUTPUT_BYTES) {
        child.kill();
        finishReject("native_helper_output_too_large");
        return;
      }
      stdout.push(chunk);
    });
    child.stderr.resume();
    child.once("error", () => finishReject("native_helper_start_failed"));
    child.once("close", () => {
      clearTimeout(timer);
      if (settled) return;
      try {
        const parsed = JSON.parse(Buffer.concat(stdout).toString("utf8")) as NativeResponse;
        if (parsed.ok !== true) {
          const error = typeof parsed.error === "string"
            ? parsed.error
            : "NATIVE_HELPER_FAILURE";
          finishReject(`native_helper_failed:${error}`);
          return;
        }
        settled = true;
        resolve(parsed);
      } catch {
        finishReject("native_helper_invalid_response");
      }
    });

    child.stdin.end(`${JSON.stringify(command)}\n`);
  });
}

const defaultDependencies: NativeHelperDependencies = {
  readBytes: readFile,
  spawnJson: defaultSpawnJson,
};

export class ProcessNativeHelper implements NativeHelper {
  private readonly dependencies: NativeHelperDependencies;

  constructor(
    private readonly executablePath: string,
    private readonly expectedSha256: string,
    dependencies: Partial<NativeHelperDependencies> = {},
    private readonly timeoutMs = 3_000,
  ) {
    if (!/^[a-f0-9]{64}$/i.test(expectedSha256)) {
      throw new Error("native_helper_manifest_invalid");
    }
    this.dependencies = { ...defaultDependencies, ...dependencies };
  }

  async publicKeySpkiDerBase64(keyName: string): Promise<string> {
    const response = await this.invoke({ command: "public_key_spki_der", keyName });
    return requiredBase64(response, "publicKeySpkiDerBase64");
  }

  async signEcdsaSha256DerBase64(
    keyName: string,
    payload: Uint8Array,
  ): Promise<string> {
    const response = await this.invoke({
      command: "sign_ecdsa_sha256_der",
      keyName,
      payloadBase64: Buffer.from(payload).toString("base64"),
    });
    return requiredBase64(response, "signatureDerBase64");
  }

  async writeCredential(target: string, value: string): Promise<void> {
    await this.invoke({ command: "credential_write", target, value });
  }

  async readCredential(target: string): Promise<string | null> {
    const response = await this.invoke({ command: "credential_read", target });
    if (response.value === null) return null;
    if (typeof response.value !== "string") {
      throw new Error("native_helper_invalid_response");
    }
    return response.value;
  }

  async deleteCredential(target: string): Promise<void> {
    await this.invoke({ command: "credential_delete", target });
  }

  private async invoke(command: NativeCommand): Promise<NativeResponse> {
    const bytes = await this.dependencies.readBytes(this.executablePath);
    const actualSha256 = createHash("sha256").update(bytes).digest("hex");
    if (actualSha256.toLowerCase() !== this.expectedSha256.toLowerCase()) {
      throw new Error("native_helper_integrity_mismatch");
    }
    return this.dependencies.spawnJson(this.executablePath, command, this.timeoutMs);
  }
}

function requiredBase64(response: NativeResponse, name: string): string {
  const value = response[name];
  if (typeof value !== "string" || value.length === 0 ||
      !/^[A-Za-z0-9+/]+={0,2}$/.test(value)) {
    throw new Error("native_helper_invalid_response");
  }
  return value;
}
