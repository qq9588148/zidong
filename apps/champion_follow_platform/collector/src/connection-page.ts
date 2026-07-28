export function connectionPageUrl(retryCount: number): string {
  if (!Number.isSafeInteger(retryCount) || retryCount < 0) {
    throw new Error("collector_retry_count_invalid");
  }
  const retryText = retryCount === 0
    ? "正在建立安全连接"
    : `第 ${retryCount} 次自动重试`;
  const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>NG 主采集 · 正在连接</title>
  <style>
    :root { color-scheme: dark; font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 28px;
      color: #edf6ff;
      background:
        radial-gradient(circle at 50% 16%, rgba(25, 144, 255, .2), transparent 42%),
        linear-gradient(160deg, #07101e, #0a1424 55%, #060b13);
    }
    main { width: min(100%, 360px); text-align: center; }
    .mark {
      width: 74px;
      height: 74px;
      margin: 0 auto 28px;
      display: grid;
      place-items: center;
      border: 1px solid rgba(92, 191, 255, .35);
      border-radius: 24px;
      background: rgba(11, 105, 194, .18);
      box-shadow: 0 18px 60px rgba(0, 99, 190, .22);
    }
    .spinner {
      width: 32px;
      height: 32px;
      border: 3px solid rgba(158, 219, 255, .18);
      border-top-color: #64c7ff;
      border-radius: 50%;
      animation: spin 1s linear infinite;
    }
    h1 { margin: 0; font-size: 25px; font-weight: 650; letter-spacing: .02em; }
    .retry { margin: 13px 0 0; color: #83d1ff; font-size: 14px; }
    .detail { margin: 24px 0 0; color: #a9b8c8; font-size: 14px; line-height: 1.8; }
    .safe {
      margin-top: 30px;
      padding-top: 20px;
      border-top: 1px solid rgba(181, 215, 239, .12);
      color: #78899b;
      font-size: 12px;
      line-height: 1.7;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    @media (prefers-reduced-motion: reduce) { .spinner { animation: none; } }
  </style>
</head>
<body>
  <main>
    <div class="mark" aria-hidden="true"><div class="spinner"></div></div>
    <h1>正在连接 NG</h1>
    <p class="retry">${retryText}</p>
    <p class="detail">线路暂时没有响应，程序会持续自动重试。<br>连接恢复后会自动进入页面，无需反复重开。</p>
    <p class="safe">登录状态和已采集数据都会保留<br>自动执行保持关闭</p>
  </main>
</body>
</html>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}
