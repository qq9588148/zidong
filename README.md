# Champion Follow Platform

当前仓库只保留新的 **Champion Follow** 框架，供后续在 Windows 上继续实现和验证。
旧 Bit28/Mac/Multilogin/外部浏览器工程、旧庄家方向策略、历史实验输出和对应计划均已移除；新代码不复用这些实现。

## 当前范围

- `apps/champion_follow_platform/`：FastAPI + PostgreSQL 核心服务。
- `apps/champion_follow_platform/collector/`：Electron 内置 Chromium 采集器框架。
- `docs/superpowers/specs/2026-07-27-champion-follow-platform-design.md`：冻结设计。
- `docs/superpowers/plans/2026-07-27-champion-follow-*.md`：分阶段实施计划。

当前不是成品：Windows 客户端、授权后台和端到端实机验证仍应按计划继续实现。自动执行默认必须保持关闭，任何真实操作必须由用户明确开启。

当前交接点：Plan 01（核心服务）和 Plan 02（采集器）已有代码与测试；Plan 03（授权/管理后台）、Plan 04（Windows 客户端）和 Plan 05（集成试点）尚待实现。

## 本地验证

### Python 核心服务

要求 Python 3.12 和 PostgreSQL 16。

```powershell
cd apps/champion_follow_platform
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
# 把 .env 中的占位符替换为本机测试密码；不要提交 .env
docker compose --env-file .env up -d --wait postgres
# 必须显式指向专用的 *_test 数据库，密码与 .env 中的本机测试密码一致
$env:TEST_DATABASE_URL = "postgresql://champion_follow:YOUR_LOCAL_TEST_PASSWORD@127.0.0.1:55432/champion_follow_test"
pytest -q
```

macOS/Linux 使用 `source .venv/bin/activate`、`cp .env.example .env` 和
`export TEST_DATABASE_URL='postgresql://…/champion_follow_test'`，其余步骤相同。

验证结束后可停止本地测试数据库：

```powershell
docker compose --env-file .env down
```

### Electron 采集器

要求 Node.js/npm；依赖版本由 `package-lock.json` 锁定。

```bash
cd apps/champion_follow_platform/collector
npm ci
npm test
npm run typecheck
npm run build
```

## Windows Codex 交接

交接关键字：`继续Windows交接`。根目录 `AGENTS.md` 定义了收到该关键字后的执行顺序。

在 Windows 克隆本仓库后，让 Codex 先阅读：

1. `AGENTS.md`（若工作区提供）
2. 本 README
3. `docs/superpowers/specs/2026-07-27-champion-follow-platform-design.md`
4. `docs/superpowers/plans/2026-07-27-champion-follow-03-auth-admin.md`
5. `docs/superpowers/plans/2026-07-27-champion-follow-04-windows-client.md`
6. `docs/superpowers/plans/2026-07-27-champion-follow-05-integration-pilot.md`

然后从尚未完成的 Windows 客户端任务继续；先建立最小可运行框架和测试，不恢复已删除的旧工程，不写入或打印真实密码、令牌、Cookie、账号标识或会话数据。

## 敏感文件

仓库只允许提交占位配置（例如 `.env.example`）。`.env`、数据库、日志、凭据交接文件、构建产物和依赖目录均由 `.gitignore` 排除。
