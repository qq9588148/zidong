# Champion Follow 工作区指令

## 交接触发词

用户发送 **`继续Windows交接`** 时：

1. 先阅读根目录 `README.md`。
2. 阅读 `docs/superpowers/specs/2026-07-27-champion-follow-platform-design.md`。
3. 按顺序检查并实施：
   - `docs/superpowers/plans/2026-07-27-champion-follow-03-auth-admin.md`
   - `docs/superpowers/plans/2026-07-27-champion-follow-04-windows-client.md`
   - `docs/superpowers/plans/2026-07-27-champion-follow-05-integration-pilot.md`
4. 先核对仓库现状与测试，不重复已经完成的任务；从第一个尚未实现且依赖已满足的任务继续。

## 实施约束

- 只维护 `apps/champion_follow_platform`；不得恢复已删除的旧 Bit28、Mac、Multilogin 或外部浏览器工程。
- 优先完成最小可运行框架；不额外扩展需求，不重构无关代码。
- 先写可复现测试，再做最小实现，并运行相关测试、类型检查和构建。
- Electron 客户端使用内置 Chromium；自动执行默认关闭，真实操作必须由用户明确开启。
- 不提交、打印或记录真实密码、令牌、Cookie、私钥、账号标识、会话数据或本地数据库。
- `.env`、日志、数据库、依赖目录和构建产物不得进入 Git。
