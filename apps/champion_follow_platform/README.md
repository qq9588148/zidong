# Champion Follow 核心服务

本目录包含 FastAPI + PostgreSQL 核心服务与主采集器。Windows 交接只维护当前 Champion Follow 路线，不恢复旧 Mac、Multilogin 或外部浏览器工程。

## Windows 本地验证

需要 Python 3.12 和 PostgreSQL 16。所有数据库测试必须显式指向专用的 `*_test` 数据库；不要把本机密码写入 Git 或命令输出。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
$env:TEST_DATABASE_URL = "postgresql://champion_follow:YOUR_LOCAL_TEST_PASSWORD@127.0.0.1:55432/champion_follow_test"
pytest -q
```

## 数据库与因果处理

服务命令从 `DATABASE_URL` 读取连接信息，并且不会打印解析后的 URL：

```powershell
champion-follow migrate
champion-follow init-namespace --version actor-hmac-v1
champion-follow process-ready --namespace-version actor-hmac-v1
```

`process-ready` 先重建仍为 pending 的期完整性投影，再按期号依次冻结开奖前榜单和候选、结算盲跟结果并推进画像。重复运行是幂等的；未归属撤单、缺口或不完整期只记录排除状态。

历史导入和采集器注册使用各自的显式参数。采集 Bearer 只写入调用方指定的一次性交接文件；导入操作不会把源行打印到终端。

## 隐私边界

- 原始第三方标识在页面隔离层转换为 `actor_key`；API 只展示 `A000001` 形式的匿名编号。
- 平台密码、Cookie、Token、原始 UID、完整请求和命名空间密钥不得进入服务器、日志、测试夹具或 Git。
- PostgreSQL 只保存采集凭据摘要；真实 `.env`、数据库、日志、依赖目录和构建产物均不得提交。
- 自动执行默认关闭；任何真实平台操作必须由用户明确开启。
