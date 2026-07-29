import { createHash } from "node:crypto";
import { createReadStream, createWriteStream } from "node:fs";
import { access, mkdir, readFile, rename, rm } from "node:fs/promises";
import { dirname, join, resolve, sep } from "node:path";
import { pipeline } from "node:stream/promises";
import { fileURLToPath } from "node:url";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execute = promisify(execFile);
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const manifest = JSON.parse(await readFile(join(root, "chrome-runtime.json"), "utf8"));
const runtime = resolve(root, "runtime");
const archive = join(runtime, `chrome-${manifest.version}-${manifest.platform}.zip`);
const temporary = `${archive}.download`;
const chromeRoot = resolve(runtime, "chrome");
const executable = join(chromeRoot, "chrome-win64", "chrome.exe");

assertManifest(manifest);
assertChild(runtime, archive);
assertChild(runtime, temporary);
assertChild(runtime, chromeRoot);
await mkdir(runtime, { recursive: true });

if (await exists(executable)) process.exit(0);

if (!await exists(archive) || await sha256(archive) !== manifest.sha256) {
  await rm(temporary, { force: true });
  const response = await fetch(manifest.url);
  if (!response.ok || !response.body) throw new Error("chrome_runtime_download_failed");
  await pipeline(response.body, createWriteStream(temporary, { flags: "wx" }));
  if (await sha256(temporary) !== manifest.sha256) {
    await rm(temporary, { force: true });
    throw new Error("chrome_runtime_hash_mismatch");
  }
  await rm(archive, { force: true });
  await rename(temporary, archive);
}

await rm(chromeRoot, { recursive: true, force: true });
await mkdir(chromeRoot, { recursive: true });
await execute("tar.exe", ["-xf", archive, "-C", chromeRoot], {
  windowsHide: true,
  timeout: 300_000,
});
if (!await exists(executable)) throw new Error("chrome_runtime_extract_failed");

function assertManifest(value) {
  if (!value || typeof value !== "object" ||
      !/^\d+(?:\.\d+){3}$/.test(value.version) ||
      value.platform !== "win64" ||
      typeof value.url !== "string" ||
      !value.url.startsWith("https://storage.googleapis.com/chrome-for-testing-public/") ||
      !/^[a-f0-9]{64}$/.test(value.sha256)) {
    throw new Error("chrome_runtime_manifest_invalid");
  }
}

function assertChild(parent, child) {
  if (!child.startsWith(resolve(parent) + sep)) {
    throw new Error("chrome_runtime_path_invalid");
  }
}

async function exists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

async function sha256(path) {
  const hash = createHash("sha256");
  await pipeline(createReadStream(path), hash);
  return hash.digest("hex");
}
