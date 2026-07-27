import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  root: resolve(__dirname, "src/renderer"),
  plugins: [react()],
  base: "./",
  build: {
    outDir: resolve(__dirname, "dist-renderer"),
    emptyOutDir: true,
  },
  test: {
    root: __dirname,
    environment: "node",
  },
});
