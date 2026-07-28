// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../../src/renderer/App";

const baseState = {
  generation: "12345678-aaaa-4bbb-8ccc-123456789abc",
  autoBet: "OFF" as const,
  executionBlock: "STARTUP_SYNC_REQUIRED" as const,
  highestTask: null,
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("App registration state", () => {
  it("shows one-time-code registration while execution remains locked", async () => {
    window.championFollow = {
      getState: vi.fn(async () => ({
        ...baseState,
        connection: {
          status: "UNREGISTERED" as const,
          registered: false,
          username: null,
          deviceLabel: null,
          errorCode: null,
        },
      })),
      register: vi.fn(),
      login: vi.fn(),
      setAutoBet: vi.fn(),
      getPlatformWindowState: vi.fn(async () => ({ open: false, probe: null })),
      openPlatformLogin: vi.fn(),
      quitApp: vi.fn(),
    };

    render(<App />);

    expect(await screen.findByRole("button", { name: "注册并绑定本机" })).toBeVisible();
    expect(screen.getByLabelText("一次性授权码")).toBeVisible();
    expect(screen.getByRole("button", { name: "自动执行已关闭" })).toBeDisabled();
  });

  it("shows the authenticated device without a registration form", async () => {
    window.championFollow = {
      getState: vi.fn(async () => ({
        ...baseState,
        connection: {
          status: "ONLINE" as const,
          registered: true,
          username: "client-user",
          deviceLabel: "90abcdef",
          errorCode: null,
        },
      })),
      register: vi.fn(),
      login: vi.fn(),
      setAutoBet: vi.fn(),
      getPlatformWindowState: vi.fn(async () => ({ open: false, probe: null })),
      openPlatformLogin: vi.fn(),
      quitApp: vi.fn(),
    };

    render(<App />);

    expect(await screen.findByText("已认证")).toBeVisible();
    expect(screen.queryByRole("button", { name: "注册并绑定本机" })).not.toBeInTheDocument();
    expect(screen.getByText("设备 …90abcdef")).toBeVisible();
  });
});
