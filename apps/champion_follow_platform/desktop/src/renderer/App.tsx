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

  useEffect(() => {
    let active = true;
    void window.championFollow.getState().then((value) => {
      if (active) setState(value);
    });
    return () => {
      active = false;
    };
  }, []);

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
            当前版本完全离线，不连接服务器、不打开平台页面，也不会执行任何操作。
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
          <p>平台页面</p><strong className="muted">未打开</strong>
          <span>没有 WebView 或外部浏览器</span>
        </article>
        <article className="status-card safety">
          <p>自动执行</p><strong>关闭</strong>
          <span>每次启动固定恢复为关闭</span>
        </article>
      </section>

      <section className="control-card">
        <div>
          <p className="section-label">执行控制</p>
          <h3>等待后续功能接入</h3>
          <p>本地外壳测试通过前，执行开关保持锁定。</p>
        </div>
        <button type="button" disabled aria-disabled="true">
          自动执行已关闭
        </button>
      </section>

      <footer>
        <span>启动代次</span>
        <code>{state.generation === "starting" ? "正在初始化" : state.generation.slice(0, 8)}</code>
        <span className="footer-note">OFFLINE · FAIL-CLOSED</span>
      </footer>
    </main>
  );
}
