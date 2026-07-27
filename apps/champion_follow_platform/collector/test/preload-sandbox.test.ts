import { describe, expect, it } from "vitest";

import { build } from "esbuild";

describe("sandbox preload build", () => {
  it("bundles for Chromium without importing Node crypto", async () => {
    const result = await build({
      entryPoints: ["src/preload.ts"],
      bundle: true,
      write: false,
      platform: "browser",
      format: "cjs",
      external: ["electron"],
    });
    const bundle = result.outputFiles[0]?.text ?? "";

    expect(bundle).not.toMatch(/node:crypto|require\(["'](?:node:)?crypto["']\)/);
  });
});
