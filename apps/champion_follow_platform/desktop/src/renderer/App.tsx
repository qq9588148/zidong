import { useEffect, useState } from "react";

type RuntimeState = {
  generation: string;
  autoBet: "OFF" | "ON";
  executionBlock: "STARTUP_SYNC_REQUIRED" | null;
  highestTask: null;
};

const safeState: RuntimeState = {
  generation: "starting",
  autoBet: "OFF",
  executionBlock: "STARTUP_SYNC_REQUIRED",
  highestTask: null,
};

export function App() {
  const [state, setState] = useState<RuntimeState>(safeState);
  const [platformOpen, setPlatformOpen] = useState(false);
  const [openingPlatform, setOpeningPlatform] = useState(false);

  useEffect(() => {
    let active = true;
    void window.championFollow.getState().then((value) => {
      if (active) setState(value);
    });
    const refreshPlatform = () => {
      void window.championFollow.getPlatformWindowState().then((value) => {
        if (active) setPlatformOpen(value.open);
      });
    };
    refreshPlatform();
    const timer = window.setInterval(refreshPlatform, 1_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const openPlatformLogin = async () => {
    setOpeningPlatform(true);
    try {
      const result = await window.championFollow.openPlatformLogin();
      setPlatformOpen(result.open);
    } finally {
      setOpeningPlatform(false);
    }
  };

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-mark" aria-hidden="true">CF</div>
        <div>
          <p className="eyebrow">CHAMPION FOLLOW</p>
          <h1>冠军跟随客户端</h1>
        </div>
        <div className="mode-pill"><span />本地测试模式</div>
      </header>

      <section className="hero-card">
        <div>
          <p className="section-label">启动状态</p>
          <h2>客户端已安全启动</h2>
          <p className="hero-copy">
            自动执行核心已就绪。服务器仍未接入；NG 平台只在你点击后打开，登录由你本人完成。
          </p>
        </div>
        <div className="shield" aria-label="安全锁定">✓</div>
      </section>

      <section className="status-grid" aria-label="客户端状态">
        <article className="status-card">
          <p>服务器</p><strong className="muted">未连接</strong>
          <span>按测试计划最后接入</span>
        </article>
        <article className="status-card">
          <p>平台页面</p>
          <strong className={platformOpen ? "active" : "muted"}>
            {platformOpen ? "已打开" : "未打开"}
          </strong>
          <span>独立内置 Chromium 会话</span>
        </article>
        <article className="status-card safety">
          <p>自动执行</p><strong>关闭</strong>
          <span>每次启动固定恢复为关闭</span>
        </article>
      </section>

      <section className="platform-card">
        <div>
          <p className="section-label">NG 平台</p>
          <h3>手动登录平台</h3>
          <p>当前入口 ng1z.com；后期可由已认证后台安全更新。账号、密码和验证码不会进入客户端日志。</p>
        </div>
        <button
          className="platform-action"
          type="button"
          onClick={() => void openPlatformLogin()}
          disabled={openingPlatform}
        >
          {openingPlatform ? "正在打开…" : platformOpen ? "显示 NG 窗口" : "打开 NG 平台登录"}
        </button>
      </section>

      <section className="control-card">
        <div>
          <p className="section-label">执行控制</p>
          <h3>等待服务器与冠军信号</h3>
          <p>平台登录不会自动开启执行；完整同步前开关保持锁定。</p>
        </div>
        <button type="button" disabled aria-disabled="true">
          自动执行已关闭
        </button>
      </section>

      <footer>
        <span>启动代次</span>
        <code>{state.generation === "starting" ? "正在初始化" : state.generation.slice(0, 8)}</code>
        <span className="footer-note">EXECUTION CORE READY · FAIL-CLOSED</span>
      </footer>
    </main>
  );
}
