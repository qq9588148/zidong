# 胜率冠军跟单平台核心服务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在新目录 `apps/champion_follow_platform` 中建立 FastAPI + PostgreSQL 核心服务，幂等接收脱敏分分彩事件，安全导入旧日志，仅从完整期次增量生成 Wilson 画像、逐期 `as-of` 盲跟快照、冠军榜和 7/30 天门槛预览。

**Architecture:** 页面端只上传标准化的 HMAC 匿名事件；FastAPI 以序号流和事件键双重去重后事务落库。纯领域层先应用可归属撤单并证明期完整，因果处理器再以“冻结开奖前画像/候选 → 揭示本期结果 → 结算盲跟 → 更新画像”的固定顺序逐期执行。门槛预览只过滤已冻结的 `as-of` 候选，绝不使用当前榜回套历史。

**Tech Stack:** Python 3.12, FastAPI 0.116.1, Pydantic 2.11.7, psycopg 3.2.9 + psycopg-pool 3.2.6, PostgreSQL 16, Uvicorn 0.35.0, pytest 8.4.1, pytest-asyncio 1.1.0, HTTPX 0.28.1, Docker Compose.

---

## 范围、固定决定与成功口径

本计划是已批准总规格的第 01 个可独立验收子项目。它实现服务端的事件、完整性、画像、盲跟、榜单和门槛预览核心；主采集端本机队列、TLS WebSocket、App 账号/管理员认证、设备绑定、多设备分配、Windows 下单、金额链、订单结算和管理页面分别由后续计划接入，不在本计划中伪造空实现。

本计划锁定以下口径：

- 首版仅接受 `P1`–`P5` 的大/小、单/双、质/合，固定赔率版本 `ffc-double-1.96-v1`；`1` 按质数规则归为“质”，与已有 FFC 结算合同一致。
- 公开下注金额用整数分 `amount_fen` 保存，仅用于追加/撤单净额；排名和盲跟一律按固定 1 元。
- 固定 1 元盲跟用整数微元：命中 `+960_000`，未命中 `-1_000_000`；不用二进制浮点结算。
- 业务时间全部以带时区的 UTC 入库；7/30 天预览的日界限以 `Asia/Shanghai` 自然日计算。
- 服务器仅保存命名空间版本和 HMAC 摘要，不保存命名空间密钥、原始 UID、昵称、Cookie、Token、密码或完整私有请求。
- 主采集端用独立高熵 Bearer 凭据认证；数据库只保存其 SHA-256 摘要。`register-collector` 不向标准输出或日志返回明文，只把凭据一次写入调用方指定的权限 `0600` 独占交接文件；主采集端导入 OS 保护存储后立即删除该文件。
- `id_quality=stable` 只能证明消息键稳定，不被当成“同一自然人”证明。API 只返回服务器生成的 `A000001` 形式匿名编号，不返回内部 `actor_key` HMAC。
- 当前命名空间只有经审计确认相同版本的旧数据可进入画像；无法确认的数据以 `baseline` 分区导入，永不与当前画像合并。
- 无法归属的撤单、未恢复缺口、结果/封盘缺失、过度撤单或撤单后仍净对压，均使整期不进入任何画像和候选。
- 用户等级是全局画像属性，设备门槛只过滤已冻结候选，不修改等级。
- 服务端在本子项目中不生成真实设备任务；只有后续计划将“显式保存且已预览的门槛版本”接入设备分配后，才可产生 `BET/CANCEL` 任务。

## 准确文件树

| Path | Responsibility |
|---|---|
| `apps/champion_follow_platform/pyproject.toml` | 锁定 Python/运行/测试依赖、包数据和 CLI 入口。 |
| `apps/champion_follow_platform/compose.yaml` | 仅供本地开发与集成测试的 PostgreSQL 16。 |
| `apps/champion_follow_platform/.env.example` | 只含明显假值的环境变量模板。 |
| `apps/champion_follow_platform/README.md` | 本地启动、迁移、导入、因果处理和隐私边界。 |
| `apps/champion_follow_platform/src/champion_follow/__init__.py` | 包版本。 |
| `apps/champion_follow_platform/src/champion_follow/config.py` | 严格环境配置；不向日志序列化数据库 URL。 |
| `apps/champion_follow_platform/src/champion_follow/db.py` | psycopg 异步连接池生命周期。 |
| `apps/champion_follow_platform/src/champion_follow/migrations.py` | 事务化、带 SHA-256 校验的 SQL 迁移执行器。 |
| `apps/champion_follow_platform/src/champion_follow/sql/0001_core.sql` | 一次性建立最终核心模型：命名空间、带摘要凭据的采集流、ACK/心跳、期次、事件、缺口、匿名用户、样本、画像、快照、候选和门槛表；应用后永不改写。 |
| `apps/champion_follow_platform/src/champion_follow/contracts/events.py` | 严格脱敏事件批次、ACK 和事件跨层 DTO。 |
| `apps/champion_follow_platform/src/champion_follow/contracts/rankings.py` | 冠军榜查询/响应 DTO。 |
| `apps/champion_follow_platform/src/champion_follow/contracts/thresholds.py` | 门槛、生效下限、7/30 天预览和版本响应 DTO。 |
| `apps/champion_follow_platform/src/champion_follow/domain/markets.py` | 30 个单项、15 个市场、相反方向与 FFC 数字分类。 |
| `apps/champion_follow_platform/src/champion_follow/domain/integrity.py` | 撤单后净额、整期完整性和一期一样本合成。 |
| `apps/champion_follow_platform/src/champion_follow/domain/statistics.py` | 一侧 95% Wilson 下界、固定单位收益和盈亏平衡线。 |
| `apps/champion_follow_platform/src/champion_follow/domain/profiles.py` | 全量/近 200 画像、盲跟收益/回撤和 30/200/500 分级。 |
| `apps/champion_follow_platform/src/champion_follow/repositories/ingestion.py` | 采集流锁、连续 ACK、事件去重与冲突检查。 |
| `apps/champion_follow_platform/src/champion_follow/repositories/issues.py` | 期次事件/缺口读取、完整性状态和样本持久化。 |
| `apps/champion_follow_platform/src/champion_follow/repositories/profiles.py` | 画像行锁、增量更新与设备安全提前量下的可跟率。 |
| `apps/champion_follow_platform/src/champion_follow/repositories/snapshots.py` | `as-of` 榜单/候选冻结、盲跟结算和快照查询。 |
| `apps/champion_follow_platform/src/champion_follow/repositories/thresholds.py` | 预览父记录、7/30 子窗口、冻结水位和完整双窗口读取门禁；不负责激活。 |
| `apps/champion_follow_platform/src/champion_follow/services/ingestion.py` | 事件批次用例与安全错误映射。 |
| `apps/champion_follow_platform/src/champion_follow/services/history_import.py` | 只读打开冻结 SQLite，检查命名空间/解析版本/摘要，转换旧 `source_events`。 |
| `apps/champion_follow_platform/src/champion_follow/services/issue_builder.py` | 从 PostgreSQL 重建期投影，不完整期只留审计状态。 |
| `apps/champion_follow_platform/src/champion_follow/services/causal.py` | 严格按期号冻结、揭示、结算、更新的单事务处理器。 |
| `apps/champion_follow_platform/src/champion_follow/services/rankings.py` | 最新及指定 `as-of` 冠军榜，只输出匿名编号。 |
| `apps/champion_follow_platform/src/champion_follow/services/threshold_preview.py` | 用冻结候选重放 7/30 天门槛效果并固定预览摘要。 |
| `apps/champion_follow_platform/src/champion_follow/api/health.py` | `/healthz` 数据库存活探针。 |
| `apps/champion_follow_platform/src/champion_follow/api/ingestion.py` | Bearer 认证的 `/v1/collector/session`、`/events`、`/heartbeat` 公开入口；早期内部 `/batches` 仅用于领域接线测试并在 Task 11 删除。 |
| `apps/champion_follow_platform/src/champion_follow/api/rankings.py` | `/v1/rankings/{market}` 只读入口。 |
| `apps/champion_follow_platform/src/champion_follow/api/previews.py` | `/v1/threshold-previews` 只读计算并持久化预览。 |
| `apps/champion_follow_platform/src/champion_follow/main.py` | FastAPI 应用工厂、连接池和服务组装。 |
| `apps/champion_follow_platform/src/champion_follow/cli.py` | `migrate`、`init-namespace`、`register-collector`、`import-legacy`、`process-ready`；采集凭据只写一次性 0600 交接文件。 |
| `apps/champion_follow_platform/tests/conftest.py` | PostgreSQL 测试连接池、迁移和逐测试清理。 |
| `apps/champion_follow_platform/tests/unit/test_markets.py` | 玩法合同与数字分类。 |
| `apps/champion_follow_platform/tests/unit/test_event_contract.py` | 事件形状、序号和隐私输入拒绝。 |
| `apps/champion_follow_platform/tests/unit/test_integrity.py` | 追加、已识别/未识别撤单、对压、缺口和期样本。 |
| `apps/champion_follow_platform/tests/unit/test_statistics.py` | Wilson、收益和盈亏平衡线。 |
| `apps/champion_follow_platform/tests/unit/test_profiles.py` | 近 200、盲跟回撤和分级边界。 |
| `apps/champion_follow_platform/tests/unit/test_thresholds.py` | 等价门槛换算和严格者生效。 |
| `apps/champion_follow_platform/tests/integration/test_health.py` | FastAPI/PostgreSQL 生命周期。 |
| `apps/champion_follow_platform/tests/integration/test_migrations.py` | 迁移幂等性、约束和隐私列。 |
| `apps/champion_follow_platform/tests/integration/test_ingestion_api.py` | 连续 ACK、重放、序号缺口和事件冲突。 |
| `apps/champion_follow_platform/tests/integration/test_history_import.py` | SQLite 导入、命名空间分区、摘要和去重。 |
| `apps/champion_follow_platform/tests/integration/test_collector_registration.py` | 高熵采集凭据只落摘要、0600 一次性交接且不进入 CLI 输出。 |
| `apps/champion_follow_platform/tests/integration/test_collector_wire_contract.py` | Bearer 拒绝、会话 ACK、wire 重放/冲突和心跳新鲜度。 |
| `apps/champion_follow_platform/tests/integration/test_causal_processing.py` | 不完整排除、无泄漏 `as-of`、增量画像和确定回放。 |
| `apps/champion_follow_platform/tests/integration/test_rankings_api.py` | 分类/总体榜、匿名输出和确定破同分。 |
| `apps/champion_follow_platform/tests/integration/test_threshold_preview_api.py` | 7/30 天 `as-of` 过滤、安全提前量和预览版本门禁。 |
| `apps/champion_follow_platform/tests/integration/test_privacy.py` | API、表结构、测试夹具和日志禁止私密字段。 |

## 统一开发命令

以下所有 PostgreSQL 测试命令都使用明显的本地测试假密码，不从终端打印连接串：

```bash
cd /Users/a123/Documents/极速/apps/champion_follow_platform
export POSTGRES_PASSWORD='TEST_ONLY_NOT_A_SECRET'
export TEST_DATABASE_URL='postgresql://champion_follow:TEST_ONLY_NOT_A_SECRET@127.0.0.1:55432/champion_follow_test'
docker compose up -d --wait postgres
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Expected: PostgreSQL healthcheck becomes `healthy`; pip exits 0 without printing any runtime credential beyond the explicit fake test value already shown above.

### Task 1: 建立可运行的 FastAPI/PostgreSQL 骨架

**Files:**
- Create: `apps/champion_follow_platform/pyproject.toml`
- Create: `apps/champion_follow_platform/compose.yaml`
- Create: `apps/champion_follow_platform/.env.example`
- Create: `apps/champion_follow_platform/src/champion_follow/__init__.py`
- Create: `apps/champion_follow_platform/src/champion_follow/config.py`
- Create: `apps/champion_follow_platform/src/champion_follow/db.py`
- Create: `apps/champion_follow_platform/src/champion_follow/api/__init__.py`
- Create: `apps/champion_follow_platform/src/champion_follow/api/health.py`
- Create: `apps/champion_follow_platform/src/champion_follow/main.py`
- Create: `apps/champion_follow_platform/tests/conftest.py`
- Test: `apps/champion_follow_platform/tests/integration/test_health.py`

- [ ] **Step 1: 创建项目目录和失败的健康检查测试**

```python
# tests/integration/test_health.py
import httpx
import pytest

from champion_follow.config import Settings
from champion_follow.main import create_app


@pytest.mark.asyncio
async def test_healthz_checks_postgres(test_database_url):
    app = create_app(Settings(database_url=test_database_url))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
```

```python
# tests/conftest.py
import os

import pytest


@pytest.fixture(scope="session")
def test_database_url():
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.fail("TEST_DATABASE_URL is required for integration tests")
    return value
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
cd /Users/a123/Documents/极速/apps/champion_follow_platform
python3.12 -m venv .venv
.venv/bin/pip install pytest==8.4.1 pytest-asyncio==1.1.0 httpx==0.28.1
PYTHONPATH=src TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q tests/integration/test_health.py
```

Expected: collection fails with `ModuleNotFoundError: No module named 'champion_follow'`.

- [ ] **Step 3: 写入锁定依赖和包入口**

```toml
# pyproject.toml
[build-system]
requires = ["setuptools==80.9.0", "wheel==0.45.1"]
build-backend = "setuptools.build_meta"

[project]
name = "champion-follow-platform"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi==0.116.1",
  "pydantic==2.11.7",
  "pydantic-settings==2.10.1",
  "psycopg[binary]==3.2.9",
  "psycopg-pool==3.2.6",
  "uvicorn[standard]==0.35.0",
]

[project.optional-dependencies]
dev = [
  "httpx==0.28.1",
  "pytest==8.4.1",
  "pytest-asyncio==1.1.0",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
champion_follow = ["sql/*.sql"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"
asyncio_mode = "auto"
markers = ["integration: requires the local PostgreSQL test database"]
```

```python
# src/champion_follow/__init__.py
__version__ = "0.1.0"
```

- [ ] **Step 4: 写入本地 PostgreSQL 和假值环境模板**

```yaml
# compose.yaml
services:
  postgres:
    image: postgres:16.4-alpine
    environment:
      POSTGRES_DB: champion_follow_test
      POSTGRES_USER: champion_follow
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD to a local test value}
    ports:
      - "127.0.0.1:55432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U champion_follow -d champion_follow_test"]
      interval: 1s
      timeout: 3s
      retries: 30
    volumes:
      - champion_follow_pgdata:/var/lib/postgresql/data

volumes:
  champion_follow_pgdata:
```

```dotenv
# .env.example
DATABASE_URL=postgresql://champion_follow:YOUR_LOCAL_POSTGRES_PASSWORD_HERE@127.0.0.1:55432/champion_follow_test
POSTGRES_PASSWORD=YOUR_LOCAL_POSTGRES_PASSWORD_HERE
```

- [ ] **Step 5: 实现严格配置和连接池生命周期**

```python
# src/champion_follow/config.py
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: SecretStr = Field(validation_alias="DATABASE_URL")
    postgres_password: SecretStr | None = Field(
        default=None,
        validation_alias="POSTGRES_PASSWORD",
    )
    service_name: str = "champion-follow-core"
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="forbid",
        populate_by_name=True,
    )
```

```python
# src/champion_follow/db.py
from contextlib import asynccontextmanager

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


def create_pool(database_url: str) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo=database_url,
        min_size=1,
        max_size=8,
        open=False,
        kwargs={"row_factory": dict_row},
    )


@asynccontextmanager
async def open_pool(database_url: str):
    pool = create_pool(database_url)
    try:
        await pool.open(wait=True)
        yield pool
    finally:
        await pool.close()
```

- [ ] **Step 6: 实现健康路由和应用工厂**

```python
# src/champion_follow/api/__init__.py
```

```python
# src/champion_follow/api/health.py
from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, str]:
    async with request.app.state.db.connection() as connection:
        value = await connection.execute("SELECT 1 AS alive")
        row = await value.fetchone()
    return {"status": "ok", "database": "ok" if row["alive"] == 1 else "error"}
```

```python
# src/champion_follow/main.py
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.health import router as health_router
from .config import Settings
from .db import open_pool


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved = settings or Settings()
        async with open_pool(resolved.database_url.get_secret_value()) as pool:
            app.state.db = pool
            yield

    app = FastAPI(title="Champion Follow Core", version="0.1.0", lifespan=lifespan)
    app.include_router(health_router)
    return app


app = create_app()
```

- [ ] **Step 7: 安装项目、启动 PostgreSQL 并确认 GREEN**

Run:

```bash
cd /Users/a123/Documents/极速/apps/champion_follow_platform
.venv/bin/pip install -e '.[dev]'
POSTGRES_PASSWORD='TEST_ONLY_NOT_A_SECRET' docker compose up -d --wait postgres
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q tests/integration/test_health.py
```

Expected: `1 passed`.

- [ ] **Step 8: 提交骨架**

```bash
cd /Users/a123/Documents/极速
git add -- apps/champion_follow_platform
git commit -m "feat: bootstrap champion follow core service"
```

### Task 2: 锁定玩法与脱敏事件合同

**Files:**
- Create: `apps/champion_follow_platform/src/champion_follow/contracts/__init__.py`
- Create: `apps/champion_follow_platform/src/champion_follow/contracts/events.py`
- Create: `apps/champion_follow_platform/src/champion_follow/domain/__init__.py`
- Create: `apps/champion_follow_platform/src/champion_follow/domain/markets.py`
- Test: `apps/champion_follow_platform/tests/unit/test_markets.py`
- Test: `apps/champion_follow_platform/tests/unit/test_event_contract.py`

- [ ] **Step 1: 写 30 个单项、15 个市场和结算的失败测试**

```python
# tests/unit/test_markets.py
import pytest

from champion_follow.domain.markets import (
    ALL_MARKETS,
    Direction,
    MarketFamily,
    parse_play,
    settle_direction,
)


def test_first_version_has_exactly_fifteen_markets_and_thirty_plays():
    assert len(ALL_MARKETS) == 15
    plays = {
        f"P{position}:{direction.value}"
        for position in range(1, 6)
        for direction in Direction
    }
    assert len(plays) == 30
    assert parse_play("P5:合").market == "P5:prime_composite"


@pytest.mark.parametrize(
    ("digit", "family", "expected"),
    [
        (0, MarketFamily.SIZE, Direction.SMALL),
        (5, MarketFamily.SIZE, Direction.BIG),
        (8, MarketFamily.PARITY, Direction.EVEN),
        (9, MarketFamily.PARITY, Direction.ODD),
        (1, MarketFamily.PRIME_COMPOSITE, Direction.PRIME),
        (4, MarketFamily.PRIME_COMPOSITE, Direction.COMPOSITE),
    ],
)
def test_settlement_matches_the_frozen_ffc_contract(digit, family, expected):
    assert settle_direction(digit, family) == expected


@pytest.mark.parametrize("play", ["P0:大", "P6:小", "P1:数字", "P1:龙", "P1:和", "P1:big"])
def test_out_of_scope_plays_are_rejected(play):
    with pytest.raises(ValueError, match="unsupported play"):
        parse_play(play)


class StringablePlay:
    def __str__(self):
        return "P1:大"


@pytest.mark.parametrize("play", ["P１:大", StringablePlay(), "P1:大小"])
def test_parse_play_requires_an_exact_supported_string(play):
    with pytest.raises(ValueError, match="unsupported play"):
        parse_play(play)


@pytest.mark.parametrize("digit", [True, "5", 5.0])
def test_settlement_requires_an_exact_integer_digit(digit):
    with pytest.raises((TypeError, ValueError), match="digit"):
        settle_direction(digit, MarketFamily.SIZE)


@pytest.mark.parametrize("family", ["size", Direction.BIG, None])
def test_settlement_requires_an_actual_market_family(family):
    with pytest.raises((TypeError, ValueError), match="family"):
        settle_direction(5, family)
```

- [ ] **Step 2: 写事件白名单、序号和隐私拒绝的失败测试**

```python
# tests/unit/test_event_contract.py
from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from champion_follow.contracts import events as events_module
from champion_follow.contracts.events import (
    BatchAck,
    CollectorBatch,
    EventKind,
    NormalizedEvent,
    canonical_event_sha256,
)


COLLECTOR = UUID("11111111-1111-4111-8111-111111111111")
ACTOR = "a" * 64
EVENT = "b" * 64 + ":0"
BIGINT_MAX = 2**63 - 1


def bet_event(**changes):
    value = {
        "event_key": EVENT,
        "local_sequence": 41,
        "actor_key": ACTOR,
        "issue": "2607270001",
        "kind": EventKind.BET,
        "source_ms": 1_785_084_000_000,
        "received_at": datetime(2026, 7, 27, tzinfo=timezone.utc),
        "play": "P1:大",
        "amount_fen": 250,
        "result_digits": None,
        "parser_version": "ffc-normalizer-v2",
    }
    value.update(changes)
    return value


def result_event(**changes):
    value = bet_event(
        actor_key=None,
        kind=EventKind.RESULT,
        play=None,
        amount_fen=None,
        result_digits=(1, 2, 3, 4, 5),
    )
    value.update(changes)
    return value


def batch(**changes):
    value = {
        "collector_id": COLLECTOR,
        "namespace_version": "actor-hmac-v1",
        "sequence_start": 41,
        "sequence_end": 41,
        "issue_hint": "2607270001",
        "events": [bet_event()],
    }
    value.update(changes)
    return value


def ack(**changes):
    value = {
        "collector_id": COLLECTOR,
        "highest_contiguous_sequence": 41,
        "accepted_events": 1,
        "status": "accepted",
    }
    value.update(changes)
    return value


def test_event_contract_accepts_only_normalized_anonymous_money():
    event = NormalizedEvent.model_validate(bet_event())
    assert event.actor_key == ACTOR
    assert event.amount_fen == 250
    assert len(canonical_event_sha256(event)) == 64


def test_canonical_event_digest_is_locked_and_mapping_order_independent():
    payload = bet_event()
    reversed_payload = dict(reversed(tuple(payload.items())))

    assert canonical_event_sha256(NormalizedEvent.model_validate(payload)) == (
        "97be27e4635336411b7d908e280f482993af2a97eeed1b29ae30dd3208c4a87e"
    )
    assert canonical_event_sha256(NormalizedEvent.model_validate(reversed_payload)) == (
        "97be27e4635336411b7d908e280f482993af2a97eeed1b29ae30dd3208c4a87e"
    )


@pytest.mark.parametrize(
    "private_field",
    ["uid", "nickname", "cookie", "token", "password", "authorization", "platform_actor_id"],
)
def test_raw_identity_and_credentials_are_rejected(private_field):
    with pytest.raises(ValidationError) as raised:
        NormalizedEvent.model_validate({**bet_event(), private_field: "PRIVATE"})
    assert "Extra inputs are not permitted" in str(raised.value)
    assert "PRIVATE" not in str(raised.value)


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (CollectorBatch, batch(private_field="PRIVATE")),
        (BatchAck, ack(private_field="PRIVATE")),
    ],
)
def test_batch_dto_errors_hide_private_extra_values(model, payload):
    with pytest.raises(ValidationError) as raised:
        model.model_validate(payload)
    assert "PRIVATE" not in str(raised.value)


def test_money_and_result_shapes_cannot_be_mixed():
    with pytest.raises(ValidationError, match="money event"):
        NormalizedEvent.model_validate(bet_event(result_digits=(1, 2, 3, 4, 5)))
    with pytest.raises(ValidationError, match="result event"):
        NormalizedEvent.model_validate(bet_event(
            kind=EventKind.RESULT,
            actor_key=None,
            play=None,
            amount_fen=None,
            result_digits=(1, 2, 3, 4),
        ))


def test_batch_requires_an_exact_contiguous_sequence():
    with pytest.raises(ValidationError, match="contiguous"):
        CollectorBatch.model_validate({
            "collector_id": COLLECTOR,
            "namespace_version": "actor-hmac-v1",
            "sequence_start": 41,
            "sequence_end": 42,
            "issue_hint": "2607270001",
            "events": [bet_event()],
        })


def test_batch_rejects_a_huge_declared_span_without_materializing_range(monkeypatch):
    def forbidden_range(*args):
        raise AssertionError("range must not be called")

    monkeypatch.setattr(events_module, "range", forbidden_range, raising=False)

    with pytest.raises(ValidationError, match="contiguous"):
        CollectorBatch.model_validate(batch(sequence_start=1, sequence_end=BIGINT_MAX))


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("local_sequence", True),
        ("local_sequence", "41"),
        ("local_sequence", 41.0),
        ("source_ms", True),
        ("source_ms", "1785084000000"),
        ("source_ms", 1_785_084_000_000.0),
        ("amount_fen", True),
        ("amount_fen", "250"),
        ("amount_fen", 250.0),
    ],
)
def test_event_integer_fields_reject_coercion(field, invalid):
    with pytest.raises(ValidationError):
        NormalizedEvent.model_validate(bet_event(**{field: invalid}))


@pytest.mark.parametrize("invalid", [True, "1", 1.0])
def test_result_digits_reject_coercion(invalid):
    with pytest.raises(ValidationError):
        NormalizedEvent.model_validate(result_event(result_digits=(invalid, 2, 3, 4, 5)))


@pytest.mark.parametrize("field", ["local_sequence", "source_ms", "amount_fen"])
def test_event_db_integer_fields_reject_bigint_overflow(field):
    with pytest.raises(ValidationError):
        NormalizedEvent.model_validate(bet_event(**{field: BIGINT_MAX + 1}))


@pytest.mark.parametrize("field", ["sequence_start", "sequence_end"])
@pytest.mark.parametrize("invalid", [True, "41", 41.0, BIGINT_MAX + 1])
def test_batch_sequence_fields_are_strict_and_bigint_bounded(field, invalid):
    with pytest.raises(ValidationError):
        CollectorBatch.model_validate(batch(**{field: invalid}))


@pytest.mark.parametrize("field", ["highest_contiguous_sequence", "accepted_events"])
@pytest.mark.parametrize("invalid", [-1, True, "1", 1.0, BIGINT_MAX + 1])
def test_ack_counts_are_strict_non_negative_bigint_bounded(field, invalid):
    with pytest.raises(ValidationError):
        BatchAck.model_validate(ack(**{field: invalid}))


@pytest.mark.parametrize("status", ["rejected", "ACCEPTED", "accepted ", 1])
def test_ack_rejects_unknown_status(status):
    with pytest.raises(ValidationError):
        BatchAck.model_validate(ack(status=status))


@pytest.mark.parametrize("status", ["accepted", "replayed"])
def test_ack_accepts_frozen_statuses(status):
    assert BatchAck.model_validate(ack(status=status)).status == status


@pytest.mark.parametrize("suffix", ["١", "1" * 16])
def test_event_key_rejects_non_ascii_or_oversized_numeric_suffix(suffix):
    with pytest.raises(ValidationError):
        NormalizedEvent.model_validate(bet_event(event_key="b" * 64 + ":" + suffix))
```

- [ ] **Step 3: 运行两个单元测试并确认 RED**

Run:

```bash
cd /Users/a123/Documents/极速/apps/champion_follow_platform
.venv/bin/pytest -q tests/unit/test_markets.py tests/unit/test_event_contract.py
```

Expected: collection fails because `champion_follow.domain.markets` and `champion_follow.contracts.events` do not exist.

- [ ] **Step 4: 实现唯一玩法解析和结算合同**

```python
# src/champion_follow/domain/__init__.py
```

```python
# src/champion_follow/domain/markets.py
import re
from dataclasses import dataclass
from enum import StrEnum


class Direction(StrEnum):
    BIG = "大"
    SMALL = "小"
    ODD = "单"
    EVEN = "双"
    PRIME = "质"
    COMPOSITE = "合"


class MarketFamily(StrEnum):
    SIZE = "size"
    PARITY = "parity"
    PRIME_COMPOSITE = "prime_composite"


FAMILY_DIRECTIONS = {
    MarketFamily.SIZE: (Direction.BIG, Direction.SMALL),
    MarketFamily.PARITY: (Direction.ODD, Direction.EVEN),
    MarketFamily.PRIME_COMPOSITE: (Direction.PRIME, Direction.COMPOSITE),
}
DIRECTION_FAMILY = {
    direction: family
    for family, directions in FAMILY_DIRECTIONS.items()
    for direction in directions
}
OPPOSITE = {
    Direction.BIG: Direction.SMALL,
    Direction.SMALL: Direction.BIG,
    Direction.ODD: Direction.EVEN,
    Direction.EVEN: Direction.ODD,
    Direction.PRIME: Direction.COMPOSITE,
    Direction.COMPOSITE: Direction.PRIME,
}
ALL_MARKETS = tuple(
    f"P{position}:{family.value}"
    for position in range(1, 6)
    for family in MarketFamily
)
PLAY = re.compile(r"P([1-5]):(大|小|单|双|质|合)")


@dataclass(frozen=True)
class ParsedPlay:
    position: int
    direction: Direction
    family: MarketFamily

    @property
    def market(self) -> str:
        return f"P{self.position}:{self.family.value}"

    @property
    def play(self) -> str:
        return f"P{self.position}:{self.direction.value}"


def parse_play(value: str) -> ParsedPlay:
    if type(value) is not str:
        raise ValueError("unsupported play")
    match = PLAY.fullmatch(value)
    if match is None:
        raise ValueError("unsupported play")
    position = int(match.group(1))
    direction = Direction(match.group(2))
    return ParsedPlay(position, direction, DIRECTION_FAMILY[direction])


def settle_direction(digit: int, family: MarketFamily) -> Direction:
    if type(digit) is not int:
        raise TypeError("digit must be an integer")
    if not 0 <= digit <= 9:
        raise ValueError("digit out of range")
    if not isinstance(family, MarketFamily):
        raise TypeError("family must be a MarketFamily")
    if family is MarketFamily.SIZE:
        return Direction.BIG if digit >= 5 else Direction.SMALL
    if family is MarketFamily.PARITY:
        return Direction.ODD if digit % 2 else Direction.EVEN
    if family is MarketFamily.PRIME_COMPOSITE:
        return Direction.PRIME if digit in {1, 2, 3, 5, 7} else Direction.COMPOSITE
    raise ValueError("unsupported family")
```

- [ ] **Step 5: 实现严格 Pydantic 事件和批次 DTO**

```python
# src/champion_follow/contracts/__init__.py
```

```python
# src/champion_follow/contracts/events.py
import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from champion_follow.domain.markets import parse_play


DIGEST = re.compile(r"^[0-9a-f]{64}$")
EVENT_KEY = re.compile(r"^[0-9a-f]{64}(?::(?:block|close|[0-9]{1,15}))?$")
ISSUE = re.compile(r"^[0-9]{8,16}$")
VERSION = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
BIGINT_MAX = 2**63 - 1
DigestText = Annotated[str, Field(pattern=DIGEST.pattern)]
EventKeyText = Annotated[str, Field(pattern=EVENT_KEY.pattern, max_length=80)]
IssueText = Annotated[str, Field(pattern=ISSUE.pattern)]
VersionText = Annotated[str, Field(pattern=VERSION.pattern)]


class EventKind(StrEnum):
    BET = "bet"
    CANCEL = "cancel"
    UNATTRIBUTED_CANCEL = "unattributed_cancel"
    CLOSE = "close"
    RESULT = "result"


class NormalizedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    event_key: EventKeyText
    local_sequence: Annotated[int, Field(strict=True, ge=1, le=BIGINT_MAX)]
    actor_key: DigestText | None
    issue: IssueText
    kind: EventKind
    source_ms: Annotated[int, Field(strict=True, ge=0, le=BIGINT_MAX)]
    received_at: AwareDatetime
    play: str | None
    amount_fen: Annotated[int, Field(strict=True, gt=0, le=BIGINT_MAX)] | None
    result_digits: tuple[Annotated[int, Field(strict=True, ge=0, le=9)], ...] | None
    parser_version: VersionText

    @model_validator(mode="after")
    def validate_shape(self):
        money = self.kind in {EventKind.BET, EventKind.CANCEL}
        if money:
            if self.actor_key is None or self.play is None or self.amount_fen is None:
                raise ValueError("money event requires actor, play and amount")
            if self.result_digits is not None:
                raise ValueError("money event cannot contain result")
            parse_play(self.play)
            return self
        if self.actor_key is not None or self.play is not None or self.amount_fen is not None:
            raise ValueError("marker event cannot contain money identity")
        if self.kind is EventKind.RESULT:
            if self.result_digits is None or len(self.result_digits) != 5:
                raise ValueError("result event requires five digits")
        elif self.result_digits is not None:
            raise ValueError("non-result event cannot contain digits")
        return self


class CollectorBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    collector_id: UUID
    namespace_version: VersionText
    sequence_start: Annotated[int, Field(strict=True, ge=1, le=BIGINT_MAX)]
    sequence_end: Annotated[int, Field(strict=True, ge=1, le=BIGINT_MAX)]
    issue_hint: IssueText
    events: tuple[NormalizedEvent, ...]

    @model_validator(mode="after")
    def validate_sequence(self):
        if not self.events:
            raise ValueError("batch events must be an exact contiguous sequence")
        if self.sequence_end != self.sequence_start + len(self.events) - 1:
            raise ValueError("batch events must be an exact contiguous sequence")
        if any(
            event.local_sequence != self.sequence_start + offset
            for offset, event in enumerate(self.events)
        ):
            raise ValueError("batch events must be an exact contiguous sequence")
        if any(event.issue != self.issue_hint for event in self.events):
            raise ValueError("batch cannot cross issues")
        return self


class BatchAck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    collector_id: UUID
    highest_contiguous_sequence: Annotated[
        int, Field(strict=True, ge=0, le=BIGINT_MAX)
    ]
    accepted_events: Annotated[int, Field(strict=True, ge=0, le=BIGINT_MAX)]
    status: Literal["accepted", "replayed"]


def canonical_event_sha256(event: NormalizedEvent) -> str:
    payload = json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

- [ ] **Step 6: 运行合同测试并确认 GREEN**

Run:

```bash
cd /Users/a123/Documents/极速/apps/champion_follow_platform
.venv/bin/pytest -q tests/unit/test_markets.py tests/unit/test_event_contract.py
```

Expected: all parameterized cases pass; output contains no actor value other than the synthetic repeated hexadecimal fixture.

- [ ] **Step 7: 提交玩法和事件合同**

```bash
cd /Users/a123/Documents/极速
git add -- apps/champion_follow_platform/src/champion_follow/contracts apps/champion_follow_platform/src/champion_follow/domain apps/champion_follow_platform/tests/unit
git commit -m "feat: define anonymous ffc event contracts"
```

### Task 3: 建立可校验、幂等的 PostgreSQL 核心数据模型

**Files:**
- Create: `apps/champion_follow_platform/src/champion_follow/sql/0001_core.sql`
- Create: `apps/champion_follow_platform/src/champion_follow/migrations.py`
- Modify: `apps/champion_follow_platform/tests/conftest.py`
- Test: `apps/champion_follow_platform/tests/integration/test_migrations.py`

- [ ] **Step 1: 先写迁移安全、约束和隐私边界 RED 测试**

`tests/integration/test_migrations.py` 必须先分别证明以下失败路径：原始字节摘要、不可排序
`Traversable`、核心资源缺失、迁移编号非连续、数据库 ledger 不是打包迁移严格前缀、
已应用资源消失/摘要漂移、SQL 失败和协程取消回滚、并发迁移只应用一次；同时覆盖命名空间、
期完整性、采集 ACK 收据、历史锚点、市场方向、结果数组、画像统计、候选榜单来源、门槛窗口
和隐私列边界。普通数据库测试必须使用每测试随机 schema，App 与 seed pool 必须共享同一个
带 `search_path` 的 DSN；测试数据库名必须以 `_test` 结尾，且基础 DSN 禁止预置 `options`。

- [ ] **Step 2: 运行并保存 RED 证据**

```bash
cd /Users/a123/Documents/极速/apps/champion_follow_platform
PYTHONPATH=src TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q \
  tests/integration/test_migrations.py tests/integration/test_health.py
```

Expected: 每组新增约束先因缺失相应数据库/fixture 行为而失败；SQL 失败和取消测试必须证明事务
不会留下 schema 或 ledger 半成品。

- [ ] **Step 3: 写入冻结前最终核心 SQL**

`src/champion_follow/sql/0001_core.sql` 的冻结内容必须与下列内容完全一致：

```sql
CREATE TABLE identity_namespaces (
    id UUID PRIMARY KEY,
    version VARCHAR(64) NOT NULL UNIQUE,
    mode VARCHAR(16) NOT NULL CHECK (mode IN ('active','baseline')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_active_identity_namespace
    ON identity_namespaces ((mode)) WHERE mode='active';

CREATE TABLE anonymous_actors (
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    actor_key CHAR(64) NOT NULL CHECK (actor_key ~ '^[0-9a-f]{64}$'),
    display_no BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    first_seen_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (namespace_id,actor_key)
);

CREATE TABLE collectors (
    id UUID PRIMARY KEY,
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    wire_id VARCHAR(80) NOT NULL UNIQUE
        CHECK (wire_id ~ '^collector-[a-z0-9-]{3,64}$'),
    label VARCHAR(64) NOT NULL UNIQUE CHECK (label ~ '^[a-z0-9][a-z0-9._-]{2,63}$'),
    parser_version VARCHAR(64) NOT NULL,
    bearer_sha256 CHAR(64) NOT NULL UNIQUE CHECK (bearer_sha256 ~ '^[0-9a-f]{64}$'),
    ack_sequence BIGINT NOT NULL DEFAULT 0 CHECK (ack_sequence >= 0),
    ack_event_key VARCHAR(80)
        CHECK (ack_event_key IS NULL OR ack_event_key ~ '^[0-9a-f]{64}(:(block|close|[0-9]{1,15}))?$'),
    history_anchor_event_key VARCHAR(80)
        CHECK (
            history_anchor_event_key IS NULL
            OR history_anchor_event_key ~ '^[0-9a-f]{64}(:(block|close|[0-9]{1,15}))?$'
        ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (namespace_id,id),
    CHECK (
        (ack_sequence=0 AND ack_event_key IS NULL)
        OR (ack_sequence>=1 AND ack_event_key IS NOT NULL)
    )
);

CREATE TABLE collector_heartbeats (
    collector_id UUID PRIMARY KEY REFERENCES collectors(id) ON DELETE CASCADE,
    issue VARCHAR(16) CHECK (issue IS NULL OR issue ~ '^[0-9]{8,16}$'),
    phase VARCHAR(16) NOT NULL CHECK (phase IN ('BETTING','CLOSED','UNKNOWN')),
    countdown_ms BIGINT NOT NULL CHECK (countdown_ms >= 0),
    observed_at_ms BIGINT NOT NULL CHECK (observed_at_ms >= 0),
    last_journal_sequence BIGINT NOT NULL CHECK (last_journal_sequence >= 0),
    capture_healthy BOOLEAN NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE import_batches (
    id UUID PRIMARY KEY,
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    partition VARCHAR(16) NOT NULL CHECK (partition IN ('current','baseline')),
    source_label VARCHAR(128) NOT NULL,
    source_sha256 CHAR(64) NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    parser_version VARCHAR(64) NOT NULL,
    row_count BIGINT NOT NULL CHECK (row_count >= 0),
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (namespace_id,source_sha256),
    UNIQUE (namespace_id,id,partition)
);

CREATE TABLE game_issues (
    issue VARCHAR(16) PRIMARY KEY CHECK (issue ~ '^[0-9]{8,16}$'),
    issue_no NUMERIC(16,0) NOT NULL UNIQUE,
    CHECK (issue_no = issue::NUMERIC)
);

CREATE TABLE issue_evaluations (
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    issue VARCHAR(16) NOT NULL REFERENCES game_issues(issue),
    closed_ms BIGINT,
    result_ms BIGINT,
    result_digits SMALLINT[],
    integrity_status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (integrity_status IN ('pending','complete','incomplete','processed')),
    integrity_reasons TEXT[] NOT NULL DEFAULT '{}',
    integrity_version VARCHAR(64),
    processed_at TIMESTAMPTZ,
    PRIMARY KEY (namespace_id,issue),
    CHECK (closed_ms IS NULL OR closed_ms >= 0),
    CHECK (result_ms IS NULL OR result_ms >= 0),
    CHECK (closed_ms IS NULL OR result_ms IS NULL OR result_ms >= closed_ms),
    CHECK (
        result_digits IS NULL OR (
            array_ndims(result_digits)=1
            AND cardinality(result_digits)=5
            AND array_position(result_digits,NULL) IS NULL
            AND result_digits <@ ARRAY[0,1,2,3,4,5,6,7,8,9]::SMALLINT[]
        )
    ),
    CHECK (
        COALESCE(array_ndims(integrity_reasons),1)=1
        AND cardinality(integrity_reasons) <= 16
        AND array_position(integrity_reasons,NULL) IS NULL
        AND integrity_reasons::TEXT
            ~ '^\{([a-z0-9_]+(,[a-z0-9_]+)*)?\}$'
    ),
    CHECK (
        (integrity_status='pending'
            AND closed_ms IS NULL AND result_ms IS NULL AND result_digits IS NULL
            AND cardinality(integrity_reasons)=0 AND integrity_version IS NULL
            AND processed_at IS NULL)
        OR
        (integrity_status='incomplete'
            AND cardinality(integrity_reasons)>=1 AND integrity_version IS NOT NULL
            AND processed_at IS NULL)
        OR
        (integrity_status='complete'
            AND closed_ms IS NOT NULL AND result_ms IS NOT NULL AND result_digits IS NOT NULL
            AND cardinality(integrity_reasons)=0 AND integrity_version IS NOT NULL
            AND processed_at IS NULL)
        OR
        (integrity_status='processed'
            AND closed_ms IS NOT NULL AND result_ms IS NOT NULL AND result_digits IS NOT NULL
            AND cardinality(integrity_reasons)=0 AND integrity_version IS NOT NULL
            AND processed_at IS NOT NULL)
    )
);

CREATE TABLE source_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    partition VARCHAR(16) NOT NULL CHECK (partition IN ('current','baseline')),
    collector_id UUID,
    import_batch_id UUID,
    stream_sequence BIGINT CHECK (stream_sequence IS NULL OR stream_sequence >= 1),
    event_key VARCHAR(80) NOT NULL
        CHECK (event_key ~ '^[0-9a-f]{64}(:(block|close|[0-9]{1,15}))?$'),
    payload_sha256 CHAR(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    actor_key CHAR(64),
    issue VARCHAR(16) NOT NULL REFERENCES game_issues(issue),
    kind VARCHAR(24) NOT NULL
        CHECK (kind IN (
            'bet','cancel','unattributed_cancel','close','result','capture_gap','issue_status'
        )),
    history_anchor_event_key VARCHAR(80) GENERATED ALWAYS AS (
        CASE WHEN partition='current' AND kind IN ('bet','cancel') THEN event_key END
    ) STORED,
    source_ms BIGINT NOT NULL CHECK (source_ms >= 0),
    received_at TIMESTAMPTZ NOT NULL,
    position SMALLINT CHECK (position BETWEEN 1 AND 5),
    direction VARCHAR(4) CHECK (direction IN ('大','小','单','双','质','合')),
    amount_fen BIGINT CHECK (amount_fen > 0),
    result_digits SMALLINT[],
    gap_reason VARCHAR(64) CHECK (gap_reason IS NULL OR gap_reason ~ '^[a-z0-9_]+$'),
    reported_complete BOOLEAN,
    reported_reasons TEXT[],
    parser_version VARCHAR(64) NOT NULL,
    source_label VARCHAR(128) NOT NULL,
    UNIQUE (namespace_id,event_key),
    UNIQUE (namespace_id,event_key,payload_sha256),
    UNIQUE (namespace_id,history_anchor_event_key),
    FOREIGN KEY (namespace_id,actor_key)
        REFERENCES anonymous_actors(namespace_id,actor_key),
    FOREIGN KEY (namespace_id,collector_id)
        REFERENCES collectors(namespace_id,id),
    FOREIGN KEY (namespace_id,import_batch_id,partition)
        REFERENCES import_batches(namespace_id,id,partition),
    FOREIGN KEY (namespace_id,issue)
        REFERENCES issue_evaluations(namespace_id,issue),
    CHECK (
        (collector_id IS NOT NULL AND import_batch_id IS NULL
            AND stream_sequence IS NOT NULL AND partition='current')
        OR
        (collector_id IS NULL AND import_batch_id IS NOT NULL AND stream_sequence IS NULL)
    ),
    CHECK (
        result_digits IS NULL OR (
            array_ndims(result_digits)=1
            AND cardinality(result_digits)=5
            AND array_position(result_digits,NULL) IS NULL
            AND result_digits <@ ARRAY[0,1,2,3,4,5,6,7,8,9]::SMALLINT[]
        )
    ),
    CHECK (
        (kind IN ('bet','cancel') AND actor_key IS NOT NULL AND position IS NOT NULL
            AND direction IS NOT NULL AND amount_fen IS NOT NULL AND result_digits IS NULL
            AND gap_reason IS NULL AND reported_complete IS NULL AND reported_reasons IS NULL)
        OR
        (kind IN ('unattributed_cancel','close') AND actor_key IS NULL AND position IS NULL
            AND direction IS NULL AND amount_fen IS NULL AND result_digits IS NULL
            AND gap_reason IS NULL AND reported_complete IS NULL AND reported_reasons IS NULL)
        OR
        (kind='result' AND actor_key IS NULL AND position IS NULL AND direction IS NULL
            AND amount_fen IS NULL AND result_digits IS NOT NULL
            AND gap_reason IS NULL AND reported_complete IS NULL AND reported_reasons IS NULL)
        OR
        (kind='capture_gap' AND actor_key IS NULL AND position IS NULL AND direction IS NULL
            AND amount_fen IS NULL AND result_digits IS NULL AND gap_reason IS NOT NULL
            AND reported_complete IS NULL AND reported_reasons IS NULL)
        OR
        (kind='issue_status' AND actor_key IS NULL AND position IS NULL AND direction IS NULL
            AND amount_fen IS NULL AND result_digits IS NULL AND gap_reason IS NULL
            AND reported_complete IS NOT NULL AND reported_reasons IS NOT NULL
            AND COALESCE(array_ndims(reported_reasons),1)=1
            AND cardinality(reported_reasons) <= 16
            AND array_position(reported_reasons,NULL) IS NULL
            AND reported_reasons::TEXT
                ~ '^\{([a-z0-9_]+(,[a-z0-9_]+)*)?\}$'
            AND (
                (reported_complete AND cardinality(reported_reasons)=0)
                OR (NOT reported_complete AND cardinality(reported_reasons)>=1)
            ))
    )
);
CREATE UNIQUE INDEX source_stream_sequence_once
    ON source_events(collector_id,stream_sequence) WHERE collector_id IS NOT NULL;

CREATE TABLE collector_event_receipts (
    namespace_id UUID NOT NULL,
    collector_id UUID NOT NULL,
    stream_sequence BIGINT NOT NULL CHECK (stream_sequence >= 1),
    event_key VARCHAR(80) NOT NULL
        CHECK (event_key ~ '^[0-9a-f]{64}(:(block|close|[0-9]{1,15}))?$'),
    payload_sha256 CHAR(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    wire_sha256 CHAR(64) CHECK (wire_sha256 IS NULL OR wire_sha256 ~ '^[0-9a-f]{64}$'),
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (collector_id,stream_sequence),
    UNIQUE (collector_id,stream_sequence,event_key),
    FOREIGN KEY (namespace_id,collector_id)
        REFERENCES collectors(namespace_id,id),
    FOREIGN KEY (namespace_id,event_key,payload_sha256)
        REFERENCES source_events(namespace_id,event_key,payload_sha256)
);
ALTER TABLE collectors
    ADD CONSTRAINT collector_ack_references_receipt
    FOREIGN KEY (id,ack_sequence,ack_event_key)
    REFERENCES collector_event_receipts(collector_id,stream_sequence,event_key)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE collectors
    ADD CONSTRAINT collector_history_anchor_references_source_event
    FOREIGN KEY (namespace_id,history_anchor_event_key)
    REFERENCES source_events(namespace_id,history_anchor_event_key);
CREATE INDEX source_issue_order ON source_events(namespace_id,issue,source_ms,event_key);
CREATE INDEX source_actor_order ON source_events(namespace_id,actor_key,issue,source_ms);

CREATE TABLE capture_gaps (
    id UUID PRIMARY KEY,
    collector_id UUID NOT NULL REFERENCES collectors(id),
    from_sequence BIGINT NOT NULL CHECK (from_sequence >= 1),
    to_sequence BIGINT NOT NULL CHECK (to_sequence >= from_sequence),
    affected_issue VARCHAR(16) NOT NULL REFERENCES game_issues(issue),
    reason VARCHAR(64) NOT NULL CHECK (reason ~ '^[a-z0-9_]+$'),
    opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    recovered_at TIMESTAMPTZ,
    CHECK (recovered_at IS NULL OR recovered_at >= opened_at),
    UNIQUE (collector_id,from_sequence,to_sequence)
);

CREATE TABLE prediction_samples (
    id UUID PRIMARY KEY,
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    actor_key CHAR(64) NOT NULL,
    issue VARCHAR(16) NOT NULL REFERENCES game_issues(issue),
    market VARCHAR(32) NOT NULL
        CHECK (market ~ '^P[1-5]:(size|parity|prime_composite)$'),
    direction VARCHAR(4) NOT NULL CHECK (direction IN ('大','小','单','双','质','合')),
    signal_source_ms BIGINT NOT NULL CHECK (signal_source_ms >= 0),
    lead_ms BIGINT NOT NULL CHECK (lead_ms >= 0),
    outcome SMALLINT NOT NULL CHECK (outcome IN (-1,0,1)),
    unit_profit_micros INTEGER NOT NULL CHECK (unit_profit_micros IN (-1000000,0,960000)),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (namespace_id,actor_key,issue,market),
    FOREIGN KEY (namespace_id,actor_key)
        REFERENCES anonymous_actors(namespace_id,actor_key),
    FOREIGN KEY (namespace_id,issue)
        REFERENCES issue_evaluations(namespace_id,issue),
    CHECK (
        (market ~ '^P[1-5]:size$' AND direction IN ('大','小'))
        OR (market ~ '^P[1-5]:parity$' AND direction IN ('单','双'))
        OR (market ~ '^P[1-5]:prime_composite$' AND direction IN ('质','合'))
    ),
    CHECK (
        (outcome=1 AND unit_profit_micros=960000)
        OR (outcome=0 AND unit_profit_micros=0)
        OR (outcome=-1 AND unit_profit_micros=-1000000)
    )
);
CREATE INDEX prediction_market_issue ON prediction_samples(market,issue);

CREATE TABLE actor_profiles (
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    actor_key CHAR(64) NOT NULL,
    scope VARCHAR(32) NOT NULL
        CHECK (scope='overall' OR scope ~ '^P[1-5]:(size|parity|prime_composite)$'),
    sample_count BIGINT NOT NULL DEFAULT 0,
    wins BIGINT NOT NULL DEFAULT 0,
    losses BIGINT NOT NULL DEFAULT 0,
    pushes BIGINT NOT NULL DEFAULT 0,
    recent_outcomes SMALLINT[] NOT NULL DEFAULT '{}',
    raw_win_rate NUMERIC(18,12) NOT NULL DEFAULT 0,
    all_wilson_lower NUMERIC(18,12) NOT NULL DEFAULT 0,
    recent_wilson_lower NUMERIC(18,12) NOT NULL DEFAULT 0,
    conservative_win_rate NUMERIC(18,12) NOT NULL DEFAULT 0,
    unit_return NUMERIC(18,12) NOT NULL DEFAULT 0,
    conservative_unit_return NUMERIC(18,12) NOT NULL DEFAULT -1,
    blind_count BIGINT NOT NULL DEFAULT 0,
    blind_wins BIGINT NOT NULL DEFAULT 0,
    blind_losses BIGINT NOT NULL DEFAULT 0,
    blind_profit_micros BIGINT NOT NULL DEFAULT 0,
    blind_peak_micros BIGINT NOT NULL DEFAULT 0,
    blind_max_drawdown_micros BIGINT NOT NULL DEFAULT 0,
    level VARCHAR(16) NOT NULL DEFAULT 'observed'
        CHECK (level IN ('observed','candidate','formal','core')),
    first_seen_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    statistics_version VARCHAR(64) NOT NULL,
    updated_through_issue VARCHAR(16),
    PRIMARY KEY (namespace_id,actor_key,scope),
    FOREIGN KEY (namespace_id,actor_key)
        REFERENCES anonymous_actors(namespace_id,actor_key),
    FOREIGN KEY (namespace_id,updated_through_issue)
        REFERENCES issue_evaluations(namespace_id,issue),
    CHECK (sample_count >= 0 AND wins >= 0 AND losses >= 0 AND pushes >= 0),
    CHECK (sample_count = wins + losses + pushes),
    CHECK (
        COALESCE(array_ndims(recent_outcomes),1)=1
        AND cardinality(recent_outcomes) = LEAST(sample_count,200)
        AND array_position(recent_outcomes,NULL) IS NULL
        AND recent_outcomes <@ ARRAY[-1,0,1]::SMALLINT[]
    ),
    CHECK (
        raw_win_rate BETWEEN 0 AND 1
        AND all_wilson_lower BETWEEN 0 AND 1
        AND recent_wilson_lower BETWEEN 0 AND 1
        AND conservative_win_rate BETWEEN 0 AND 1
        AND all_wilson_lower <= raw_win_rate
        AND conservative_win_rate <= raw_win_rate
    ),
    CHECK (
        unit_return BETWEEN -1 AND 0.96
        AND conservative_unit_return BETWEEN -1 AND 0.96
    ),
    CHECK (
        raw_win_rate = CASE
            WHEN wins + losses = 0 THEN 0
            ELSE round(wins::NUMERIC / (wins + losses),12)
        END
    ),
    CHECK (
        unit_return = CASE
            WHEN wins + losses = 0 THEN 0
            ELSE round((wins::NUMERIC * 0.96 - losses) / (wins + losses),12)
        END
    ),
    CHECK (
        conservative_win_rate = CASE
            WHEN sample_count < 50 THEN all_wilson_lower
            ELSE LEAST(all_wilson_lower,recent_wilson_lower)
        END
        AND conservative_unit_return = round(1.96 * conservative_win_rate - 1,12)
    ),
    CHECK (
        blind_count >= 0 AND blind_wins >= 0 AND blind_losses >= 0
        AND blind_wins + blind_losses <= blind_count
    ),
    CHECK (blind_profit_micros = blind_wins * 960000 - blind_losses * 1000000),
    CHECK (
        blind_peak_micros >= 0
        AND blind_peak_micros >= blind_profit_micros
        AND blind_max_drawdown_micros >= 0
        AND blind_max_drawdown_micros >= blind_peak_micros - blind_profit_micros
    ),
    CHECK (
        (first_seen_at IS NULL AND last_seen_at IS NULL)
        OR (first_seen_at IS NOT NULL AND last_seen_at IS NOT NULL
            AND last_seen_at >= first_seen_at)
    ),
    CHECK (
        scope<>'overall' OR (
            (level='observed' AND sample_count < 30)
            OR (
                level='candidate' AND sample_count >= 30
                AND NOT (sample_count >= 200 AND blind_count >= 50
                    AND blind_profit_micros > 0)
            )
            OR (
                level='formal' AND sample_count >= 200 AND blind_count >= 50
                AND blind_profit_micros > 0
                AND NOT (sample_count >= 500 AND blind_count >= 200)
            )
            OR (
                level='core' AND sample_count >= 500 AND blind_count >= 200
                AND blind_profit_micros > 0
            )
        )
    )
);
CREATE INDEX profile_market_rank
    ON actor_profiles(scope,conservative_unit_return DESC,sample_count DESC,actor_key);

CREATE TABLE ranking_snapshots (
    id UUID PRIMARY KEY,
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    issue VARCHAR(16) NOT NULL REFERENCES game_issues(issue),
    scope VARCHAR(32) NOT NULL
        CHECK (scope='overall' OR scope ~ '^P[1-5]:(size|parity|prime_composite)$'),
    frozen_at TIMESTAMPTZ NOT NULL,
    statistics_version VARCHAR(64) NOT NULL,
    manifest_sha256 CHAR(64) NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (namespace_id,issue,scope),
    UNIQUE (namespace_id,id),
    UNIQUE (namespace_id,id,scope),
    UNIQUE (namespace_id,id,issue,scope,frozen_at,statistics_version),
    FOREIGN KEY (namespace_id,issue)
        REFERENCES issue_evaluations(namespace_id,issue)
);

CREATE TABLE ranking_entries (
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    snapshot_id UUID NOT NULL,
    actor_key CHAR(64) NOT NULL,
    rank INTEGER NOT NULL CHECK (rank >= 1),
    sample_count BIGINT NOT NULL,
    wins BIGINT NOT NULL,
    losses BIGINT NOT NULL,
    pushes BIGINT NOT NULL,
    raw_win_rate NUMERIC(18,12) NOT NULL,
    all_wilson_lower NUMERIC(18,12) NOT NULL,
    recent_wilson_lower NUMERIC(18,12) NOT NULL,
    conservative_win_rate NUMERIC(18,12) NOT NULL,
    unit_return NUMERIC(18,12) NOT NULL,
    conservative_unit_return NUMERIC(18,12) NOT NULL,
    blind_count BIGINT NOT NULL,
    blind_profit_micros BIGINT NOT NULL,
    blind_max_drawdown_micros BIGINT NOT NULL,
    level VARCHAR(16) NOT NULL
        CHECK (level IN ('observed','candidate','formal','core')),
    PRIMARY KEY (snapshot_id,actor_key),
    UNIQUE (snapshot_id,rank),
    UNIQUE (namespace_id,snapshot_id,actor_key,rank),
    FOREIGN KEY (namespace_id,snapshot_id)
        REFERENCES ranking_snapshots(namespace_id,id) ON DELETE CASCADE,
    FOREIGN KEY (namespace_id,actor_key)
        REFERENCES anonymous_actors(namespace_id,actor_key),
    CHECK (sample_count >= 0 AND wins >= 0 AND losses >= 0 AND pushes >= 0),
    CHECK (sample_count = wins + losses + pushes),
    CHECK (
        raw_win_rate BETWEEN 0 AND 1
        AND all_wilson_lower BETWEEN 0 AND 1
        AND recent_wilson_lower BETWEEN 0 AND 1
        AND conservative_win_rate BETWEEN 0 AND 1
        AND all_wilson_lower <= raw_win_rate
        AND conservative_win_rate <= raw_win_rate
    ),
    CHECK (
        unit_return BETWEEN -1 AND 0.96
        AND conservative_unit_return BETWEEN -1 AND 0.96
    ),
    CHECK (
        raw_win_rate = CASE
            WHEN wins + losses = 0 THEN 0
            ELSE round(wins::NUMERIC / (wins + losses),12)
        END
    ),
    CHECK (
        unit_return = CASE
            WHEN wins + losses = 0 THEN 0
            ELSE round((wins::NUMERIC * 0.96 - losses) / (wins + losses),12)
        END
    ),
    CHECK (
        conservative_win_rate = CASE
            WHEN sample_count < 50 THEN all_wilson_lower
            ELSE LEAST(all_wilson_lower,recent_wilson_lower)
        END
        AND conservative_unit_return = round(1.96 * conservative_win_rate - 1,12)
    ),
    CHECK (blind_count >= 0 AND blind_max_drawdown_micros >= 0)
);

CREATE TABLE asof_candidates (
    id UUID PRIMARY KEY,
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    snapshot_id UUID NOT NULL,
    issue VARCHAR(16) NOT NULL REFERENCES game_issues(issue),
    market VARCHAR(32) NOT NULL
        CHECK (market ~ '^P[1-5]:(size|parity|prime_composite)$'),
    actor_key CHAR(64) NOT NULL,
    direction VARCHAR(4) NOT NULL CHECK (direction IN ('大','小','单','双','质','合')),
    signal_source_ms BIGINT NOT NULL CHECK (signal_source_ms >= 0),
    lead_ms BIGINT NOT NULL CHECK (lead_ms >= 0),
    prior_lead_times_ms BIGINT[] NOT NULL,
    profile_level VARCHAR(16) NOT NULL
        CHECK (profile_level IN ('observed','candidate','formal','core')),
    profile_sample_count BIGINT NOT NULL,
    profile_wins BIGINT NOT NULL,
    profile_losses BIGINT NOT NULL,
    profile_raw_win_rate NUMERIC(18,12) NOT NULL,
    profile_conservative_win_rate NUMERIC(18,12) NOT NULL,
    profile_conservative_unit_return NUMERIC(18,12) NOT NULL,
    base_rank INTEGER NOT NULL CHECK (base_rank >= 1),
    statistics_version VARCHAR(64) NOT NULL,
    frozen_at TIMESTAMPTZ NOT NULL,
    outcome SMALLINT CHECK (outcome IN (-1,0,1)),
    unit_profit_micros INTEGER CHECK (unit_profit_micros IN (-1000000,0,960000)),
    settled_at TIMESTAMPTZ,
    UNIQUE (namespace_id,issue,market,actor_key),
    FOREIGN KEY (namespace_id,actor_key)
        REFERENCES anonymous_actors(namespace_id,actor_key),
    FOREIGN KEY (
        namespace_id,snapshot_id,issue,market,frozen_at,statistics_version
    ) REFERENCES ranking_snapshots(
        namespace_id,id,issue,scope,frozen_at,statistics_version
    ),
    FOREIGN KEY (namespace_id,snapshot_id,actor_key,base_rank)
        REFERENCES ranking_entries(namespace_id,snapshot_id,actor_key,rank),
    CHECK (
        (market ~ '^P[1-5]:size$' AND direction IN ('大','小'))
        OR (market ~ '^P[1-5]:parity$' AND direction IN ('单','双'))
        OR (market ~ '^P[1-5]:prime_composite$' AND direction IN ('质','合'))
    ),
    CHECK (
        COALESCE(array_ndims(prior_lead_times_ms),1)=1
        AND array_position(prior_lead_times_ms,NULL) IS NULL
        AND 0 <= ALL(prior_lead_times_ms)
        AND cardinality(prior_lead_times_ms) <= profile_sample_count
    ),
    CHECK (
        profile_sample_count >= 0 AND profile_wins >= 0 AND profile_losses >= 0
        AND profile_wins + profile_losses <= profile_sample_count
    ),
    CHECK (
        profile_raw_win_rate BETWEEN 0 AND 1
        AND profile_conservative_win_rate BETWEEN 0 AND 1
        AND profile_conservative_win_rate <= profile_raw_win_rate
        AND profile_conservative_unit_return BETWEEN -1 AND 0.96
    ),
    CHECK (
        profile_raw_win_rate = CASE
            WHEN profile_wins + profile_losses = 0 THEN 0
            ELSE round(profile_wins::NUMERIC / (profile_wins + profile_losses),12)
        END
        AND profile_conservative_unit_return =
            round(1.96 * profile_conservative_win_rate - 1,12)
    ),
    CHECK (
        (outcome IS NULL AND unit_profit_micros IS NULL AND settled_at IS NULL)
        OR (
            outcome IS NOT NULL AND unit_profit_micros IS NOT NULL AND settled_at IS NOT NULL
            AND settled_at >= frozen_at
            AND (
                (outcome=1 AND unit_profit_micros=960000)
                OR (outcome=0 AND unit_profit_micros=0)
                OR (outcome=-1 AND unit_profit_micros=-1000000)
            )
        )
    )
);
CREATE INDEX asof_window ON asof_candidates(frozen_at,issue,market);

CREATE TABLE processing_state (
    namespace_id UUID PRIMARY KEY REFERENCES identity_namespaces(id),
    last_issue_no NUMERIC(16,0) NOT NULL DEFAULT 0 CHECK (last_issue_no >= 0),
    last_issue VARCHAR(16),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (namespace_id,last_issue)
        REFERENCES issue_evaluations(namespace_id,issue),
    CHECK (
        (last_issue_no=0 AND last_issue IS NULL)
        OR (last_issue IS NOT NULL AND last_issue_no=last_issue::NUMERIC)
    )
);

CREATE TABLE threshold_previews (
    id UUID PRIMARY KEY,
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    request_sha256 CHAR(64) NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    safe_lead_ms BIGINT NOT NULL CHECK (safe_lead_ms >= 0),
    request_config JSONB NOT NULL CHECK (jsonb_typeof(request_config)='object'),
    as_of TIMESTAMPTZ NOT NULL,
    watermark_snapshot_id UUID NOT NULL,
    watermark_scope VARCHAR(7) GENERATED ALWAYS AS ('overall') STORED,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (namespace_id,request_sha256),
    FOREIGN KEY (namespace_id,watermark_snapshot_id,watermark_scope)
        REFERENCES ranking_snapshots(namespace_id,id,scope)
);

CREATE TABLE threshold_preview_windows (
    preview_id UUID NOT NULL REFERENCES threshold_previews(id) ON DELETE CASCADE,
    window_days SMALLINT NOT NULL CHECK (window_days IN (7,30)),
    frozen_signal_count BIGINT NOT NULL CHECK (frozen_signal_count >= 0),
    executable_signal_count BIGINT NOT NULL CHECK (
        executable_signal_count >= 0 AND executable_signal_count <= frozen_signal_count
    ),
    win_count BIGINT NOT NULL CHECK (win_count >= 0),
    loss_count BIGINT NOT NULL CHECK (loss_count >= 0),
    unit_profit_micros BIGINT NOT NULL,
    raw_win_rate NUMERIC(18,12) NOT NULL CHECK (raw_win_rate BETWEEN 0 AND 1),
    conservative_win_rate NUMERIC(18,12) NOT NULL
        CHECK (conservative_win_rate BETWEEN 0 AND 1),
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (preview_id,window_days),
    CHECK (win_count + loss_count <= executable_signal_count),
    CHECK (unit_profit_micros = win_count * 960000 - loss_count * 1000000),
    CHECK (
        raw_win_rate = CASE
            WHEN win_count + loss_count = 0 THEN 0
            ELSE round(win_count::NUMERIC / (win_count + loss_count),12)
        END
    ),
    CHECK (conservative_win_rate <= raw_win_rate),
    CHECK (window_end > window_start)
);
```

关键权威边界：`source_events` 是命名空间内唯一语义事件；
`collector_event_receipts` 是每个 journal 序号独立、可 ACK 的持久收据，`wire_sha256`
只存于收据。`issue_evaluations` 以 `(namespace_id,issue)` 隔离完整性；历史锚点只能指向
`current` 的 bet/cancel；候选必须绑定同一冻结快照中的 actor/rank。等级是全局属性，只有
`actor_profiles.scope='overall'` 可由该行计数校验等级，市场画像/榜单只携带冻结的全局等级。
`threshold_previews` 是请求与冻结水位父记录，7/30 日指标存入子表；恰好两窗口由 Task 10
的单事务仓库写入和读取门禁保证。`capture_gaps` 是允许重叠/嵌套的缺口观察，不是规范化区间。
Plan 01 不拥有设备实际订单结算表；实际订单/结算由 Plan 03 建模。

- [ ] **Step 4: 实现原始字节摘要和严格前缀迁移器**

```python
# src/champion_follow/migrations.py
import hashlib
import re
from dataclasses import dataclass
from importlib.resources import files

from psycopg_pool import AsyncConnectionPool


MIGRATION_NAME = re.compile(r"^(?P<number>[0-9]{4})_[a-z0-9_]+\.sql$")


@dataclass(frozen=True, slots=True)
class _Migration:
    version: str
    sql: str
    sha256: str


def _packaged_migrations() -> tuple[_Migration, ...]:
    directory = files("champion_follow").joinpath("sql")
    resources = sorted(
        (item for item in directory.iterdir() if item.name.endswith(".sql")),
        key=lambda item: item.name,
    )
    if not resources or resources[0].name != "0001_core.sql":
        raise RuntimeError("core migration is missing")
    migrations = []
    for expected_number, item in enumerate(resources, 1):
        match = MIGRATION_NAME.fullmatch(item.name)
        if match is None or int(match.group("number")) != expected_number:
            raise RuntimeError("migration versions must be contiguous and monotonic")
        payload = item.read_bytes()
        try:
            sql = payload.decode("utf-8")
        except UnicodeDecodeError:
            raise RuntimeError("migration is not valid UTF-8") from None
        migrations.append(_Migration(
            version=item.name.removesuffix(".sql"),
            sql=sql,
            sha256=hashlib.sha256(payload).hexdigest(),
        ))
    return tuple(migrations)


async def migrate(pool: AsyncConnectionPool) -> None:
    migrations = _packaged_migrations()
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock(7260727)")
            await connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version VARCHAR(64) PRIMARY KEY,sha256 CHAR(64) NOT NULL,"
                "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            result = await connection.execute(
                "SELECT version,sha256 FROM schema_migrations ORDER BY version"
            )
            applied_rows = tuple(await result.fetchall())
            applied = {row["version"]: row["sha256"] for row in applied_rows}
            packaged_versions = tuple(migration.version for migration in migrations)
            applied_versions = tuple(row["version"] for row in applied_rows)
            if set(applied_versions) - set(packaged_versions):
                raise RuntimeError("applied migration resource is missing")
            if applied_versions != packaged_versions[:len(applied_versions)]:
                raise RuntimeError("applied migrations are not a strict prefix")
            for migration in migrations:
                digest = applied.get(migration.version)
                if digest is not None:
                    if digest != migration.sha256:
                        raise RuntimeError("applied migration digest changed")
                    continue
                await connection.execute(migration.sql)
                await connection.execute(
                    "INSERT INTO schema_migrations(version,sha256) VALUES (%s,%s)",
                    (migration.version, migration.sha256),
                )
```

- [ ] **Step 5: 使用安全的随机 schema fixture 和完整列允许清单**

```python
# tests/conftest.py
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from champion_follow.db import create_pool
from champion_follow.migrations import migrate


EXPECTED_COLUMNS = {
    "schema_migrations": "version sha256 applied_at",
    "identity_namespaces": "id version mode created_at",
    "anonymous_actors": "namespace_id actor_key display_no first_seen_at",
    "collectors": (
        "id namespace_id wire_id label parser_version bearer_sha256 ack_sequence "
        "ack_event_key history_anchor_event_key created_at"
    ),
    "collector_heartbeats": (
        "collector_id issue phase countdown_ms observed_at_ms last_journal_sequence "
        "capture_healthy received_at"
    ),
    "import_batches": (
        "id namespace_id partition source_label source_sha256 parser_version row_count imported_at"
    ),
    "game_issues": "issue issue_no",
    "issue_evaluations": (
        "namespace_id issue closed_ms result_ms result_digits integrity_status integrity_reasons "
        "integrity_version processed_at"
    ),
    "source_events": (
        "id namespace_id partition collector_id import_batch_id stream_sequence event_key "
        "payload_sha256 actor_key issue kind history_anchor_event_key source_ms received_at "
        "position direction amount_fen result_digits gap_reason reported_complete reported_reasons "
        "parser_version source_label"
    ),
    "collector_event_receipts": (
        "namespace_id collector_id stream_sequence event_key payload_sha256 wire_sha256 received_at"
    ),
    "capture_gaps": (
        "id collector_id from_sequence to_sequence affected_issue reason opened_at recovered_at"
    ),
    "prediction_samples": (
        "id namespace_id actor_key issue market direction signal_source_ms lead_ms outcome "
        "unit_profit_micros created_at"
    ),
    "actor_profiles": (
        "namespace_id actor_key scope sample_count wins losses pushes recent_outcomes raw_win_rate "
        "all_wilson_lower recent_wilson_lower conservative_win_rate unit_return "
        "conservative_unit_return blind_count blind_wins blind_losses blind_profit_micros "
        "blind_peak_micros blind_max_drawdown_micros level first_seen_at last_seen_at "
        "statistics_version updated_through_issue"
    ),
    "ranking_snapshots": (
        "id namespace_id issue scope frozen_at statistics_version manifest_sha256"
    ),
    "ranking_entries": (
        "namespace_id snapshot_id actor_key rank sample_count wins losses pushes raw_win_rate "
        "all_wilson_lower recent_wilson_lower conservative_win_rate unit_return "
        "conservative_unit_return blind_count blind_profit_micros blind_max_drawdown_micros level"
    ),
    "asof_candidates": (
        "id namespace_id snapshot_id issue market actor_key direction signal_source_ms lead_ms "
        "prior_lead_times_ms profile_level profile_sample_count profile_wins profile_losses "
        "profile_raw_win_rate profile_conservative_win_rate profile_conservative_unit_return "
        "base_rank statistics_version frozen_at outcome unit_profit_micros settled_at"
    ),
    "processing_state": "namespace_id last_issue_no last_issue updated_at",
    "threshold_previews": (
        "id namespace_id request_sha256 safe_lead_ms request_config as_of "
        "watermark_snapshot_id watermark_scope generated_at"
    ),
    "threshold_preview_windows": (
        "preview_id window_days frozen_signal_count executable_signal_count win_count loss_count "
        "unit_profit_micros raw_win_rate conservative_win_rate window_start window_end"
    ),
}
EXPECTED_COLUMNS = {
    table: frozenset(columns.split()) for table, columns in EXPECTED_COLUMNS.items()
}
EXPECTED_TABLES = set(EXPECTED_COLUMNS)


class _RedactedDatabaseUrl(str):
    def __repr__(self):
        return "<redacted test database URL>"


@pytest.fixture(scope="session")
def base_test_database_url():
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.fail("TEST_DATABASE_URL is required for integration tests")
    parameters = conninfo_to_dict(value)
    database = parameters.get("dbname", "")
    if not database.endswith("_test") or parameters.get("options"):
        pytest.fail("TEST_DATABASE_URL must name a dedicated *_test database without options")
    return _RedactedDatabaseUrl(value)


@pytest_asyncio.fixture(scope="session")
async def database_pool(base_test_database_url):
    pool = create_pool(base_test_database_url)
    await pool.open(wait=True)
    try:
        async with pool.connection() as connection:
            row = await (
                await connection.execute("SELECT current_database() AS database")
            ).fetchone()
            if not row["database"].endswith("_test"):
                pytest.fail("connected database is not a dedicated *_test database")
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def test_database_url(database_pool, base_test_database_url):
    schema = f"test_{uuid4().hex}"
    async with database_pool.connection() as connection:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    conninfo = _RedactedDatabaseUrl(
        make_conninfo(base_test_database_url, options=f"-csearch_path={schema}")
    )
    try:
        yield conninfo
    finally:
        async with database_pool.connection() as connection:
            await connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


@pytest_asyncio.fixture
async def raw_pool(test_database_url):
    pool = create_pool(test_database_url)
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def isolated_pool(raw_pool):
    yield raw_pool


@pytest_asyncio.fixture
async def pool(raw_pool):
    await migrate(raw_pool)
    async with raw_pool.connection() as connection:
        result = await connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=current_schema()"
        )
        actual = {row["table_name"] for row in await result.fetchall()}
        result = await connection.execute(
            "SELECT table_name,column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema()"
        )
        actual_columns = {table: set() for table in EXPECTED_TABLES}
        for row in await result.fetchall():
            actual_columns.setdefault(row["table_name"], set()).add(row["column_name"])
    assert actual == EXPECTED_TABLES, "migration table set and fixture authority diverged"
    assert actual_columns == EXPECTED_COLUMNS, "migration columns and privacy authority diverged"
    yield raw_pool
```

- [ ] **Step 6: 运行迁移、健康检查及全部当前应用回归**

```bash
cd /Users/a123/Documents/极速/apps/champion_follow_platform
PYTHONPATH=src TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q \
  tests/integration/test_migrations.py tests/integration/test_health.py
PYTHONPATH=src TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q
```

Expected: 全部 PASS；fixture 的表/列允许清单与迁移完全一致，没有公共 schema `TRUNCATE`，
并发、失败和取消均不产生部分迁移。

- [ ] **Step 7: 提交并冻结核心模型**

```bash
cd /Users/a123/Documents/极速

git add -- apps/champion_follow_platform/src/champion_follow/sql \
  apps/champion_follow_platform/src/champion_follow/migrations.py \
  apps/champion_follow_platform/tests/conftest.py \
  apps/champion_follow_platform/tests/integration/test_health.py \
  apps/champion_follow_platform/tests/integration/test_migrations.py \
  docs/superpowers/plans/2026-07-27-champion-follow-01-core-server.md \
  docs/superpowers/plans/2026-07-27-champion-follow-03-auth-admin.md
git commit -m "fix: harden champion follow postgres authority"
```

After this commit, `0001_core.sql` is immutable: its recorded SHA-256 is the authority. Every later task
changes only code/tests；任何新 schema 都必须使用下一个连续编号迁移，不能改写 `0001_core.sql`。

### Task 4: 实现连续 ACK、事件幂等和 FastAPI 入口

**Files:**
- Modify: `apps/champion_follow_platform/src/champion_follow/contracts/events.py`
- Modify: `apps/champion_follow_platform/tests/unit/test_event_contract.py`
- Create: `apps/champion_follow_platform/src/champion_follow/repositories/__init__.py`
- Create: `apps/champion_follow_platform/src/champion_follow/repositories/ingestion.py`
- Create: `apps/champion_follow_platform/src/champion_follow/services/__init__.py`
- Create: `apps/champion_follow_platform/src/champion_follow/services/ingestion.py`
- Create: `apps/champion_follow_platform/src/champion_follow/api/ingestion.py`
- Modify: `apps/champion_follow_platform/src/champion_follow/main.py`
- Test: `apps/champion_follow_platform/tests/integration/test_ingestion_api.py`

- [ ] **Step 1: 写入连续批次、重放和隐私的失败测试夹具**

```python
# tests/integration/test_ingestion_api.py
from uuid import UUID

import httpx
import pytest

from champion_follow.config import Settings
from champion_follow.main import create_app
from champion_follow.migrations import migrate


NAMESPACE = UUID("10000000-0000-4000-8000-000000000001")
COLLECTOR = UUID("20000000-0000-4000-8000-000000000001")


async def seed_collector(pool):
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO identity_namespaces(id,version,mode) VALUES (%s,%s,'active')",
                (NAMESPACE, "actor-hmac-v1"),
            )
            await connection.execute(
                "INSERT INTO collectors(id,namespace_id,wire_id,label,parser_version,bearer_sha256) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    COLLECTOR, NAMESPACE, "collector-main-01", "primary-collector",
                    "ffc-normalizer-v2", "d" * 64,
                ),
            )


def event(sequence=1, event_key=None, amount_fen=100):
    return {
        "event_key": event_key or (f"{sequence:064x}:0"),
        "local_sequence": sequence,
        "actor_key": "a" * 64,
        "issue": "2607270001",
        "kind": "bet",
        "source_ms": 1_785_084_000_000 + sequence,
        "received_at": "2026-07-27T00:00:00Z",
        "play": "P1:大",
        "amount_fen": amount_fen,
        "result_digits": None,
        "parser_version": "ffc-normalizer-v2",
    }


def batch(start=1, end=1, events=None):
    return {
        "collector_id": str(COLLECTOR),
        "namespace_version": "actor-hmac-v1",
        "sequence_start": start,
        "sequence_end": end,
        "issue_hint": "2607270001",
        "events": events or [event(number) for number in range(start, end + 1)],
    }


@pytest.fixture
async def client(pool, test_database_url):
    await seed_collector(pool)
    app = create_app(Settings(database_url=test_database_url))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as value:
            yield value
```

- [ ] **Step 2: 追加 ACK、缺口和冲突测试**

Append to `tests/integration/test_ingestion_api.py`:

```python
@pytest.mark.integration
async def test_batch_commits_then_replay_returns_the_same_contiguous_ack(client, pool):
    first = await client.post("/v1/collector/batches", json=batch())
    replay = await client.post("/v1/collector/batches", json=batch())
    assert first.status_code == 200
    assert first.json()["highest_contiguous_sequence"] == 1
    assert first.json()["accepted_events"] == 1
    assert replay.json()["status"] == "replayed"
    assert replay.json()["highest_contiguous_sequence"] == 1
    async with pool.connection() as connection:
        rows = await connection.execute("SELECT count(*) AS n FROM source_events")
        actors = await connection.execute("SELECT count(*) AS n FROM anonymous_actors")
        assert (await rows.fetchone())["n"] == 1
        assert (await actors.fetchone())["n"] == 1


@pytest.mark.integration
async def test_future_batch_records_gap_but_does_not_ack_or_store_it(client, pool):
    assert (await client.post("/v1/collector/batches", json=batch())).status_code == 200
    response = await client.post("/v1/collector/batches", json=batch(3, 3))
    assert response.status_code == 409
    assert response.json() == {
        "detail": {"code": "sequence_gap", "highest_contiguous_sequence": 1}
    }
    async with pool.connection() as connection:
        rows = await connection.execute("SELECT from_sequence,to_sequence FROM capture_gaps")
        assert dict(await rows.fetchone()) == {"from_sequence": 2, "to_sequence": 2}
        count = await connection.execute("SELECT count(*) AS n FROM source_events")
        assert (await count.fetchone())["n"] == 1


@pytest.mark.integration
async def test_same_event_key_with_changed_semantics_is_rejected(client):
    key = "f" * 64 + ":0"
    assert (await client.post(
        "/v1/collector/batches", json=batch(events=[event(event_key=key)]),
    )).status_code == 200
    changed = batch(2, 2, events=[event(2, event_key=key, amount_fen=200)])
    response = await client.post("/v1/collector/batches", json=changed)
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "event_conflict"}}


@pytest.mark.integration
async def test_raw_uid_is_rejected_before_service_code(client):
    value = batch()
    value["events"][0]["uid"] = "PRIVATE"
    response = await client.post("/v1/collector/batches", json=value)
    assert response.status_code == 422
    assert "PRIVATE" not in response.text
    assert all("input" not in item for item in response.json()["detail"])
```

Also add RED cases for sequence-independent digests (changing only `local_sequence`/`received_at` is equal), a
history canonical row followed by a new journal sequence that still creates a durable receipt and ACK, exact
per-sequence replay verification, partial-overlap rejection when `sequence_start <= ack < sequence_end`, nested
`3→4→2` gap observations and recovery only through `to_sequence <= ack`, and full transaction rollback on a later
same-batch conflict. The repository order is fixed: replay (`end <= ack`) → partial-overlap conflict → future-gap
marker (`start > ack+1`) → exact contiguous insertion. Close/result/status/gap may advance only ACK; current money
events advance the history anchor by `(source_ms,event_key)` and older money events never move it backwards.

- [ ] **Step 3: 运行 API 测试并确认 RED**

Run:

```bash
cd /Users/a123/Documents/极速/apps/champion_follow_platform
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q tests/integration/test_ingestion_api.py
```

Expected: requests return `404 Not Found` because `/v1/collector/batches` is not registered.

- [ ] **Step 4: 使事件语义摘要排除传输序号和重收时间**

Replace only `canonical_event_sha256()` in `contracts/events.py` with:

```python
def canonical_event_sha256(event: NormalizedEvent) -> str:
    payload = json.dumps(
        event.model_dump(
            mode="json",
            exclude={"local_sequence", "received_at"},
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

This lets history/realtime overlap use a new local sequence without changing the authoritative event identity, while still treating changed actor/play/amount/result as a conflict.

Update the locked vector in `tests/unit/test_event_contract.py` to
`3516f50c99f9d2798e8b2733aaf6c492ee9ec5d97f894ceab44c00a75b8c275f`, and
assert that changing only `local_sequence` and `received_at` leaves this digest unchanged.

- [ ] **Step 5: 实现 canonical event＋每序号 receipt 的连续 ACK 仓库**

先增加 RED 用例，覆盖“历史导入已有同一语义事件，实时 journal 仍能以新序号收到持久 receipt
并连续 ACK”、同序号相同收据重放、同序号 event/payload 冲突、3→4→2 形成嵌套缺口以及
补齐 2/3/4 后所有对应缺口观察才恢复。

`IngestionRepository` 的单批事务必须按以下顺序执行：

1. `SELECT ... FOR UPDATE` 锁定 collector，校验 namespace/parser 以及批次边界；
2. 为 `issue_hint` 和每个 event 先幂等创建共享 `game_issues`，再幂等创建该 collector
   namespace 下的 `issue_evaluations`；
3. 若 `sequence_start != ack+1`，只写入一条允许重叠的 `capture_gaps` 观察并返回内部
   `GapDetected(ack)`；**不得在这段事务内抛 `SequenceGap`**；
4. 对每个连续 event 先计算 sequence-independent `payload_sha256`。按
   `(namespace_id,event_key)` 查/插 `source_events`：不存在则写 canonical event；已存在则摘要必须
   相同。历史导入行与实时行重叠时保留历史 canonical lineage，不改写 collector/sequence；
5. 无论 canonical event 是新写还是历史已有，都必须按 `(collector_id,stream_sequence)` 写
   `collector_event_receipts(namespace_id,event_key,payload_sha256,wire_sha256=NULL)`。已存在 receipt
   必须逐字段相同，否则返回安全的 sequence/event conflict；
6. 仅当本批成功持久 `current` bet/cancel 时，在同一事务内把
   `collectors.history_anchor_event_key` 推进为该 namespace **全局**按 `(source_ms,event_key)`
   排序的最新 `partition='current' AND kind IN ('bet','cancel')` 事件，不得倒退。
   没有合格钱类事件则保持 NULL/原值；close/result/CAPTURE_GAP/ISSUE_STATUS
   只能推进 ACK。该键是历史回填游标，与 `ack_event_key` 严格独立；
7. receipt 全部持久化后更新 `collectors.ack_sequence/ack_event_key`。延迟 FK 在事务提交时证明 ACK
   精确指向该 collector 的最后一条 receipt；只恢复 `to_sequence <= 新 ACK` 的缺口观察；
8. 事务正常提交后，service 才把 `GapDetected` 映射为 `SequenceGap`/HTTP 409。这样缺口不会因异常
   回滚。事件或收据冲突仍在事务内抛错并整体回滚。

批次 `sequence_end <= ack` 不能只看 ACK 就返回 replay；必须逐序号读取
`collector_event_receipts` 并核对 event key/payload（Task 11 还要核对 wire digest），一致才返回
`replayed`。内部 `/batches` 此时不接收 wire digest，所以 receipt 的 `wire_sha256` 为 NULL。

- [ ] **Step 6: 实现用例层和只返回安全代码的路由**

```python
# src/champion_follow/services/__init__.py
```

```python
# src/champion_follow/services/ingestion.py
from champion_follow.contracts.events import CollectorBatch


class IngestionService:
    def __init__(self, repository):
        self.repository = repository

    async def accept(self, batch: CollectorBatch):
        return await self.repository.ingest(batch)
```

```python
# src/champion_follow/api/ingestion.py
from fastapi import APIRouter, HTTPException, Request

from champion_follow.contracts.events import BatchAck, CollectorBatch
from champion_follow.repositories.ingestion import (
    CollectorContractError,
    EventConflict,
    SequenceGap,
)


router = APIRouter(prefix="/v1/collector", tags=["collector"])


@router.post("/batches", response_model=BatchAck)
async def ingest_batch(batch: CollectorBatch, request: Request) -> BatchAck:
    try:
        return await request.app.state.ingestion.accept(batch)
    except SequenceGap as error:
        raise HTTPException(409, detail={
            "code": "sequence_gap",
            "highest_contiguous_sequence": error.highest_contiguous_sequence,
        }) from None
    except EventConflict:
        raise HTTPException(409, detail={"code": "event_conflict"}) from None
    except CollectorContractError as error:
        raise HTTPException(409, detail={"code": str(error)}) from None
```

- [ ] **Step 7: 把采集服务组装进 FastAPI 生命周期**

Update `main.py` imports and `lifespan`/router registration to this complete form:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api.health import router as health_router
from .api.ingestion import router as ingestion_router
from .config import Settings
from .db import open_pool
from .repositories.ingestion import IngestionRepository
from .services.ingestion import IngestionService


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved = settings or Settings()
        async with open_pool(resolved.database_url.get_secret_value()) as pool:
            app.state.db = pool
            app.state.ingestion = IngestionService(IngestionRepository(pool))
            yield

    app = FastAPI(title="Champion Follow Core", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def safe_request_validation_error(_request, error):
        detail = [
            {key: item[key] for key in ("type", "loc", "msg") if key in item}
            for item in error.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": detail})

    app.include_router(health_router)
    app.include_router(ingestion_router)
    return app


app = create_app()
```

- [ ] **Step 8: 运行合同、迁移和采集 API 测试**

Run:

```bash
cd /Users/a123/Documents/极速/apps/champion_follow_platform
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q \
  tests/unit/test_event_contract.py \
  tests/integration/test_migrations.py \
  tests/integration/test_ingestion_api.py
```

Expected: all tests pass; the gap request returns 409 without storing sequence 3, and no response contains the synthetic private input value.

- [ ] **Step 9: 提交幂等采集入口**

```bash
cd /Users/a123/Documents/极速
git add -- apps/champion_follow_platform/src/champion_follow apps/champion_follow_platform/tests
git commit -m "feat: ingest contiguous anonymous event batches"
```

### Task 5: 从冻结的旧 SQLite 日志干净导入

**Files:**
- Create: `apps/champion_follow_platform/src/champion_follow/services/history_import.py`
- Create: `apps/champion_follow_platform/src/champion_follow/cli.py`
- Modify: `apps/champion_follow_platform/pyproject.toml`
- Test: `apps/champion_follow_platform/tests/integration/test_history_import.py`
- Test: `apps/champion_follow_platform/tests/integration/test_collector_registration.py`

- [ ] **Step 1: 写旧库转换、分区和幂等的失败测试夹具**

```python
# tests/integration/test_history_import.py
import json
import sqlite3
from pathlib import Path
from uuid import UUID

import pytest

from champion_follow.services.history_import import HistoryImportError, import_legacy


NAMESPACE = UUID("10000000-0000-4000-8000-000000000001")
ACTOR = "a" * 64


def make_legacy(path: Path, *, normalizer="7", with_wal=False):
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
        "CREATE TABLE source_events("
        "event_key TEXT PRIMARY KEY,actor_key TEXT,source_ms INTEGER NOT NULL,"
        "kind TEXT NOT NULL,explicit_issue TEXT,assigned_issue TEXT,play TEXT,"
        "amount_text TEXT,result_json TEXT,assignment TEXT NOT NULL,id_quality TEXT NOT NULL,"
        "observed_at INTEGER NOT NULL);"
    )
    connection.execute("INSERT INTO meta VALUES ('public_normalizer_version',?)", (normalizer,))
    rows = [
        ("a" * 64 + ":0", ACTOR, 1000, "bet", "2607270001", "2607270001", "P1:大", "2.50", None, "frozen", "stable", 1000),
        ("b" * 64 + ":0", None, 1100, "close", None, "2607270001", None, None, None, "frozen", "stable", 1100),
        ("c" * 64 + ":0", None, 1200, "result", "2607270001", "2607270001", None, None, "[5, 2, 1, 0, 9]", "frozen", "stable", 1200),
    ]
    connection.executemany("INSERT INTO source_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    connection.commit()
    connection.close()
    if with_wal:
        Path(str(path) + "-wal").write_bytes(b"not-a-frozen-snapshot")


async def seed_active(pool):
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO identity_namespaces(id,version,mode) VALUES (%s,%s,'active')",
                (NAMESPACE, "actor-hmac-v1"),
            )


@pytest.mark.integration
async def test_current_namespace_import_is_idempotent_and_contains_no_raw_fields(pool, tmp_path):
    await seed_active(pool)
    source = tmp_path / "frozen.sqlite3"
    make_legacy(source)
    first = await import_legacy(
        pool, source, "ffc-shadow-20260722", "actor-hmac-v1", "current", "7"
    )
    second = await import_legacy(
        pool, source, "ffc-shadow-20260722", "actor-hmac-v1", "current", "7"
    )
    assert first.inserted == 3
    assert second.inserted == 0
    assert second.status == "already_imported"
    async with pool.connection() as connection:
        result = await connection.execute(
            "SELECT count(*) AS n FROM source_events WHERE partition='current'"
        )
        assert (await result.fetchone())["n"] == 3
        raw = await connection.execute("SELECT actor_key,amount_fen FROM source_events")
        assert dict(await raw.fetchone()) == {"actor_key": ACTOR, "amount_fen": 250}


@pytest.mark.integration
async def test_mismatched_namespace_can_only_enter_baseline_partition(pool, tmp_path):
    await seed_active(pool)
    source = tmp_path / "frozen.sqlite3"
    make_legacy(source)
    with pytest.raises(HistoryImportError, match="current namespace"):
        await import_legacy(pool, source, "old", "actor-hmac-v0", "current", "7")
    result = await import_legacy(
        pool, source, "old", "actor-hmac-v0", "baseline", "7"
    )
    assert result.partition == "baseline"
    async with pool.connection() as connection:
        rows = await connection.execute(
            "SELECT count(*) AS n FROM source_events WHERE partition='baseline'"
        )
        assert (await rows.fetchone())["n"] == 3


@pytest.mark.integration
async def test_import_rejects_a_database_with_an_unfrozen_wal(tmp_path, pool):
    await seed_active(pool)
    source = tmp_path / "live.sqlite3"
    make_legacy(source, with_wal=True)
    with pytest.raises(HistoryImportError, match="frozen"):
        await import_legacy(pool, source, "live", "actor-hmac-v1", "current", "7")
```

Create `tests/integration/test_collector_registration.py`:

```python
import hashlib
import json
import stat
from uuid import UUID

import pytest

from champion_follow.cli import _register_collector
from champion_follow.config import Settings


NAMESPACE = UUID("10000000-0000-4000-8000-000000000001")


@pytest.mark.integration
async def test_collector_registration_keeps_only_digest_and_uses_one_time_0600_handoff(
    pool, test_database_url, tmp_path
):
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO identity_namespaces(id,version,mode) VALUES (%s,%s,'active')",
                (NAMESPACE, "actor-hmac-v1"),
            )
    handoff = tmp_path / "collector-credential.json"
    result = await _register_collector(
        Settings(database_url=test_database_url),
        label="primary-collector",
        wire_id="collector-main-01",
        namespace_version="actor-hmac-v1",
        parser_version="btcffc-1",
        handoff_path=handoff,
    )
    assert result == {
        "status": "created",
        "label": "primary-collector",
        "collector_id": "collector-main-01",
        "credential_handoff": str(handoff),
    }
    assert stat.S_IMODE(handoff.stat().st_mode) == 0o600
    bundle = json.loads(handoff.read_text(encoding="utf-8"))
    assert set(bundle) == {"format", "collector_id", "bearer"}
    assert bundle["format"] == "champion-collector-credential-v1"
    assert bundle["collector_id"] == "collector-main-01"
    assert len(bundle["bearer"]) >= 64
    async with pool.connection() as connection:
        stored = await connection.execute(
            "SELECT wire_id,bearer_sha256 FROM collectors WHERE wire_id=%s",
            ("collector-main-01",),
        )
        row = await stored.fetchone()
    assert row["wire_id"] == "collector-main-01"
    assert row["bearer_sha256"] == hashlib.sha256(bundle["bearer"].encode()).hexdigest()
    with pytest.raises(FileExistsError):
        await _register_collector(
            Settings(database_url=test_database_url),
            label="second-collector",
            wire_id="collector-main-02",
            namespace_version="actor-hmac-v1",
            parser_version="btcffc-1",
            handoff_path=handoff,
        )
```

除上述用例外，先增加 RED：导入后每个 `(namespace,issue)` 必须有 `issue_evaluations`；
不同冻结批次若用同一 event key 携带不同语义摘要必须失败；
`test_register_after_current_import_binds_latest_money_anchor` 证明先导入后注册时会绑定该 active
namespace 中按 `(source_ms,event_key)` 排序最新的 `current` bet/cancel；
`test_register_before_current_import_refreshes_history_anchor` 证明先注册后导入也会在同一导入事务中
刷新该 namespace 的所有 collector。再导入更早的钱类事件不得使锚点倒退；
没有历史钱类事件时保持 NULL，close/result/baseline 事件不得成为锚点。

- [ ] **Step 2: 运行导入与凭据交接测试并确认 RED**

Run:

```bash
cd /Users/a123/Documents/极速/apps/champion_follow_platform
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q \
  tests/integration/test_history_import.py \
  tests/integration/test_collector_registration.py
```

Expected: collection fails because `champion_follow.services.history_import` and the CLI registration helper are not defined.

- [ ] **Step 3: 完成 SQLite 只读解析与事件建构**

```python
# src/champion_follow/services/history_import.py
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from uuid import UUID, uuid4

from champion_follow.contracts.events import EventKind, NormalizedEvent, canonical_event_sha256
from champion_follow.domain.markets import parse_play


class HistoryImportError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImportResult:
    status: str
    inserted: int
    partition: str
    row_count: int


def _money_fen(value: str) -> int:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise HistoryImportError("invalid legacy amount") from None
    if amount <= 0 or amount != Decimal(str(value)):
        raise HistoryImportError("legacy amount is not exact fen")
    return int(amount * 100)


def _observed_at(value: int) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def _event_from_row(row, sequence: int, parser_version: str) -> NormalizedEvent | None:
    kind = str(row["kind"])
    if kind == "player_evidence":
        return None
    mapped = {
        "bet": EventKind.BET,
        "cancel_candidate": EventKind.CANCEL,
        "cancel_notice": EventKind.UNATTRIBUTED_CANCEL,
        "close": EventKind.CLOSE,
        "result": EventKind.RESULT,
    }.get(kind)
    if mapped is None:
        raise HistoryImportError("unsupported legacy event kind")
    issue = row["assigned_issue"] or row["explicit_issue"]
    if not issue:
        raise HistoryImportError("legacy event has no issue")
    result = None
    if mapped is EventKind.RESULT:
        try:
            result = tuple(int(value) for value in json.loads(row["result_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            raise HistoryImportError("invalid legacy result") from None
    play = row["play"]
    if mapped in {EventKind.BET, EventKind.CANCEL}:
        if not row["actor_key"] or not play:
            raise HistoryImportError("money event has no anonymous actor")
        parsed = parse_play(play)
        if not re.fullmatch(r"[0-9a-f]{64}", str(row["actor_key"])):
            raise HistoryImportError("legacy actor key is not a digest")
    else:
        parsed = None
    return NormalizedEvent(
        event_key=str(row["event_key"]),
        local_sequence=sequence,
        actor_key=str(row["actor_key"]) if row["actor_key"] else None,
        issue=str(issue),
        kind=mapped,
        source_ms=int(row["source_ms"]),
        received_at=_observed_at(row["observed_at"]),
        play=parsed.play if parsed else None,
        amount_fen=_money_fen(row["amount_text"]) if row["amount_text"] else None,
        result_digits=result,
        parser_version=parser_version,
    )


def read_frozen_legacy(path: Path, parser_version: str) -> tuple[str, tuple[NormalizedEvent, ...]]:
    path = Path(path)
    if not path.is_file() or Path(str(path) + "-wal").exists() or Path(str(path) + "-shm").exists():
        raise HistoryImportError("legacy database must be a frozen sqlite file")
    source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    uri = f"file:{path.resolve()}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        meta = connection.execute(
            "SELECT value FROM meta WHERE key='public_normalizer_version'"
        ).fetchone()
        if meta is None or str(meta[0]) != parser_version:
            raise HistoryImportError("legacy parser version mismatch")
        rows = connection.execute(
            "SELECT event_key,actor_key,source_ms,kind,explicit_issue,assigned_issue,"
            "play,amount_text,result_json,assignment,id_quality,observed_at "
            "FROM source_events WHERE assignment IN ('assigned','frozen') "
            "ORDER BY source_ms,event_key"
        ).fetchall()
        events = tuple(
            event for index, row in enumerate(rows, 1)
            if (event := _event_from_row(row, index, parser_version)) is not None
        )
        return source_sha256, events
    except sqlite3.Error as error:
        raise HistoryImportError("legacy sqlite read failed") from error
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass


async def import_legacy(
    pool,
    path: Path,
    source_label: str,
    namespace_version: str,
    partition: str,
    parser_version: str,
) -> ImportResult:
    if partition not in {"current", "baseline"}:
        raise HistoryImportError("invalid import partition")
    source_sha256, events = read_frozen_legacy(Path(path), parser_version)
    async with pool.connection() as connection:
        async with connection.transaction():
            active = await connection.execute(
                "SELECT id,version FROM identity_namespaces WHERE mode='active'"
            )
            active_row = await active.fetchone()
            if active_row is None:
                raise HistoryImportError("active namespace is not initialized")
            if (partition == "current") != (active_row["version"] == namespace_version):
                raise HistoryImportError("current namespace version does not match active namespace")
            namespace = await connection.execute(
                "SELECT id FROM identity_namespaces WHERE version=%s",
                (namespace_version,),
            )
            namespace_row = await namespace.fetchone()
            if namespace_row is None:
                namespace_id = uuid4()
                await connection.execute(
                    "INSERT INTO identity_namespaces(id,version,mode) VALUES (%s,%s,%s)",
                    (namespace_id, namespace_version, "active" if partition == "current" else "baseline"),
                )
            else:
                namespace_id = namespace_row["id"]
            existing = await connection.execute(
                "SELECT id FROM import_batches WHERE namespace_id=%s AND source_sha256=%s",
                (namespace_id, source_sha256),
            )
            already_imported = await existing.fetchone() is not None
            if already_imported:
                inserted = 0
            else:
                batch_id = uuid4()
                await connection.execute(
                    "INSERT INTO import_batches(id,namespace_id,partition,source_label,"
                    "source_sha256,parser_version,row_count) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (batch_id, namespace_id, partition, source_label, source_sha256,
                     parser_version, len(events)),
                )
                inserted = 0
            events_to_insert = () if already_imported else events
            for event in events_to_insert:
                await connection.execute(
                    "INSERT INTO game_issues(issue,issue_no) VALUES (%s,%s) "
                    "ON CONFLICT (issue) DO NOTHING",
                    (event.issue, int(event.issue)),
                )
                await connection.execute(
                    "INSERT INTO issue_evaluations(namespace_id,issue) VALUES (%s,%s) "
                    "ON CONFLICT (namespace_id,issue) DO NOTHING",
                    (namespace_id, event.issue),
                )
                if event.actor_key:
                    await connection.execute(
                        "INSERT INTO anonymous_actors(namespace_id,actor_key,first_seen_at) "
                        "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                        (namespace_id, event.actor_key, event.received_at),
                    )
                parsed = parse_play(event.play) if event.play else None
                result = await connection.execute(
                    "INSERT INTO source_events(namespace_id,partition,import_batch_id,"
                    "event_key,payload_sha256,actor_key,issue,kind,source_ms,received_at,"
                    "position,direction,amount_fen,result_digits,parser_version,source_label) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (namespace_id,event_key) DO NOTHING RETURNING id",
                    (
                        namespace_id, partition, batch_id, event.event_key,
                        canonical_event_sha256(event), event.actor_key, event.issue,
                        event.kind.value, event.source_ms, event.received_at,
                        parsed.position if parsed else None,
                        parsed.direction.value if parsed else None,
                        event.amount_fen,
                        list(event.result_digits) if event.result_digits else None,
                        event.parser_version, source_label,
                    ),
                )
                if await result.fetchone():
                    inserted += 1
                else:
                    existing_event = await connection.execute(
                        "SELECT payload_sha256 FROM source_events "
                        "WHERE namespace_id=%s AND event_key=%s",
                        (namespace_id, event.event_key),
                    )
                    if (await existing_event.fetchone())["payload_sha256"] != canonical_event_sha256(event):
                        raise HistoryImportError("legacy event conflicts with canonical history")
            if partition == "current":
                await connection.execute(
                    "WITH latest AS ("
                    "SELECT event_key FROM source_events "
                    "WHERE namespace_id=%s AND partition='current' AND kind IN ('bet','cancel') "
                    "ORDER BY source_ms DESC,event_key DESC LIMIT 1"
                    ") UPDATE collectors AS collector "
                    "SET history_anchor_event_key=latest.event_key FROM latest "
                    "WHERE collector.namespace_id=%s "
                    "AND collector.history_anchor_event_key IS DISTINCT FROM latest.event_key",
                    (namespace_id, namespace_id),
                )
            return ImportResult(
                "already_imported" if already_imported else "imported",
                inserted, partition, len(events),
            )
```

- [ ] **Step 4: 实现只读导入 CLI 与一次性采集凭据交接**

Create `src/champion_follow/cli.py` with no command that prints a database URL, an actor key, a source row, or a Bearer value:

```python
import argparse
import asyncio
import hashlib
import json
import os
import secrets
from pathlib import Path
from uuid import uuid4

from .config import Settings
from .db import create_pool
from .migrations import migrate
from .services.history_import import import_legacy


async def _initialize_namespace(settings, version):
    pool = create_pool(settings.database_url.get_secret_value())
    await pool.open(wait=True)
    try:
        await migrate(pool)
        namespace_id = uuid4()
        async with pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "INSERT INTO identity_namespaces(id,version,mode) VALUES (%s,%s,'active')",
                    (namespace_id, version),
                )
        return {"status": "created", "version": version}
    finally:
        await pool.close()


async def _migrate_only(settings):
    pool = create_pool(settings.database_url.get_secret_value())
    await pool.open(wait=True)
    try:
        await migrate(pool)
        return {"status": "migrated"}
    finally:
        await pool.close()


async def _register_collector(
    settings,
    label,
    wire_id,
    namespace_version,
    parser_version,
    handoff_path,
):
    bearer = secrets.token_urlsafe(48)
    bearer_sha256 = hashlib.sha256(bearer.encode("utf-8")).hexdigest()
    bundle = {
        "format": "champion-collector-credential-v1",
        "collector_id": wire_id,
        "bearer": bearer,
    }
    path = Path(handoff_path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    os.fchmod(descriptor, 0o600)
    pool = create_pool(settings.database_url.get_secret_value())
    committed = False
    try:
        await pool.open(wait=True)
        await migrate(pool)
        async with pool.connection() as connection:
            async with connection.transaction():
                row = await connection.execute(
                    "SELECT id FROM identity_namespaces WHERE version=%s AND mode='active'",
                    (namespace_version,),
                )
                namespace = await row.fetchone()
                if namespace is None:
                    raise ValueError("namespace_not_found")
                anchor_result = await connection.execute(
                    "SELECT event_key AS history_anchor_event_key FROM source_events "
                    "WHERE namespace_id=%s AND partition='current' "
                    "AND kind IN ('bet','cancel') "
                    "ORDER BY source_ms DESC,event_key DESC LIMIT 1",
                    (namespace["id"],),
                )
                anchor = await anchor_result.fetchone()
                await connection.execute(
                    "INSERT INTO collectors(id,namespace_id,wire_id,label,parser_version,"
                    "bearer_sha256,history_anchor_event_key) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        uuid4(), namespace["id"], wire_id, label, parser_version,
                        bearer_sha256, anchor["history_anchor_event_key"] if anchor else None,
                    ),
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    descriptor = -1
                    stream.write(json.dumps(bundle, ensure_ascii=False, sort_keys=True) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
        committed = True
        return {
            "status": "created",
            "label": label,
            "collector_id": wire_id,
            "credential_handoff": str(path),
        }
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        await pool.close()
        if not committed:
            path.unlink(missing_ok=True)


async def _import(settings, args):
    pool = create_pool(settings.database_url.get_secret_value())
    await pool.open(wait=True)
    try:
        await migrate(pool)
        result = await import_legacy(
            pool, args.source, args.source_label, args.namespace_version,
            args.partition, args.parser_version,
        )
        return {
            "status": result.status,
            "inserted": result.inserted,
            "partition": result.partition,
            "row_count": result.row_count,
        }
    finally:
        await pool.close()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="champion-follow")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    init = sub.add_parser("init-namespace")
    init.add_argument("--version", required=True)
    register = sub.add_parser("register-collector")
    register.add_argument("--label", required=True)
    register.add_argument("--collector-id", required=True)
    register.add_argument("--namespace-version", required=True)
    register.add_argument("--parser-version", required=True)
    register.add_argument("--credential-handoff", required=True)
    imported = sub.add_parser("import-legacy")
    imported.add_argument("--source", required=True)
    imported.add_argument("--source-label", required=True)
    imported.add_argument("--namespace-version", required=True)
    imported.add_argument("--partition", choices=("current", "baseline"), required=True)
    imported.add_argument("--parser-version", required=True)
    args = parser.parse_args(argv)
    settings = Settings()
    if args.command == "migrate":
        result = asyncio.run(_migrate_only(settings))
    elif args.command == "init-namespace":
        result = asyncio.run(_initialize_namespace(settings, args.version))
    elif args.command == "register-collector":
        result = asyncio.run(_register_collector(
            settings, args.label, args.collector_id, args.namespace_version,
            args.parser_version, args.credential_handoff,
        ))
    else:
        result = asyncio.run(_import(settings, args))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
```

After `src/champion_follow/cli.py` exists, add its console script to `pyproject.toml`:

```toml
[project.scripts]
champion-follow = "champion_follow.cli:main"
```

The handoff file is an installation-time bridge, not long-term storage: Plan 02 imports this exact three-field bundle into its OS-protected credential adapter and unlinks it immediately. `O_EXCL` prevents overwrite/reuse, the database transaction stores only `bearer_sha256`, and every exception removes an uncommitted handoff. Never pass the Bearer on argv, stdout, logs, screenshots, or evidence artifacts.

- [ ] **Step 5: 用真实测试大小的冻结库和一次性交接执行回归**

Run:

```bash
cd /Users/a123/Documents/极速/apps/champion_follow_platform
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q \
  tests/integration/test_history_import.py \
  tests/integration/test_collector_registration.py
```

Expected: `4 passed`;旧库导入保持幂等/分区边界，采集注册只持久化摘要，交接文件恰为 `0600`，返回对象不含 Bearer，重复交接路径被拒绝。

- [ ] **Step 6: 提交导入器**

```bash
cd /Users/a123/Documents/极速
git add -- apps/champion_follow_platform/src/champion_follow/services/history_import.py \
  apps/champion_follow_platform/src/champion_follow/cli.py \
  apps/champion_follow_platform/pyproject.toml \
  apps/champion_follow_platform/tests/integration/test_history_import.py \
  apps/champion_follow_platform/tests/integration/test_collector_registration.py
git commit -m "feat: import history and register collectors safely"
```

### Task 6: 证明期完整并合成用户期样

**Files:**
- Create: `apps/champion_follow_platform/src/champion_follow/domain/integrity.py`
- Create: `apps/champion_follow_platform/src/champion_follow/repositories/issues.py`
- Create: `apps/champion_follow_platform/src/champion_follow/services/issue_builder.py`
- Test: `apps/champion_follow_platform/tests/unit/test_integrity.py`
- Test: `apps/champion_follow_platform/tests/integration/test_issue_builder.py`

- [ ] **Step 1: 写净额撤单、对压和一期一样本的单元失败测试**

```python
# tests/unit/test_integrity.py
from dataclasses import replace

import pytest

from champion_follow.domain.integrity import IssueEvent, evaluate_issue


def ev(key, kind, *, actor="a" * 64, position=1, direction="大", amount=100, time=100):
    return IssueEvent(
        event_key=key, kind=kind, actor_key=actor, issue="2607270001",
        position=position, direction=direction, amount_fen=amount,
        source_ms=time, result_digits=None,
    )


def result(time=300):
    return IssueEvent(
        event_key="r", kind="result", actor_key=None, issue="2607270001",
        position=None, direction=None, amount_fen=None, source_ms=time,
        result_digits=(5, 2, 1, 0, 9),
    )


def close(time=250):
    return replace(result(time), event_key="c", kind="close", result_digits=None)


def test_identified_cancel_is_applied_before_testing_opposite_directions():
    evaluation = evaluate_issue(
        "2607270001",
        [ev("b1", "bet", direction="大", time=100), ev("x", "cancel", direction="大", time=150),
         ev("b2", "bet", direction="小", time=180), close(), result()],
        unresolved_gap=False,
    )
    assert evaluation.complete
    assert [(row.market, row.direction) for row in evaluation.predictions] == [("P1:size", "小")]
    assert evaluation.predictions[0].outcome == -1


def test_opposite_net_after_cancel_is_an_integrity_failure():
    evaluation = evaluate_issue(
        "2607270001",
        [ev("b1", "bet", direction="大"), ev("b2", "bet", direction="小"), close(), result()],
        unresolved_gap=False,
    )
    assert not evaluation.complete
    assert evaluation.predictions == ()
    assert "opposing_net" in evaluation.reasons


def test_unattributed_cancel_and_gap_exclude_the_whole_issue():
    evaluation = evaluate_issue(
        "2607270001",
        [ev("b1", "bet"), ev("u", "unattributed_cancel"), close(), result()],
        unresolved_gap=True,
    )
    assert not evaluation.complete
    assert set(evaluation.reasons) == {"unattributed_cancel", "capture_gap"}


def test_persisted_collector_gap_is_sticky_but_status_hints_do_not_override_server_proof():
    gap = replace(close(140), event_key="g", kind="capture_gap")
    status = replace(close(160), event_key="s", kind="issue_status")
    evaluation = evaluate_issue(
        "2607270001", [ev("b1", "bet"), gap, status, close(), result()], unresolved_gap=False,
    )
    assert not evaluation.complete
    assert evaluation.reasons == ("capture_gap",)


def test_same_direction_additions_become_one_prediction():
    evaluation = evaluate_issue(
        "2607270001",
        [ev("b1", "bet", time=100), ev("b2", "bet", amount=200, time=120), close(), result()],
        unresolved_gap=False,
    )
    assert len(evaluation.predictions) == 1
    assert evaluation.predictions[0].signal_source_ms == 120
```

- [ ] **Step 2: 运行单元测试并确认 RED**

```bash
cd /Users/a123/Documents/极速/apps/champion_follow_platform
.venv/bin/pytest -q tests/unit/test_integrity.py
```

Expected: collection fails because `champion_follow.domain.integrity` does not exist.

- [ ] **Step 3: 定义服务器证明所需的期投影和净额追踪**

`IssueEvent` 除钱类/开奖结果字段外，必须携带 `reported_complete: bool | None` 和
`reported_reasons: tuple[str,...] | None`。`evaluate_issue()` 仍按 `(source_ms,event_key)` 排序，
先应用可归属撤单，再检查过度撤单、撤单后净对压、唯一 close、唯一合法 result、result 不早于
close、持久 `capture_gap` 和未恢复 `capture_gaps`。

`ISSUE_STATUS` 只是采集端提示，不是服务器证明：

- 任意 `reported_complete=false` 都是粘性排除信号，至少加入 `reported_incomplete`，并合并其
  allowlisted reasons；后续 true 不得清除；
- `reported_complete=true` 只保留审计，不能补足 close/result、不能消除撤单/缺口，也不能直接
  把期标为 complete；
- 任意持久 `capture_gap` 事件永久排除该期；传输层缺口观察只有全部恢复后才不再增加
  `capture_gap`，但已持久 gap 仍粘性保留。

只有服务器所有条件同时成立才合成 prediction；同 actor/issue/market 的同向追加合为一个样本，
信号时刻取该市场最后一次钱类变化，金额不参与排名。

- [ ] **Step 4: 按 namespace 读取并持久化 `issue_evaluations`**

`IssueRepository.pending_issues()` 必须从 `issue_evaluations ie JOIN game_issues gi` 查询
`ie.namespace_id=%s AND ie.integrity_status='pending'`，再与该 namespace 的
`processing_state.last_issue_no` 比较；不能再读取全局 `game_issues.integrity_status`。
`load_issue_events()` 读取 `reported_complete/reported_reasons`，只取该 namespace 的
`partition='current'`；`has_unresolved_gap()` 通过 collector namespace 隔离。

`save_evaluation()` 在一个事务中更新且只更新
`issue_evaluations(namespace_id,issue)` 的 close/result/integrity 字段。complete 时幂等 upsert
`prediction_samples`；incomplete 时必须删除该 namespace/issue 可能由旧失败尝试留下的样本。
状态从 `processed` 回退或不同 integrity version 覆盖已经处理的期都必须失败关闭。所有 reason
保存前按安全标识去重排序。

- [ ] **Step 5: 写入 PostgreSQL 投影边界集成测试**

测试 seed 顺序固定为：`identity_namespaces` → `anonymous_actors/import_batches` →
`game_issues` → `issue_evaluations(namespace_id,issue)` → `source_events`。覆盖：

1. 同一期在 active/baseline namespace 可分别 complete/incomplete，互不覆盖；
2. false ISSUE_STATUS 后跟 true 仍排除，单独 true 不能替代服务器 close/result；
3. 未恢复缺口、持久 gap、无法归属撤单、过度撤单、净对压均产生零样本；
4. 完整期恰好生成一期一市场一样本；重复 rebuild 幂等；
5. 写入中途失败时 evaluation 与 samples 同事务回滚。

- [ ] **Step 6: 运行完整性单元与集成测试**

Run:

```bash
cd /Users/a123/Documents/极速/apps/champion_follow_platform
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q \
  tests/unit/test_integrity.py tests/integration/test_issue_builder.py
```

Expected: all tests PASS; the persisted sample has `outcome=1` for `P1:大` on digit 5, and any transport gap, persisted `CAPTURE_GAP`, or opposing-net test has zero samples.

- [ ] **Step 7: 提交期完整性与样本合成**

```bash
cd /Users/a123/Documents/极速
git add -- apps/champion_follow_platform/src/champion_follow/domain/integrity.py apps/champion_follow_platform/src/champion_follow/repositories/issues.py apps/champion_follow_platform/src/champion_follow/services/issue_builder.py apps/champion_follow_platform/tests/unit/test_integrity.py apps/champion_follow_platform/tests/integration/test_issue_builder.py
git commit -m "feat: reject incomplete issues and synthesize predictions"
```

### Task 7: 实现 Wilson 保守画像、固定单位收益和等级

**Files:**
- Create: `apps/champion_follow_platform/src/champion_follow/domain/statistics.py`
- Create: `apps/champion_follow_platform/src/champion_follow/domain/profiles.py`
- Test: `apps/champion_follow_platform/tests/unit/test_statistics.py`
- Test: `apps/champion_follow_platform/tests/unit/test_profiles.py`

- [ ] **Step 1: 写 Wilson、收益和盈亏平衡线失败测试**

```python
# tests/unit/test_statistics.py
from decimal import Decimal

from champion_follow.domain.statistics import (
    BREAK_EVEN_RATE,
    conservative_unit_return,
    fixed_unit_return,
    wilson_lower,
)


def test_wilson_lower_uses_the_frozen_one_sided_z_value():
    assert wilson_lower(20, 30) == Decimal("0.516595491454")
    assert wilson_lower(30, 30) == Decimal("0.917275691875")
    assert wilson_lower(0, 0) == Decimal("0")


def test_fixed_unit_return_and_break_even_are_exact_decimal_values():
    assert fixed_unit_return(51, 49) == Decimal("-0.0004")
    assert conservative_unit_return(Decimal("0.6")) == Decimal("0.176")
    assert BREAK_EVEN_RATE == Decimal("0.5102040816326530612244897959")


def test_fixed_return_uses_postgres_numeric_half_away_from_zero_rounding():
    assert fixed_unit_return(3, 8189) == Decimal("-0.999282226563")
```

- [ ] **Step 2: 写近 200、盲跟回撤和等级边界失败测试**

```python
# tests/unit/test_profiles.py
from decimal import Decimal

from champion_follow.domain.profiles import ProfileState, classify_level


def test_recent_window_is_capped_and_conservative_rate_uses_the_lower_window():
    state = ProfileState.empty()
    for _ in range(50):
        state = state.observe(1)
    for _ in range(90):
        state = state.observe(1)
    for _ in range(110):
        state = state.observe(-1)
    metrics = state.metrics()
    assert metrics.sample_count == 250
    assert len(state.recent_outcomes) == 200
    assert metrics.conservative_win_rate == metrics.recent_wilson_lower
    assert metrics.conservative_win_rate < metrics.all_wilson_lower


def test_blind_follow_tracks_equity_peak_and_max_drawdown_without_reset():
    state = ProfileState.empty()
    state = state.observe_blind(1).observe_blind(1).observe_blind(-1)
    assert state.blind_count == 3
    assert state.blind_profit_micros == 920000
    assert state.blind_max_drawdown_micros == 1000000
    assert state.blind_peak_micros == 1920000


def test_levels_are_global_and_require_both_sample_and_blind_profit_gates():
    assert classify_level(29, 100, 1) == "observed"
    assert classify_level(30, 0, 0) == "candidate"
    assert classify_level(200, 50, 1) == "formal"
    assert classify_level(200, 50, 0) == "candidate"
    assert classify_level(500, 200, 1) == "core"
    assert classify_level(500, 200, 0) == "candidate"
```

- [ ] **Step 3: 实现高精度 Wilson 和固定单位函数**

```python
# src/champion_follow/domain/statistics.py
from decimal import Decimal, ROUND_HALF_UP, localcontext


STATISTICS_VERSION = "statistics-v1-z16448536269514722-recent200"
Z = Decimal("1.6448536269514722")
ODDS = Decimal("1.96")
BREAK_EVEN_RATE = Decimal(1) / ODDS
MICROS = Decimal(1_000_000)


def _quantize(value: Decimal) -> Decimal:
    # PostgreSQL round(numeric, scale) rounds ties away from zero.
    return value.quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)


def wilson_lower(wins: int, decisive: int) -> Decimal:
    if decisive <= 0:
        return Decimal(0)
    with localcontext() as context:
        context.prec = 48
        n = Decimal(decisive)
        p = Decimal(wins) / n
        z2 = Z * Z
        variance = (p * (Decimal(1) - p) + z2 / (Decimal(4) * n)) / n
        lower = (
            p + z2 / (Decimal(2) * n) - Z * variance.sqrt()
        ) / (Decimal(1) + z2 / n)
        return _quantize(max(Decimal(0), min(Decimal(1), lower)))


def fixed_unit_return(wins: int, losses: int) -> Decimal:
    decisive = wins + losses
    if decisive <= 0:
        return Decimal(0)
    return _quantize((Decimal(wins) * Decimal("0.96") - Decimal(losses)) / decisive)


def conservative_unit_return(conservative_rate: Decimal) -> Decimal:
    return _quantize(ODDS * Decimal(conservative_rate) - Decimal(1))
```

```python
# src/champion_follow/domain/profiles.py
from dataclasses import dataclass, replace
from decimal import Decimal

from .statistics import (
    STATISTICS_VERSION,
    conservative_unit_return,
    fixed_unit_return,
    wilson_lower,
)


def classify_level(sample_count: int, blind_count: int, blind_profit_micros: int) -> str:
    profitable = blind_count > 0 and blind_profit_micros > 0
    if sample_count >= 500 and blind_count >= 200 and profitable:
        return "core"
    if sample_count >= 200 and blind_count >= 50 and profitable:
        return "formal"
    if sample_count >= 30:
        return "candidate"
    return "observed"


@dataclass(frozen=True)
class ProfileMetrics:
    sample_count: int
    wins: int
    losses: int
    pushes: int
    raw_win_rate: Decimal
    all_wilson_lower: Decimal
    recent_wilson_lower: Decimal
    conservative_win_rate: Decimal
    unit_return: Decimal
    conservative_unit_return: Decimal


@dataclass(frozen=True)
class ProfileState:
    sample_count: int
    wins: int
    losses: int
    pushes: int
    recent_outcomes: tuple[int, ...]
    blind_count: int
    blind_wins: int
    blind_losses: int
    blind_profit_micros: int
    blind_peak_micros: int
    blind_max_drawdown_micros: int

    @classmethod
    def empty(cls):
        return cls(0, 0, 0, 0, (), 0, 0, 0, 0, 0, 0)

    def observe(self, outcome: int):
        if outcome not in (-1, 0, 1):
            raise ValueError("outcome must be -1, 0 or 1")
        return replace(
            self,
            sample_count=self.sample_count + 1,
            wins=self.wins + (outcome == 1),
            losses=self.losses + (outcome == -1),
            pushes=self.pushes + (outcome == 0),
            recent_outcomes=(*self.recent_outcomes, outcome)[-200:],
        )

    def observe_blind(self, outcome: int):
        if outcome not in (-1, 0, 1):
            raise ValueError("outcome must be -1, 0 or 1")
        profit = {1: 960000, -1: -1000000, 0: 0}[outcome]
        equity = self.blind_profit_micros + profit
        peak = max(self.blind_peak_micros, equity)
        drawdown = peak - equity
        return replace(
            self,
            blind_count=self.blind_count + 1,
            blind_wins=self.blind_wins + (outcome == 1),
            blind_losses=self.blind_losses + (outcome == -1),
            blind_profit_micros=equity,
            blind_peak_micros=peak,
            blind_max_drawdown_micros=max(self.blind_max_drawdown_micros, drawdown),
        )

    def metrics(self) -> ProfileMetrics:
        decisive = self.wins + self.losses
        recent_wins = sum(value == 1 for value in self.recent_outcomes)
        recent_decisive = sum(value != 0 for value in self.recent_outcomes)
        all_lower = wilson_lower(self.wins, decisive)
        recent_lower = wilson_lower(recent_wins, recent_decisive)
        conservative = min(all_lower, recent_lower) if self.sample_count >= 50 else all_lower
        raw = Decimal(self.wins) / decisive if decisive else Decimal(0)
        return ProfileMetrics(
            self.sample_count, self.wins, self.losses, self.pushes,
            raw, all_lower, recent_lower, conservative,
            fixed_unit_return(self.wins, self.losses),
            conservative_unit_return(conservative),
        )

    @property
    def level(self) -> str:
        return classify_level(self.sample_count, self.blind_count, self.blind_profit_micros)
```

- [ ] **Step 4: 运行统计单元测试并确认 GREEN**

```bash
cd /Users/a123/Documents/极速/apps/champion_follow_platform
.venv/bin/pytest -q tests/unit/test_statistics.py tests/unit/test_profiles.py
```

Expected: all tests PASS；Wilson、fixed return 和 conservative return 均为 12 位 Decimal，
显式 `ROUND_HALF_UP` 与 PostgreSQL `round(numeric,12)` 的 tie-away-from-zero 语义一致；
250-sample profile 使用较低的 recent-200 下界。

- [ ] **Step 5: 检查新表字段约束并提交**

```bash
cd /Users/a123/Documents/极速/apps/champion_follow_platform
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q tests/integration/test_migrations.py
cd /Users/a123/Documents/极速
git add -- apps/champion_follow_platform/src/champion_follow/domain/statistics.py apps/champion_follow_platform/src/champion_follow/domain/profiles.py apps/champion_follow_platform/tests/unit/test_statistics.py apps/champion_follow_platform/tests/unit/test_profiles.py
git commit -m "feat: calculate conservative champion profiles"
```

Expected: migration test remains green and the commit contains no credential or raw identity field.

### Task 8: 按期冻结榜单与候选，再揭示结果更新画像

**Files:**
- Create: `apps/champion_follow_platform/src/champion_follow/repositories/profiles.py`
- Create: `apps/champion_follow_platform/src/champion_follow/repositories/snapshots.py`
- Create: `apps/champion_follow_platform/src/champion_follow/services/causal.py`
- Test: `apps/champion_follow_platform/tests/integration/test_causal_processing.py`

- [ ] **Step 1: 写因果顺序 RED 测试**

```python
@pytest.mark.integration
async def test_issue_snapshot_uses_only_profiles_before_that_issue(pool, seeded_complete_issues):
    service = CausalProcessor(pool, statistics_version="wilson-95-one-sided-v1")
    await service.process_ready(namespace_version="actor-hmac-v1")
    async with pool.connection() as connection:
        row = await (await connection.execute(
            "SELECT re.sample_count FROM ranking_entries re "
            "JOIN ranking_snapshots rs ON rs.id=re.snapshot_id "
            "WHERE rs.issue=%s AND rs.scope='P1:size' AND re.rank=1",
            ("2607270002",),
        )).fetchone()
        future_actor = await (await connection.execute(
            "SELECT count(*) AS n FROM ranking_entries re "
            "JOIN ranking_snapshots rs ON rs.id=re.snapshot_id "
            "WHERE rs.issue=%s AND re.actor_key=%s",
            ("2607270001", "f" * 64),
        )).fetchone()
    # Issue 2's pre-draw snapshot contains only the one sample settled at issue 1.
    assert row["sample_count"] == 1
    assert future_actor["n"] == 0
```

Add tests that processing the same range twice is idempotent, an incomplete issue advances no profile, an unattributed cancellation creates no candidate, and a crash after snapshot insert rolls the whole issue transaction back. `test_freeze_rankings_returns_every_scope_snapshot_id` requires a mapping whose keys are exactly `overall` plus all 15 markets. `test_multi_market_candidates_reference_their_exact_market_snapshots` freezes predictions from multiple markets and proves every candidate uses `snapshot_ids_by_scope[prediction.market]`; `test_candidate_cannot_reuse_another_market_snapshot` proves there is no overall/other-market fallback.

- [ ] **Step 2: 运行 RED**

```bash
cd /Users/a123/Documents/极速/apps/champion_follow_platform
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q tests/integration/test_causal_processing.py
```

Expected: collection FAIL because `CausalProcessor` and snapshot repositories do not exist.

- [ ] **Step 3: 实现画像行锁与确定性更新**

`repositories/profiles.py` must expose these exact operations:

```python
class ProfileRepository:
    async def load_for_update(self, connection, namespace_id, actor_key, market) -> ProfileState: ...
    async def save(self, connection, namespace_id, actor_key, market, state, metrics, level, issue): ...
    async def ranked_before(self, connection, namespace_id, market, issue_no): ...
```

`load_for_update()` 使用 `SELECT ... FOR UPDATE`；缺行返回 `ProfileState.empty()`。
每个 prediction 同时更新其 market scope 与 `overall` scope，但全局等级只由更新后的 overall state
调用 `classify_level()` 得出。`save()` 一次 upsert 写全量/近 200 Wilson、固定单位收益、盲跟
收益/回撤、统计版本和处理水位；overall 行受数据库等级门禁，market 行的 `level` 只是该 actor
全局等级副本。全局等级变化时同步该 actor 的所有 market 行。冻结市场榜时必须 JOIN overall 行
取等级，不能用市场局部 sample/blind 重新分类。Recent outcomes 以一维 SMALLINT 数组持久化，长度
严格为 `min(sample_count,200)`。

- [ ] **Step 4: 实现每期单事务因果处理器**

冻结时为 `overall` 和 15 个 market 分别创建 `ranking_snapshots`，
`freeze_rankings()` 必须返回键集合恰好是这 16 个 scope 的 `scope -> snapshot_id` 映射；同一期
16 个 snapshot 在同一因果事务、同一冻结边界中生成，任何半套快照都整期回滚。每个 market 的
`ranking_entries` 保存该 market 的冻结统计，但 `level` 从同一 actor 的冻结前 overall 画像复制。
`freeze_candidates()` 对每个 prediction 只能使用
`snapshot_ids_by_scope[prediction.market]`，不得回退到 overall 或复用其他 market 的 UUID；并从该精确
market 快照真实的 ranking entry 复制
`actor_key/base_rank/profile_level/profile_sample_count/profile_wins/profile_losses/`
`profile_raw_win_rate/profile_conservative_win_rate/profile_conservative_unit_return/`
`statistics_version/frozen_at`。数据库 FK 证明 actor/rank 属于该快照；仓库测试逐字段比较，禁止从
当前画像重算或伪造。`prior_lead_times_ms` 只含该 market 在本期之前的 lead times，数量不得超过
冻结 profile sample count。

```python
class CausalProcessor:
    async def process_one(self, namespace_id, issue: str) -> str:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                state = await self._lock_processing_state(connection, namespace_id)
                evaluation = await self.issue_builder.evaluate(connection, namespace_id, issue)
                if not evaluation.complete:
                    await self._record_excluded(connection, namespace_id, issue, evaluation.reasons)
                    return "excluded"
                snapshot_ids_by_scope = await self.snapshots.freeze_rankings(
                    connection, namespace_id, issue, state.last_issue_no,
                )
                candidates = await self.snapshots.freeze_candidates(
                    connection, snapshot_ids_by_scope, evaluation.predictions,
                )
                await self.snapshots.settle_candidates(
                    connection, candidates, evaluation.result_digits,
                )
                await self._apply_prediction_outcomes(
                    connection, namespace_id, evaluation.predictions,
                    evaluation.result_digits, candidate_keys={c.key for c in candidates},
                )
                await self._advance(connection, namespace_id, issue)
                return "processed"
```

The implementation order is invariant: lock cursor → prove complete → freeze ranking rows built only from prior profiles → freeze current predictors → reveal already-stored result for settlement → update profiles → advance cursor. Never load current profiles after updating them to populate that issue's snapshot.

- [ ] **Step 5: 运行 GREEN 和确定性重放**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q tests/integration/test_causal_processing.py
```

Expected: all tests PASS；重置派生表后确定性重放得到同一有序 snapshot SHA-256；
不存在 snapshot 外 actor/rank 的 candidate；市场样本很少但 overall 已 formal/core 的用户仍携带正确
全局等级，且当 overall 条件下降时所有市场快照同步降级。

- [ ] **Step 6: 提交**

```bash
cd /Users/a123/Documents/极速
git add -- apps/champion_follow_platform/src/champion_follow/repositories/profiles.py \
  apps/champion_follow_platform/src/champion_follow/repositories/snapshots.py \
  apps/champion_follow_platform/src/champion_follow/services/causal.py \
  apps/champion_follow_platform/tests/integration/test_causal_processing.py
git commit -m "feat: freeze causal champion snapshots"
```

### Task 9: 提供分类冠军榜且只暴露匿名短编号

**Files:**
- Create: `apps/champion_follow_platform/src/champion_follow/contracts/rankings.py`
- Create: `apps/champion_follow_platform/src/champion_follow/services/rankings.py`
- Create: `apps/champion_follow_platform/src/champion_follow/api/rankings.py`
- Modify: `apps/champion_follow_platform/src/champion_follow/main.py`
- Test: `apps/champion_follow_platform/tests/integration/test_rankings_api.py`

- [ ] **Step 1: 写分类、破同分和隐私 RED 测试**

```python
@pytest.mark.integration
async def test_market_ranking_is_deterministic_and_hides_actor_key(client, seeded_rankings):
    response = await client.get("/v1/rankings/P1:SIZE?as_of_issue=2607270042")
    assert response.status_code == 200
    body = response.json()
    assert [row["actor_ref"] for row in body["entries"][:2]] == ["A000007", "A000012"]
    assert body["entries"][0]["conservative_unit_return"] >= body["entries"][1]["conservative_unit_return"]
    assert "actor_key" not in response.text
```

Add tests for all 15 markets, overall display ranking, base tie order `(conservative_unit_return DESC, sample_count DESC, actor_key ASC)`, unknown issue, and no snapshot. Device-specific allocation in Plan 03 computes followable rate from the frozen candidate's prior lead times and inserts it between conservative return and sample count; the generic ranking endpoint has no device safe-lead input and therefore must not invent a global followable rate.

- [ ] **Step 2: 运行 RED**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q tests/integration/test_rankings_api.py
```

Expected: 404 because the rankings router is not registered.

- [ ] **Step 3: 实现只读 DTO 与服务**

```python
class RankingEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    actor_ref: str
    market: str
    rank: int
    level: Literal["observed", "candidate", "formal", "core"]
    sample_count: int
    raw_win_rate: Decimal
    conservative_win_rate: Decimal
    unit_return: Decimal
    conservative_unit_return: Decimal
    blind_count: int
    blind_unit_return: Decimal
```

`actor_ref` is formatted as `A{display_no:06d}` from the existing server-owned `anonymous_actors.display_no`; it must never be derived by truncating the HMAC and the API never returns `actor_key`. The endpoint accepts only a validated market and optional `as_of_issue`, reads a frozen snapshot, and never computes a fresh ranking from current profiles.

- [ ] **Step 4: 注册路由并运行 GREEN**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q tests/integration/test_rankings_api.py tests/integration/test_privacy.py
```

Expected: all tests PASS; no response contains a 64-character actor HMAC.

- [ ] **Step 5: 提交**

```bash
git add -- apps/champion_follow_platform/src/champion_follow/contracts/rankings.py \
  apps/champion_follow_platform/src/champion_follow/services/rankings.py \
  apps/champion_follow_platform/src/champion_follow/api/rankings.py \
  apps/champion_follow_platform/src/champion_follow/main.py \
  apps/champion_follow_platform/tests/integration/test_rankings_api.py
git commit -m "feat: expose frozen anonymous champion rankings"
```

### Task 10: 只用历史 `as-of` 候选计算 7/30 天门槛预览

**Files:**
- Create: `apps/champion_follow_platform/src/champion_follow/contracts/thresholds.py`
- Create: `apps/champion_follow_platform/src/champion_follow/repositories/thresholds.py`
- Create: `apps/champion_follow_platform/src/champion_follow/services/threshold_preview.py`
- Create: `apps/champion_follow_platform/src/champion_follow/api/previews.py`
- Modify: `apps/champion_follow_platform/src/champion_follow/main.py`
- Test: `apps/champion_follow_platform/tests/unit/test_thresholds.py`
- Test: `apps/champion_follow_platform/tests/integration/test_threshold_preview_api.py`

- [ ] **Step 1: 写等价门槛和未来泄漏 RED 测试**

```python
def test_effective_win_rate_uses_the_stricter_equivalent_threshold():
    proposal = ThresholdProposal(
        minimum_level="formal",
        minimum_conservative_win_rate=Decimal("0.52"),
        minimum_conservative_unit_return=Decimal("0.04"),
        minimum_followable_rate=Decimal("0.70"),
    )
    assert proposal.effective_minimum_win_rate == (
        (Decimal("1.04") / Decimal("1.96")).quantize(Decimal("0.000000000001"), rounding=ROUND_CEILING)
    )

@pytest.mark.integration
async def test_preview_filters_frozen_candidates_not_today_profile(client, frozen_candidates, pool):
    before = (await client.post("/v1/threshold-previews", json=proposal_json())).json()
    async with pool.connection() as connection:
        await connection.execute("UPDATE actor_profiles SET conservative_win_rate=0.99")
    after = (await client.post("/v1/threshold-previews", json=proposal_json())).json()
    assert after["windows"] == before["windows"]
    assert after["watermark_snapshot_id"] == before["watermark_snapshot_id"]
```

Add `test_preview_watermark_is_latest_overall_snapshot_at_or_before_as_of`,
`test_preview_excludes_candidates_after_watermark_issue`,
`test_preview_watermark_never_depends_on_uuid_order`, and
`test_preview_rejects_a_watermark_without_a_complete_scope_snapshot_set`. The fixtures deliberately give a later
issue a lexicographically smaller UUID and include an incomplete 15-of-16 snapshot group, so UUID ordering or a
partially committed issue cannot pass.

- [ ] **Step 2: 运行 RED**

```bash
.venv/bin/pytest -q tests/unit/test_thresholds.py tests/integration/test_threshold_preview_api.py
```

Expected: collection FAIL because threshold DTOs and preview service do not exist.

- [ ] **Step 3: 实现门槛 DTO 和设备相关可跟单率**

`ThresholdProposal` stores the four approved controls and computes the effective minimum win rate with `ROUND_CEILING`. `ThresholdPreviewService.preview()` accepts `as_of`, optional `device_id`, and that device's recorded safe-lead history. It first selects the latest fully committed issue at or before `as_of`: join its `scope='overall'` snapshot to `game_issues`, require the same namespace/issue to contain exactly `overall` plus all 15 market scopes, require `frozen_at <= as_of`, and order by numeric `issue_no DESC`. The selected overall snapshot UUID is the watermark identity, but UUID value/order is never a time boundary. It then queries only settled `asof_candidates` inside the Shanghai 7/30-day calendar windows whose joined numeric `candidate.issue_no <= watermark.issue_no` and `candidate.frozen_at <= as_of`. A candidate is executable only if its frozen level/metrics meet the proposal and its arrival-to-close interval was at least the device safe lead that existed then.

Return exactly two immutable windows:

```python
@dataclass(frozen=True, slots=True)
class PreviewWindow:
    days: int
    frozen_signal_count: int
    executable_signal_count: int
    win_count: int
    loss_count: int
    unit_profit_micros: int
    raw_win_rate: Decimal
    conservative_win_rate: Decimal

@dataclass(frozen=True, slots=True)
class ThresholdPreviewResult:
    preview_id: UUID
    watermark_snapshot_id: UUID
    generated_at: datetime
    windows: tuple[PreviewWindow, PreviewWindow]
```

- [ ] **Step 4: 单事务持久化父预览、冻结水位和恰好两个窗口**

Repository 把 fixed-decimal proposal、scope/device ID、safe-lead version、`as_of` 和
watermark snapshot canonicalize 后计算请求 SHA-256。在一个事务中先写
`threshold_previews(namespace_id,request_sha256,safe_lead_ms,request_config,as_of,watermark_snapshot_id)`，
再写 `threshold_preview_windows` 的 7、30 两行；重复相同请求返回同一不可变父记录。

写入前验证窗口集合严格等于 `{7,30}`；写后在同事务复读并再次验证。任何 API/仓库读取若少一行、
多一行或重复窗口都失败关闭，绝不返回残缺预览。`unit_profit_micros` 与 win/loss 固定单位公式一致，
`raw_win_rate` 与 decisive count 一致，`conservative_win_rate <= raw_win_rate`；数据库 CHECK
是第二道门禁。`threshold_previews.watermark_scope` 固定生成为 `overall`，复合 FK 证明
watermark 必须是同 namespace 的 overall snapshot。仓库还必须证明其所在期有完整
16-scope 快照组且 `frozen_at <= as_of`；候选查询以 numeric `issue_no` 和 `as_of` 为边界，
严禁比较或排序 snapshot UUID。

本任务不创建或激活 `threshold_versions`；激活权威属于 Plan 03，管理员只能引用这个已完整预览的
parent ID、request digest 和 watermark。

- [ ] **Step 5: 运行 GREEN**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q \
  tests/unit/test_thresholds.py tests/integration/test_threshold_preview_api.py
```

Expected: all tests PASS；修改今日画像不改变历史预览；改变历史 safe-lead version 只影响设备
可执行数；人为删除任一 7/30 子行后读取失败关闭；相同请求、水位和 as-of 幂等返回同一 preview；
之后期候选、UUID 逆序和不完整快照组都不能穿过 watermark。

- [ ] **Step 6: 提交**

```bash
git add -- apps/champion_follow_platform/src/champion_follow/contracts/thresholds.py \
  apps/champion_follow_platform/src/champion_follow/repositories/thresholds.py \
  apps/champion_follow_platform/src/champion_follow/services/threshold_preview.py \
  apps/champion_follow_platform/src/champion_follow/api/previews.py \
  apps/champion_follow_platform/src/champion_follow/main.py \
  apps/champion_follow_platform/tests/unit/test_thresholds.py \
  apps/champion_follow_platform/tests/integration/test_threshold_preview_api.py
git commit -m "feat: preview thresholds from frozen candidates"
```

### Task 11: 适配主采集端会话、事件和心跳公开合同

**Files:**
- Modify: `apps/champion_follow_platform/src/champion_follow/contracts/events.py`
- Modify: `apps/champion_follow_platform/src/champion_follow/api/ingestion.py`
- Modify: `apps/champion_follow_platform/src/champion_follow/repositories/ingestion.py`
- Modify: `apps/champion_follow_platform/tests/integration/test_ingestion_api.py`
- Test: `apps/champion_follow_platform/tests/integration/test_collector_wire_contract.py`

- [ ] **Step 1: 写与 Plan 02 完全相同的 wire RED 测试**

```python
import hashlib
import json
import secrets
from uuid import UUID

import httpx
import pytest
import pytest_asyncio

from champion_follow.config import Settings
from champion_follow.main import create_app


NAMESPACE = UUID("10000000-0000-4000-8000-000000000001")
COLLECTOR = UUID("20000000-0000-4000-8000-000000000001")


def canonical_wire_record_sha256(seq, event):
    payload = json.dumps(
        {"seq": seq, "event": event},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture
def collector_bearer():
    return secrets.token_urlsafe(48)


@pytest.fixture
def one_wire_record():
    event = {
        "kind": "CLOSE",
        "eventKey": "e" * 64,
        "issue": "2607270001",
        "sourceMs": 1_785_084_000_000,
        "receivedAtMs": 1_785_084_000_100,
        "source": "realtime",
        "parserVersion": "btcffc-1",
        "namespaceVersion": "actor-hmac-v1",
    }
    return {"seq": 1, "event": event, "digest": canonical_wire_record_sha256(1, event)}


@pytest_asyncio.fixture
async def wire_client(pool, test_database_url, collector_bearer):
    digest = hashlib.sha256(collector_bearer.encode("utf-8")).hexdigest()
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO identity_namespaces(id,version,mode) VALUES (%s,%s,'active')",
                (NAMESPACE, "actor-hmac-v1"),
            )
            await connection.execute(
                "INSERT INTO collectors(id,namespace_id,wire_id,label,parser_version,bearer_sha256) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    COLLECTOR, NAMESPACE, "collector-main-01", "primary-collector",
                    "btcffc-1", digest,
                ),
            )
    app = create_app(Settings(database_url=test_database_url))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.mark.integration
async def test_collector_session_event_ack_and_heartbeat(
    wire_client, collector_bearer, one_wire_record
):
    headers = {"Authorization": f"Bearer {collector_bearer}"}
    session = await wire_client.post("/v1/collector/session", headers=headers, json={
        "collector_id": "collector-main-01", "namespace_version": "actor-hmac-v1",
    })
    assert session.status_code == 200
    assert session.json() == {
        "ack_seq": 0,
        "ack_event_key": None,
        "history_anchor_event_key": None,
        "namespace_empty": True,
    }
    batch = {"collector_id": "collector-main-01", "namespace_version": "actor-hmac-v1",
             "from_seq": 1, "to_seq": 1, "records": [one_wire_record]}
    accepted = await wire_client.post("/v1/collector/events", headers=headers, json=batch)
    assert accepted.status_code == 200
    assert accepted.json() == {"ack_seq": 1}
    resumed = await wire_client.post("/v1/collector/session", headers=headers, json={
        "collector_id": "collector-main-01", "namespace_version": "actor-hmac-v1",
    })
    assert resumed.json() == {
        "ack_seq": 1,
        "ack_event_key": one_wire_record["event"]["eventKey"],
        "history_anchor_event_key": None,
        "namespace_empty": True,
    }
    heartbeat = {"collector_id": "collector-main-01", "issue": "2607270001",
                 "phase": "BETTING", "countdown_ms": 900, "observed_at_ms": 10,
                 "last_journal_seq": 1, "capture_healthy": True}
    assert (await wire_client.post(
        "/v1/collector/heartbeat", headers=headers, json=heartbeat
    )).status_code == 204
```

The fixture generates a fresh test-only Bearer in memory, inserts only its SHA-256 digest, and never includes the value in assertion messages. Add tests for missing/malformed/wrong Bearer rejection, a credential paired with a different `collector_id`, a replayed identical `(collector, seq,digest)`, changed digest conflict, non-contiguous bounds, stale namespace, ACK/event-key restoration, and heartbeat freshness becoming false after moving server `received_at` to 1001 ms ago. `test_namespace_empty_depends_on_anchorable_money_history_not_any_source_event` proves a namespace containing only close/result/status/gap/baseline rows remains empty for history replay. Add one imported-current-money fixture proving session can return `ack_seq=0/ack_event_key=null` together with a non-null independent `history_anchor_event_key`. `test_realtime_money_event_advances_history_anchor_but_later_marker_ack_does_not` first accepts a bet/cancel, then accepts a later close/result/status/gap receipt: ACK moves to the marker while the money anchor remains unchanged. A later money event advances the anchor by `(source_ms,event_key)`; an older event never regresses it. Add one batch containing `CAPTURE_GAP` and both false/true `ISSUE_STATUS`: all sequences must ACK durably, false and the gap make that issue ineligible, and later `complete=true` never overrides server proof. Assert only safe error codes and statuses; never snapshot headers or the generated Bearer.

- [ ] **Step 2: 运行 RED**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q tests/integration/test_collector_wire_contract.py
```

Expected: 404 because the authenticated public adapter routes are absent.

- [ ] **Step 3: 实现 wire-to-core 适配器**

Keep `CollectorBatch` and the collector UUID as internal domain types. Add strict public models matching Plan 02: the public `collector_id` is `collectors.wire_id`; each record contains `seq`, normalized `event`, and the collector's canonical SHA-256 `digest`. The digest input is exactly UTF-8 canonical JSON of `{ "seq": seq, "event": event }` with recursively sorted object keys, compact separators, literal Unicode and no NaN; it is not an event-only digest. Verify it before conversion and persist it on that sequence's `collector_event_receipts.wire_sha256`；
`source_events.payload_sha256` remains the sequence-independent semantic digest and canonical rows never carry wire
bytes. Every accepted wire sequence writes/validates a receipt even when its semantic event already came from history or
another sequence. Replay queries the exact `(collector_id,stream_sequence)` receipt and compares event key、payload
and wire SHA-256 before returning ACK；changed bytes return `collector_sequence_conflict` rather than silent replay.

Map Plan 02's `BET`, `CANCEL`, `CANCEL_UNATTRIBUTED`, `CLOSE`, and `RESULT` fields explicitly to `NormalizedEvent` (`amountMinor` to integer `amount_fen`, `receivedAtMs` to UTC, and `parserVersion="btcffc-1"` unchanged), set `local_sequence=seq`, and use the existing repository transaction. Persist `CAPTURE_GAP` as a `source_events.kind='capture_gap'` row with only its allowlisted `gap_reason`; persist `ISSUE_STATUS` as `kind='issue_status'` with only `reported_complete` and `reported_reasons`. These two records participate in the same sequence/digest/ACK transaction but never create actors or predictions. `IssueBuilder` treats every persisted `capture_gap` as sticky and ignores `issue_status` as a proof of completeness; the server still requires its own close, result, cancellation, and gap checks. Reject cross-issue/non-contiguous batches, resolve the authenticated wire identity to its internal UUID, and map `highest_contiguous_sequence` to `ack_seq`; do not expose accepted payloads, internal UUIDs, credential digests, or actor keys.

Parse `Authorization` as exactly one Bearer credential, hash it in memory with SHA-256, and look up the unique `bearer_sha256`; missing, malformed, unknown, and wrong-collector identities return only `401/403` plus a stable safe code. Never interpolate the credential/header into logs or exceptions. `/v1/collector/session` separately returns stored `ack_sequence`、`ack_event_key`、`history_anchor_event_key`；`namespace_empty` 表示该 namespace 不存在可作历史锚点的 `current` bet/cancel，不是“没有任何 canonical event”，baseline/close/result/status/gap 都不得使其为 false。历史锚点不能冒充 ACK，ACK 也不能冒充历史锚点。每次成功持久钱类事件时，必须在同一批次事务中把采集器的历史锚点推进到该 namespace 按 `(source_ms,event_key)` 的最新 `current` bet/cancel，不得倒退；close/result/gap/status 只能推进 ACK。`/heartbeat` upserts `collector_heartbeats` with issue, phase, countdown, capture health, last journal sequence and server receive time; server `received_at`, not the client clock, determines the one-second freshness rule.

The final router must expose only `/session`, `/events`, and `/heartbeat`: remove the temporary unauthenticated `/batches` route. Preserve its repository coverage by changing `test_ingestion_api.py` to invoke `app.state.ingestion.accept(CollectorBatch.model_validate(...))` directly; raw private-field rejection remains a DTO validation test. This prevents a second unauthenticated write path in production.

- [ ] **Step 4: 运行 GREEN 和旧内部回归**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q \
  tests/integration/test_collector_wire_contract.py \
  tests/integration/test_ingestion_api.py
```

Expected: all tests PASS; every production collector route rejects unauthenticated writes, the production collector uses only `/session`, `/events`, `/heartbeat`, the last ACK event key survives reconnect, heartbeat freshness uses the persisted server timestamp, and direct service tests still prove repository idempotency.

- [ ] **Step 5: 提交**

```bash
git add -- apps/champion_follow_platform/src/champion_follow/contracts/events.py \
  apps/champion_follow_platform/src/champion_follow/api/ingestion.py \
  apps/champion_follow_platform/src/champion_follow/repositories/ingestion.py \
  apps/champion_follow_platform/tests/integration/test_ingestion_api.py \
  apps/champion_follow_platform/tests/integration/test_collector_wire_contract.py
git commit -m "feat: expose reliable collector wire contract"
```

## Plan 01 completion verification

```bash
cd /Users/a123/Documents/极速/apps/champion_follow_platform
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q
```

Expected: all unit and PostgreSQL integration tests PASS; a derived-table replay produces the same digest; every collector write route requires the registered Bearer; the database contains only its SHA-256 digest; API, schema and captured-output privacy scans find no raw identity, plaintext credential, third-party balance or private request fields.
