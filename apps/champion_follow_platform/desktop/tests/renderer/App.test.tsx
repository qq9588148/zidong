// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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

  it("shows a prominent dialog when registration is rejected", async () => {
    window.championFollow = {
      getState: vi.fn(async () => ({
        ...baseState,
        connection: {
          status: "UNREGISTERED" as const,
          registered: false,
          username: null,
          deviceLabel: null,
          errorCode: "REGISTRATION_REJECTED",
        },
      })),
      register: vi.fn(async () => ({
        ok: false as const,
        code: "REGISTRATION_REJECTED" as const,
      })),
      login: vi.fn(),
      setAutoBet: vi.fn(),
      getPlatformWindowState: vi.fn(async () => ({ open: false, probe: null })),
      openPlatformLogin: vi.fn(),
      quitApp: vi.fn(),
    };

    render(<App />);

    fireEvent.change(await screen.findByLabelText("一次性授权码"), {
      target: { value: "a".repeat(48) },
    });
    fireEvent.change(screen.getByLabelText("客户端账号"), {
      target: { value: "client-user" },
    });
    fireEvent.change(screen.getByLabelText("密码"), {
      target: { value: "example-password" },
    });
    fireEvent.change(screen.getByLabelText("确认密码"), {
      target: { value: "example-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "注册并绑定本机" }));

    const dialog = await screen.findByRole("alertdialog", { name: "注册失败" });
    expect(dialog).toBeVisible();
    expect(dialog).toHaveTextContent("授权码无效、已使用或注册信息不可用。");
    expect(screen.getByRole("button", { name: "知道了" })).toBeVisible();
  });

  it("shows a success dialog and authenticated state after registration", async () => {
    const unregistered = {
      ...baseState,
      connection: {
        status: "UNREGISTERED" as const,
        registered: false,
        username: null,
        deviceLabel: null,
        errorCode: null,
      },
    };
    const authenticated = {
      ...baseState,
      connection: {
        status: "ONLINE" as const,
        registered: true,
        username: "client-user",
        deviceLabel: "90abcdef",
        errorCode: null,
      },
    };
    window.championFollow = {
      getState: vi.fn()
        .mockResolvedValueOnce(unregistered)
        .mockResolvedValue(authenticated),
      register: vi.fn(async () => ({ ok: true as const })),
      login: vi.fn(),
      setAutoBet: vi.fn(),
      getPlatformWindowState: vi.fn(async () => ({ open: false, probe: null })),
      openPlatformLogin: vi.fn(),
      quitApp: vi.fn(),
    };

    render(<App />);

    fireEvent.change(await screen.findByLabelText("一次性授权码"), {
      target: { value: "a".repeat(48) },
    });
    fireEvent.change(screen.getByLabelText("客户端账号"), {
      target: { value: "client-user" },
    });
    fireEvent.change(screen.getByLabelText("密码"), {
      target: { value: "example-password" },
    });
    fireEvent.change(screen.getByLabelText("确认密码"), {
      target: { value: "example-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "注册并绑定本机" }));

    const dialog = await screen.findByRole("alertdialog", { name: "注册成功" });
    expect(dialog).toHaveTextContent("账号注册和本机绑定已完成");
    expect(await screen.findByText("已认证")).toBeVisible();
  });
});
