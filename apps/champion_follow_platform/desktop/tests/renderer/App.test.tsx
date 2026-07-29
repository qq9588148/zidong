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
  signal: {
    status: "WAITING_FOR_PLATFORM" as const,
    periodId: null,
    task: null,
    errorCode: null,
  },
};

const emptyPlatformState = {
  open: false,
  probe: null,
  session: {
    encryptionAvailable: null,
    snapshotLoaded: false,
    snapshotPresent: false,
    pageOriginAllowed: null,
    captureStatus: "IDLE" as const,
    restoreStatus: "IDLE" as const,
    errorCode: null,
  },
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("App registration state", () => {
  it("lets the customer enter a platform address and opens it without auto-entering a game", async () => {
    const openPlatformAddress = vi.fn(async () => ({
      ok: true as const,
      open: true as const,
    }));
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
      getPlatformWindowState: vi.fn(async () => emptyPlatformState),
      openPlatformLogin: vi.fn(),
      openPlatformAddress,
      quitApp: vi.fn(),
    };

    render(<App />);

    const address = await screen.findByLabelText("平台网址");
    fireEvent.change(address, { target: { value: "ng888.com" } });
    fireEvent.click(screen.getByRole("button", { name: "打开网址" }));

    expect(openPlatformAddress).toHaveBeenCalledWith("ng888.com");
    expect(await screen.findByText(/请登录并手动进入游戏/))
      .toBeVisible();
  });

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
      getPlatformWindowState: vi.fn(async () => emptyPlatformState),
      openPlatformLogin: vi.fn(),
      quitApp: vi.fn(),
    };

    render(<App />);

    expect(await screen.findByRole("button", { name: "注册并绑定本机" })).toBeVisible();
    expect(screen.getByLabelText("一次性授权码")).toBeVisible();
    expect(screen.getByRole("button", {
      name: "开启自动执行",
    })).toBeDisabled();
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
      getPlatformWindowState: vi.fn(async () => emptyPlatformState),
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
      getPlatformWindowState: vi.fn(async () => emptyPlatformState),
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
      getPlatformWindowState: vi.fn(async () => emptyPlatformState),
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

  it("shows read-only NG period, countdown and safe balance recognition", async () => {
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
      getPlatformWindowState: vi.fn(async () => ({
        ...emptyPlatformState,
        open: true,
        probe: {
          gameVisible: true,
          currentPeriodId: "2607290008",
          countdownMs: 65_000,
          periodCandidateCount: 2,
          countdownCandidateCount: 1,
          directionTextCounts: {
            BIG: 1,
            SMALL: 0,
            ODD: 0,
            EVEN: 1,
            PRIME: 0,
            COMPOSITE: 0,
          },
          balanceLabelVisible: true,
          balanceValueReadable: true,
          publicBetCommandCount: 2,
          publicBetSourceAvailable: true,
          publicBetSourceComplete: true,
          stakeInputCount: 0,
          betControlCount: 0,
          contractReady: false,
        },
      })),
      openPlatformLogin: vi.fn(),
      quitApp: vi.fn(),
    };

    render(<App />);

    expect(await screen.findByText(/当前期号：2607290008/)).toBeVisible();
    expect(screen.getByText(/倒计时：01:05/)).toBeVisible();
    expect(screen.getByText(/余额：已识别/)).toBeVisible();
    expect(screen.getByText(/赔率：固定 1\.96/)).toBeVisible();
    expect(screen.getByText(/公开下注：2 条/)).toBeVisible();
    expect(screen.getByText(/不会下注/)).toBeVisible();
  });

  it("shows a sanitized server signal without enabling execution", async () => {
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
        signal: {
          status: "SYNCED" as const,
          periodId: "2607290010",
          task: {
            action: "BET" as const,
            periodId: "2607290010",
            revision: 4,
            ball: 2 as const,
            direction: "ODD" as const,
            signalVersion: 7,
            userLevel: "CORE" as const,
          },
          errorCode: null,
        },
      })),
      register: vi.fn(),
      login: vi.fn(),
      setAutoBet: vi.fn(),
      getPlatformWindowState: vi.fn(async () => emptyPlatformState),
      openPlatformLogin: vi.fn(),
      quitApp: vi.fn(),
    };

    render(<App />);

    expect(await screen.findByText("第 2 球 · 单")).toBeVisible();
    expect(screen.getByText(/任务版本 4/)).toBeVisible();
    expect(screen.getByText(/信号版本 7/)).toBeVisible();
    expect(screen.getByRole("button", {
      name: "开启自动执行",
    })).toBeDisabled();
    expect(document.body.textContent).not.toMatch(/actor_ref|signature|device_id/);
  });

  it("shows the server global stop as an execution protection", async () => {
    window.championFollow = {
      getState: vi.fn(async () => ({
        ...baseState,
        executionBlock: "SERVER_GLOBAL_STOP" as const,
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
      getPlatformWindowState: vi.fn(async () => emptyPlatformState),
      openPlatformLogin: vi.fn(),
      quitApp: vi.fn(),
    };

    render(<App />);

    expect(await screen.findByText(/服务器全局停止已开启/)).toBeVisible();
    expect(screen.getByRole("button", { name: "开启自动执行" })).toBeDisabled();
  });
});
