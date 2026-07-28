import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const desktop = join(dirname(fileURLToPath(import.meta.url)), "..");
const project = join(
  desktop,
  "native",
  "ChampionFollow.DeviceIdentity",
  "ChampionFollow.DeviceIdentity.csproj",
);
const output = join(
  desktop,
  "native",
  "ChampionFollow.DeviceIdentity",
  "bin",
  "Release",
  "net10.0-windows",
  "win-x64",
  "publish",
);

execFileSync("dotnet", [
  "publish",
  project,
  "--configuration", "Release",
  "--self-contained", "false",
  "--no-restore",
  "--output", output,
  "-p:PublishSingleFile=true",
  "-p:PublishTrimmed=false",
  "-p:DebugType=None",
], { stdio: "inherit", windowsHide: true });

const executable = join(output, "ChampionFollow.DeviceIdentity.exe");
const sha256 = createHash("sha256").update(readFileSync(executable)).digest("hex");
writeFileSync(
  join(output, "ChampionFollow.DeviceIdentity.sha256"),
  `${sha256}\n`,
  { encoding: "utf8" },
);
