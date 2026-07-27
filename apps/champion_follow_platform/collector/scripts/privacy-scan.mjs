import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";

const forbidden = [
  "raw-player-marker",
  "raw-message-marker",
  "document.cookie",
  'localStorage.getItem("token")',
];

for (const file of (await readdir("dist")).filter(
  (name) => /\.(?:mjs|cjs|js)$/.test(name) && !name.endsWith(".map"),
)) {
  const body = await readFile(join("dist", file), "utf8");
  for (const marker of forbidden) {
    if (body.includes(marker)) throw new Error(`privacy_scan_failed:${file}`);
  }
}

process.stdout.write("privacy scan passed\n");
