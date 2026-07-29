import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  chromeLaunchArguments,
  parseDevToolsActivePort,
  selectPlatformPageTarget,
} from "../../src/main/chrome-controller";

describe("dedicated Chrome controller", () => {
  it("launches with a non-default profile and a private debugging port", () => {
    const args = chromeLaunchArguments({
      profileDirectory: "C:/client/chrome-profile",
      initialUrl: "https://ng888.com/",
      proxyUrl: "http://127.0.0.1:25378",
    });

    expect(args).toContain("--user-data-dir=C:/client/chrome-profile");
    expect(args).toContain("--remote-debugging-port=0");
    expect(args).toContain("--proxy-server=http://127.0.0.1:25378");
    expect(args.at(-1)).toBe("https://ng888.com/");
    expect(args.join(" ")).not.toMatch(/password|cookie|token/i);
    expect(args.join(" ")).not.toMatch(/proxy-bypass-list|host-resolver-rules/i);
  });

  it("parses only a loopback DevTools endpoint", () => {
    expect(parseDevToolsActivePort("65046\n/devtools/browser/abc\n"))
      .toEqual({
        port: 65046,
        browserWebSocketUrl: "ws://127.0.0.1:65046/devtools/browser/abc",
      });
    expect(() => parseDevToolsActivePort("0\n/devtools/browser/abc\n"))
      .toThrow("chrome_debug_endpoint_invalid");
    expect(() => parseDevToolsActivePort("65046\nws://other.example/x\n"))
      .toThrow("chrome_debug_endpoint_invalid");
  });

  it("selects only an HTTPS page target", () => {
    expect(selectPlatformPageTarget([
      { id: "bg", type: "background_page", url: "chrome-extension://x/" },
      {
        id: "page",
        type: "page",
        url: "https://random.example/home",
        webSocketDebuggerUrl: "ws://127.0.0.1:65046/devtools/page/page",
      },
    ])?.id).toBe("page");
    expect(selectPlatformPageTarget([
      {
        id: "file",
        type: "page",
        url: "file:///C:/private.txt",
        webSocketDebuggerUrl: "ws://127.0.0.1:65046/devtools/page/file",
      },
    ])).toBeNull();
  });

  it("packages the pinned Chrome runtime for a clean Windows computer", () => {
    const packageJson = JSON.parse(readFileSync(
      join(process.cwd(), "package.json"),
      "utf8",
    ));
    const runtime = JSON.parse(readFileSync(
      join(process.cwd(), "chrome-runtime.json"),
      "utf8",
    ));

    expect(packageJson.scripts.build).toContain("prepare:chrome");
    expect(packageJson.build.extraResources).toContainEqual({
      from: "runtime/chrome/chrome-win64",
      to: "chrome",
    });
    expect(packageJson.build.win.target).toContain("nsis");
    expect(packageJson.build.nsis).toMatchObject({
      oneClick: false,
      allowToChangeInstallationDirectory: true,
      createDesktopShortcut: true,
    });
    expect(runtime).toMatchObject({
      version: "151.0.7922.47",
      platform: "win64",
      sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
    });
  });
});
