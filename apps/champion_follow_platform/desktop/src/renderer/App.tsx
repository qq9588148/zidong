import { type FormEvent, useEffect, useState } from "react";

type RuntimeState = {
  generation: string;
  autoBet: "OFF" | "ON";
  executionBlock:
    | "STARTUP_SYNC_REQUIRED"
    | "SERVER_GLOBAL_STOP"
    | "SAFETY_SYNC_UNAVAILABLE"
    | null;
  highestTask: null;
  connection: {
    status: "UNREGISTERED" | "CONNECTING" | "ONLINE" | "AUTH_REQUIRED" | "OFFLINE";
    registered: boolean;
    username: string | null;
    deviceLabel: string | null;
    errorCode: string | null;
  };
  signal: {
    status:
      | "WAITING_FOR_AUTH"
      | "AUTH_REQUIRED"
      | "WAITING_FOR_PLATFORM"
      | "CONNECTING"
      | "SYNCED"
      | "OFFLINE";
    periodId: string | null;
    task:
      | {
          action: "BET";
          periodId: string;
          revision: number;
          ball: 1 | 2 | 3 | 4 | 5;
          direction: "BIG" | "SMALL" | "ODD" | "EVEN" | "PRIME" | "COMPOSITE";
          signalVersion: number;
          userLevel: "CANDIDATE" | "FORMAL" | "CORE";
        }
      | {
          action: "CANCEL";
          periodId: string;
          revision: number;
          reason: string;
        }
      | null;
    errorCode: string | null;
  };
};

type PlatformPageProbe = Awaited<
  ReturnType<typeof window.championFollow.getPlatformWindowState>
>["probe"];
type PlatformSessionState = Awaited<
  ReturnType<typeof window.championFollow.getPlatformWindowState>
>["session"];

type AuthFeedback = {
  kind: "success" | "error";
  title: string;
  message: string;
};

const safeState: RuntimeState = {
  generation: "starting",
  autoBet: "OFF",
  executionBlock: "STARTUP_SYNC_REQUIRED",
  highestTask: null,
  connection: {
    status: "UNREGISTERED",
    registered: false,
    username: null,
    deviceLabel: null,
    errorCode: null,
  },
  signal: {
    status: "WAITING_FOR_AUTH",
    periodId: null,
    task: null,
    errorCode: null,
  },
};

const errorLabels: Record<string, string> = {
  INVALID_INPUT: "请检查授权码、账号和密码格式。",
  SERVER_UNAVAILABLE: "暂时无法连接服务器，请稍后重试。",
  REGISTRATION_REJECTED: "授权码无效、已使用或注册信息不可用。",
  LOGIN_REJECTED: "账号、密码或本机设备身份验证失败。",
  LOCAL_IDENTITY_UNAVAILABLE: "本机安全身份不可用，请联系管理员重新绑定。",
};

const executionBlockLabels = {
  STARTUP_SYNC_REQUIRED: "等待页面、信号和金额链完成安全同步",
  SERVER_GLOBAL_STOP: "服务器全局停止已开启",
  SAFETY_SYNC_UNAVAILABLE: "服务器安全状态暂时无法确认",
} as const;

export function App() {
  const [state, setState] = useState<RuntimeState>(safeState);
  const [platformOpen, setPlatformOpen] = useState(false);
  const [platformProbe, setPlatformProbe] = useState<PlatformPageProbe>(null);
  const [platformSession, setPlatformSession] = useState<PlatformSessionState>({
    encryptionAvailable: null,
    snapshotLoaded: false,
    snapshotPresent: false,
    pageOriginAllowed: null,
    captureStatus: "IDLE",
    restoreStatus: "IDLE",
    errorCode: null,
  });
  const [openingPlatform, setOpeningPlatform] = useState(false);
  const [platformAddress, setPlatformAddress] = useState("ng888.com");
  const [platformAddressMessage, setPlatformAddressMessage] = useState("");
  const [authorizationCode, setAuthorizationCode] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [authBusy, setAuthBusy] = useState(false);
  const [authMessage, setAuthMessage] = useState("");
  const [authFeedback, setAuthFeedback] = useState<AuthFeedback | null>(null);

  useEffect(() => {
    let active = true;
    const refreshClient = () => {
      void window.championFollow.getState().then((value) => {
        if (active) {
          setState(value);
          setUsername((current) => current || value.connection.username || "");
        }
      });
    };
    refreshClient();
    const refreshPlatform = () => {
      void window.championFollow.getPlatformWindowState().then((value) => {
        if (active) {
          setPlatformOpen(value.open);
          setPlatformProbe(value.probe);
          setPlatformSession(value.session);
        }
      });
    };
    refreshPlatform();
    const platformTimer = window.setInterval(refreshPlatform, 1_000);
    const clientTimer = window.setInterval(refreshClient, 2_000);
    return () => {
      active = false;
      window.clearInterval(platformTimer);
      window.clearInterval(clientTimer);
    };
  }, []);

  const refreshState = async () => {
    setState(await window.championFollow.getState());
  };

  const submitRegistration = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (password !== confirmPassword) {
      const message = "两次输入的密码不一致。";
      setAuthMessage(message);
      setAuthFeedback({ kind: "error", title: "注册失败", message });
      return;
    }
    setAuthBusy(true);
    setAuthMessage("");
    try {
      const result = await window.championFollow.register({
        authorizationCode,
        username,
        password,
      });
      setPassword("");
      setConfirmPassword("");
      if (result.ok) {
        setAuthorizationCode("");
        const message = "账号注册和本机绑定已完成，客户端已经通过服务器认证。";
        setAuthMessage(message);
        setAuthFeedback({ kind: "success", title: "注册成功", message });
      } else {
        const message = errorLabels[result.code] ?? "注册失败，请稍后重试。";
        setAuthMessage(message);
        setAuthFeedback({ kind: "error", title: "注册失败", message });
      }
      await refreshState();
    } catch {
      const message = "注册请求未完成，请检查网络连接后重试。";
      setAuthMessage(message);
      setAuthFeedback({ kind: "error", title: "注册失败", message });
    } finally {
      setAuthBusy(false);
    }
  };

  const submitLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setAuthBusy(true);
    setAuthMessage("");
    try {
      const result = await window.championFollow.login({ username, password });
      setPassword("");
      const message = result.ok
        ? "服务器登录成功。"
        : (errorLabels[result.code] ?? "登录失败，请稍后重试。");
      setAuthMessage(message);
      setAuthFeedback({
        kind: result.ok ? "success" : "error",
        title: result.ok ? "登录成功" : "登录失败",
        message,
      });
      await refreshState();
    } catch {
      const message = "登录请求未完成，请检查网络连接后重试。";
      setAuthMessage(message);
      setAuthFeedback({ kind: "error", title: "登录失败", message });
    } finally {
      setAuthBusy(false);
    }
  };

  const openPlatformLogin = async () => {
    setOpeningPlatform(true);
    try {
      const result = await window.championFollow.openPlatformLogin();
      setPlatformOpen(result.open);
    } finally {
      setOpeningPlatform(false);
    }
  };

  const openPlatformAddress = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setOpeningPlatform(true);
    setPlatformAddressMessage("");
    try {
      const open = window.championFollow.openPlatformAddress;
      if (!open) throw new Error("NAVIGATION_UNAVAILABLE");
      const result = await open(platformAddress);
      if (result.ok) {
        setPlatformOpen(result.open);
        setPlatformAddressMessage("平台网址已在专用 Chrome 中打开，请登录并手动进入游戏。");
      } else {
        setPlatformAddressMessage(result.code === "INVALID_ADDRESS"
          ? "请输入有效的 HTTPS 平台网址。"
          : "网址暂时无法打开，请检查地址或网络后重试。");
      }
    } catch {
      setPlatformAddressMessage("网址暂时无法打开，请检查地址或网络后重试。");
    } finally {
      setOpeningPlatform(false);
    }
  };

  const toggleAutoBet = async () => {
    setState(await window.championFollow.setAutoBet(state.autoBet !== "ON"));
  };

  const quitApp = async () => {
    if (!window.confirm("完全退出后，平台可能要求重新登录。确定退出吗？")) return;
    await window.championFollow.quitApp();
  };

  const detectedContractParts = platformProbe === null ? 0 : [
    platformProbe.currentPeriodId !== null,
    platformProbe.countdownMs !== null,
    Object.values(platformProbe.directionTextCounts).every((count) => count > 0),
    platformProbe.balanceLabelVisible && platformProbe.balanceValueReadable,
    platformProbe.stakeInputCount > 0 && platformProbe.betControlCount > 0,
  ].filter(Boolean).length;
  const platformCountdown = platformProbe?.countdownMs === null ||
    platformProbe?.countdownMs === undefined
    ? "未识别"
    : formatCountdown(platformProbe.countdownMs);
  const signalCopy = describeSignal(state.signal);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-mark" aria-hidden="true">CF</div>
        <div>
          <p className="eyebrow">CHAMPION FOLLOW</p>
          <h1>冠军跟随客户端</h1>
        </div>
        <div className="mode-pill"><span />服务器接入测试</div>
      </header>

      <section className="hero-card">
        <div>
          <p className="section-label">启动状态</p>
          <h2>{state.connection.status === "ONLINE" ? "客户端已连接服务器" : "客户端已安全启动"}</h2>
          <p className="hero-copy">
            {state.connection.status === "ONLINE"
              ? "账号与本机设备已经绑定，重启后会通过 Windows 凭据库自动恢复会话。"
              : "先完成客户端账号注册和本机绑定。NG 平台登录仍由你本人完成，自动执行保持关闭。"}
          </p>
        </div>
        <div className="shield" aria-label="安全锁定">✓</div>
      </section>

      <section className="status-grid" aria-label="客户端状态">
        <article className="status-card">
          <p>服务器</p>
          <strong className={state.connection.status === "ONLINE" ? "active" : "muted"}>
            {state.connection.status === "ONLINE" ? "已认证" :
              state.connection.status === "CONNECTING" ? "连接中" :
              state.connection.status === "AUTH_REQUIRED" ? "需要登录" :
              state.connection.status === "OFFLINE" ? "暂时离线" : "未注册"}
          </strong>
          <span>{state.connection.deviceLabel
            ? `设备 …${state.connection.deviceLabel}`
            : "等待一次性授权码"}</span>
        </article>
        <article className="status-card">
          <p>平台页面</p>
          <strong className={platformOpen ? "active" : "muted"}>
            {platformOpen ? "已打开" : "未打开"}
          </strong>
          <span>独立 Chrome 会话 · 登录资料与日常浏览器隔离</span>
        </article>
        <article className="status-card safety">
          <p>自动执行</p><strong>{state.autoBet === "ON" ? "已开启" : "关闭"}</strong>
          <span>每次启动固定恢复为关闭</span>
        </article>
      </section>

      {state.connection.status !== "ONLINE" && (
        <section className="account-card">
          <div className="account-copy">
            <p className="section-label">客户端账号</p>
            <h3>{state.connection.registered ? "重新登录服务器" : "注册并绑定这台电脑"}</h3>
            <p>{state.connection.registered
              ? "设备身份仍保存在本机，只需输入客户端账号和密码恢复会话。"
              : "使用后台生成的一次性授权码。一个客户端账号只绑定一台电脑。"}</p>
          </div>
          <form
            className="account-form"
            onSubmit={state.connection.registered ? submitLogin : submitRegistration}
          >
            {!state.connection.registered && (
              <label>
                一次性授权码
                <input
                  value={authorizationCode}
                  onChange={(event) => setAuthorizationCode(event.target.value)}
                  autoComplete="off"
                  required
                  minLength={40}
                  maxLength={100}
                />
              </label>
            )}
            <label>
              客户端账号
              <input
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="username"
                required
                minLength={3}
                maxLength={80}
              />
            </label>
            <label>
              密码
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete={state.connection.registered ? "current-password" : "new-password"}
                required
                minLength={12}
                maxLength={128}
              />
            </label>
            {!state.connection.registered && (
              <label>
                确认密码
                <input
                  type="password"
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  autoComplete="new-password"
                  required
                  minLength={12}
                  maxLength={128}
                />
              </label>
            )}
            <button className="account-action" type="submit" disabled={authBusy}>
              {authBusy ? "正在处理…" : state.connection.registered ? "登录服务器" : "注册并绑定本机"}
            </button>
            <p className="account-message" role="status" aria-live="polite">
              {authMessage || (state.connection.errorCode
                ? errorLabels[state.connection.errorCode]
                : "授权码和密码不会写入日志。")}
            </p>
          </form>
        </section>
      )}

      <section className="platform-card">
        <div>
          <p className="section-label">NG 平台</p>
          <h3>手动登录平台</h3>
          <p>客户自行输入网址、登录并进入“比特分分彩”；程序识别页面后自动开始工作。</p>
          <p>
            页面合同：{platformProbe?.contractReady
              ? "已完整识别（只读）"
              : `已识别 ${detectedContractParts}/5 项（不会下注）`}
          </p>
          <p>
            当前期号：{platformProbe?.currentPeriodId ?? "未识别"}
            {" · "}倒计时：{platformCountdown}
          </p>
          <p>
            赔率：固定 1.96
            {" · "}余额：{platformProbe?.balanceValueReadable ? "已识别" : "未识别"}
            {" · "}公开下注：{platformProbe?.publicBetCommandCount ?? 0} 条
          </p>
          <p>
            登录态保存：{platformSession.snapshotPresent
              ? "已加密保存"
              : platformSession.errorCode
                ? `未保存（${platformSession.errorCode}）`
                : "正在检测"}
          </p>
        </div>
        <div className="platform-actions">
          <form className="platform-address-form" onSubmit={openPlatformAddress}>
            <label htmlFor="platform-address">平台网址</label>
            <div>
              <input
                id="platform-address"
                value={platformAddress}
                onChange={(event) => setPlatformAddress(event.target.value)}
                placeholder="ng888.com"
                autoComplete="off"
                spellCheck={false}
                required
              />
              <button className="platform-action" type="submit" disabled={openingPlatform}>
                {openingPlatform ? "正在打开…" : "打开网址"}
              </button>
            </div>
            <p role="status" aria-live="polite">{platformAddressMessage}</p>
          </form>
          <button
            className="platform-action secondary"
            type="button"
            onClick={() => void openPlatformLogin()}
            disabled={openingPlatform}
          >
            {platformOpen ? "显示专用 Chrome" : "打开默认网址"}
          </button>
        </div>
      </section>

      <section className="control-card">
        <div>
          <p className="section-label">服务器信号 · 自动执行</p>
          <h3>{signalCopy.title}</h3>
          <p>{signalCopy.detail}</p>
          <p role="status">
            执行保护：{state.executionBlock === null
              ? "允许用户明确开启"
              : executionBlockLabels[state.executionBlock]}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void toggleAutoBet()}
          disabled={state.autoBet !== "ON" && (
            state.executionBlock !== null ||
            state.connection.status !== "ONLINE" ||
            platformProbe?.contractReady !== true ||
            state.signal.status !== "SYNCED"
          )}
        >
          {state.autoBet === "ON" ? "关闭自动执行" : "开启自动执行"}
        </button>
      </section>

      <footer>
        <span>启动代次</span>
        <code>{state.generation === "starting" ? "正在初始化" : state.generation.slice(0, 8)}</code>
        <span className="footer-note">EXECUTION CORE READY · FAIL-CLOSED</span>
        <button className="quit-action" type="button" onClick={() => void quitApp()}>
          完全退出
        </button>
      </footer>

      {authFeedback && (
        <div className="result-backdrop">
          <section
            className={`result-dialog ${authFeedback.kind}`}
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="auth-result-title"
            aria-describedby="auth-result-message"
          >
            <div className="result-icon" aria-hidden="true">
              {authFeedback.kind === "success" ? "✓" : "!"}
            </div>
            <h3 id="auth-result-title">{authFeedback.title}</h3>
            <p id="auth-result-message">{authFeedback.message}</p>
            <button type="button" onClick={() => setAuthFeedback(null)}>知道了</button>
          </section>
        </div>
      )}
    </main>
  );
}

function formatCountdown(milliseconds: number): string {
  const seconds = Math.floor(milliseconds / 1_000);
  const hours = Math.floor(seconds / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  const remainder = seconds % 60;
  return hours > 0
    ? [hours, minutes, remainder].map((value) => String(value).padStart(2, "0")).join(":")
    : [minutes, remainder].map((value) => String(value).padStart(2, "0")).join(":");
}

const directionLabels = {
  BIG: "大",
  SMALL: "小",
  ODD: "单",
  EVEN: "双",
  PRIME: "质",
  COMPOSITE: "合",
} as const;

function describeSignal(signal: RuntimeState["signal"]): {
  title: string;
  detail: string;
} {
  if (signal.status === "WAITING_FOR_AUTH" || signal.status === "AUTH_REQUIRED") {
    return {
      title: "等待服务器认证",
      detail: "客户端通过服务器认证后才会接收签名信号。",
    };
  }
  if (signal.status === "WAITING_FOR_PLATFORM") {
    return {
      title: "等待进入比特分分彩",
      detail: "识别到当前期号后才会同步该期服务器信号。",
    };
  }
  if (signal.status === "CONNECTING") {
    return {
      title: "正在同步服务器信号",
      detail: `当前期号 ${signal.periodId ?? "未识别"}，等待权威任务版本。`,
    };
  }
  if (signal.status === "OFFLINE") {
    return {
      title: "信号通道暂时不可用",
      detail: "客户端会自动重连；离线期间不会生成或执行本地方向。",
    };
  }
  if (signal.task === null) {
    return {
      title: "当前期暂无合格信号",
      detail: `期号 ${signal.periodId ?? "未识别"} 已与服务器同步。`,
    };
  }
  if (signal.task.action === "CANCEL") {
    return {
      title: "本期信号已取消",
      detail: `期号 ${signal.task.periodId} · 版本 ${signal.task.revision} · ${cancelReasonLabel(signal.task.reason)}`,
    };
  }
  return {
    title: `第 ${signal.task.ball} 球 · ${directionLabels[signal.task.direction]}`,
    detail: `期号 ${signal.task.periodId} · 任务版本 ${signal.task.revision} · 信号版本 ${signal.task.signalVersion} · 仅展示`,
  };
}

function cancelReasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    champion_withdrew: "冠军已撤回",
    profile_downgraded: "画像已降级",
    threshold_changed: "门槛已变化",
    collector_stale: "采集心跳过期",
    data_gap: "数据存在缺口",
    device_reassigned: "设备已重新分配",
    account_disabled: "账号已停用",
    device_unbound: "设备已解绑",
    global_stop: "服务器全局停止",
  };
  return labels[reason] ?? "服务器已取消";
}
