import { build } from "esbuild";

await Promise.all([
  build({
    entryPoints: ["src/main.ts"],
    outfile: "dist/main.mjs",
    bundle: true,
    platform: "node",
    format: "esm",
    external: ["electron"],
    sourcemap: true,
  }),
  build({
    entryPoints: ["src/preload.ts"],
    outfile: "dist/preload.cjs",
    bundle: true,
    platform: "browser",
    format: "cjs",
    external: ["electron"],
    sourcemap: true,
  }),
  build({
    entryPoints: ["src/bridge/page-hook.ts"],
    outfile: "dist/page-hook.js",
    bundle: true,
    platform: "browser",
    format: "iife",
  }),
]);
