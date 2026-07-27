# Champion Follow Integration and Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把中央服务、主采集端、管理员后台和 Windows 客户端组成可复现的端到端系统，完成历史回放、故障演练、单设备 1 元真实闭环、2～3 设备分流、100 客户端压测和备份恢复验收。

**Architecture:** 自动化验收全部使用脱敏合成平台和固定时钟，真实平台只在 Windows 受控试点步骤由用户显式开启并限制为 1 元。测试编排通过单一 pilot Compose 环境启动 PostgreSQL 与服务端，采集端、客户端模拟器使用真实合同和签名；每个验收产物带版本、命令、校验摘要和通过/失败结论。上线门禁是逐层累积的，任何隐私、重复订单、恢复、revision 或余额问题都会阻断下一阶段。

**Tech Stack:** Python 3.12、pytest、PostgreSQL、Docker Compose、Electron 43.2.0、PowerShell 7、k6、Windows 11、GitHub Actions/自建 Windows runner。

---

## 前置条件、文件结构与不变量

本计划只在 Plan 01～04 的单元和组件集成测试全部通过后执行。旧监控项目已移除；本计划不恢复其数据库或庄家方向逻辑，也不清理工作区现有未跟踪文件。

锁定新增文件：

```text
apps/champion_follow_platform/
  ops/
    compose/pilot.compose.yml             # PostgreSQL、server、只读测试探针
    env/pilot.env.example                 # 纯占位配置，不含秘密
    run/.gitignore                        # 排除本地 pilot 秘密与临时状态
    migrations/check_schema.py            # migration 与模型一致性
    runbooks/single-device-pilot.md        # Windows 1元闭环逐项证据
    runbooks/multi-device-pilot.md         # 2～3设备分流验收
    runbooks/incident-stop.md              # 全局停止、未知订单和恢复
    runbooks/backup-restore.md             # 每日备份与季度恢复演练
  tests/e2e/
    conftest.py                            # 固定时钟、临时库和进程编排
    fixtures/                              # 脱敏事件、任务、结果和平台回执
    test_history_bootstrap.py
    test_live_champion_flow.py
    test_revision_cancel_flow.py
    test_recovery_matrix.py
    test_reporting_boundaries.py
    test_privacy_boundary.py
    test_three_device_allocation.py
  tests/load/
    device-client.js                       # 真实WSS合同的k6客户端
    collector-burst.js                     # 采集ACK/重放压力
    assertions.py                          # DB不变量检查
  scripts/
    pilot-up.sh
    pilot-down.sh
    run-e2e.sh
    run-100-client-load.sh
    backup-postgres.sh
    restore-postgres.sh
    verify-restore.py
    build-evidence-index.py
  evidence/.gitignore                      # 只提交结构，不提交运行秘密/大日志
.github/workflows/champion-follow-ci.yml
.github/workflows/champion-follow-windows.yml
```

贯穿验收的不变量：

- 每设备每期平台确认订单数 `<= 1`；
- 同设备同期任务最高 revision 决定最终动作，高 revision `CANCEL` 永久压过旧 `BET`；
- 所有信号在开奖前已存在，画像只使用当时之前的数据；
- 无法归属撤单、采集缺口、心跳过期和未知结算均不下注；
- 原始第三方标识、App 密码、Cookie、Token、设备私钥和完整平台请求不进入服务端、日志、证据包或测试夹具；
- 所有周期报表按 `Asia/Shanghai` 自然边界；
- Mac 的测试结果不能替代 Windows 实机提交延迟和真实订单验收。

### Task 1: 建立无秘密的 Pilot 编排和一键清理

**Files:**
- Create: `apps/champion_follow_platform/ops/compose/pilot.compose.yml`
- Create: `apps/champion_follow_platform/ops/env/pilot.env.example`
- Create: `apps/champion_follow_platform/ops/run/.gitignore`
- Create: `apps/champion_follow_platform/scripts/pilot-up.sh`
- Create: `apps/champion_follow_platform/scripts/pilot-down.sh`
- Create: `apps/champion_follow_platform/ops/migrations/check_schema.py`
- Create: `apps/champion_follow_platform/tests/e2e/test_pilot_health.py`

- [ ] **Step 1: 写健康与 migration RED 测试**

```python
@pytest.mark.e2e
def test_pilot_starts_with_empty_database_and_no_executable_threshold(http, db):
    health = http.get("/healthz").json()
    assert health == {"status": "ok", "database": "ok"}
    assert db.scalar("select count(*) from threshold_configs") == 0
    assert db.scalar("select count(*) from device_task_revisions") == 0

@pytest.mark.e2e
def test_schema_matches_declared_head(db):
    assert db.scalar(
        "select sha256 from schema_migrations where version='0001_core'"
    ) == EXPECTED_CORE_MIGRATION_SHA256
    assert db.scalar(
        "select version_num from alembic_version"
    ) == EXPECTED_AUTH_ALEMBIC_HEAD
```

- [ ] **Step 2: 运行 RED**

```bash
cd apps/champion_follow_platform
python -m pytest -q tests/e2e/test_pilot_health.py
```

Expected: FAIL because Compose and pilot helpers do not exist.

- [ ] **Step 3: 实现最小 Compose**

`pilot.compose.yml` 固定 PostgreSQL major version和镜像 digest，服务端只暴露 localhost 测试端口，数据库不暴露公网；健康检查必须等待 migration 完成。`pilot.env.example` 只包含：

```dotenv
POSTGRES_DB=champion_follow
POSTGRES_USER=champion_follow
POSTGRES_PASSWORD=SET_A_LOCAL_PILOT_PASSWORD
DATABASE_URL=postgresql://champion_follow:SET_A_LOCAL_PILOT_PASSWORD@postgres/champion_follow
CHAMPION_DATABASE_URL=postgresql+asyncpg://champion_follow:SET_A_LOCAL_PILOT_PASSWORD@postgres/champion_follow
CHAMPION_PUBLIC_BASE_URL=https://127.0.0.1:58443
CHAMPION_TRUSTED_ADMIN_ORIGIN=https://127.0.0.1:58443
CHAMPION_TASK_SIGNING_KEY_PATH=/run/secrets/task-signing.pem
CHAMPION_SECRET_VAULT_KEY_PATH=/run/secrets/vault.key
CHAMPION_ALLOCATION_SEED_PATH=/run/secrets/allocation-seed.key
CHAMPION_ALLOCATION_SEED_VERSION=allocation-v1
CHAMPION_TOKEN_PEPPER=SET_A_LOCAL_TEST_PEPPER_OF_AT_LEAST_32_BYTES
TZ=Asia/Shanghai
```

Collector Bearer 故意不得出现在这份 env 或 Compose 配置中。它只能由 Plan 01 `register-collector` 生成到本次 run 的 owner-only 交接文件，再由 Plan 02 导入 OS 保护存储。

`ops/run/.gitignore` contains exactly:

```gitignore
*
!.gitignore
```

`pilot-up.sh` 只在所需值缺失或仍为占位符时拒绝启动，不打印实际值。对全新的隔离 pilot run，脚本在被 `.gitignore` 排除且权限 `0700` 的 run secrets 目录中一次生成 Ed25519 任务私钥、32 字节 vault key 和 32 字节 allocation seed，文件权限 `0600`；同一 run 重启必须复用，绝不在 stdout、evidence 或 Compose inspect 输出其内容。然后创建隔离 volume，先在同一 PostgreSQL 上运行 Plan 01 的 `champion-follow migrate`，再运行 Plan 03 的 `alembic upgrade head`，最后由 `check_schema.py` 重算并核对 `schema_migrations` 中的核心 SQL digest、Alembic head 和 SQLAlchemy metadata；比对时明确排除 `table.info.schema_owner == "plan01"` 的只读映射，避免把核心表误判为 Alembic 所有。Compose 的唯一 server 容器必须运行 `champion_follow_server.app:create_app` 生成的单 FastAPI 进程，不另起核心微服务。匿名命名空间恢复私钥不挂载到 server，只通过 Plan 02 的 RSA-OAEP 恢复信封在受控恢复流程中使用。`pilot-down.sh` 默认保留 volume 和 run secrets，只有显式 `--destroy-test-data` 才删除带本次随机 pilot label 的 volume 及对应 run secrets，且拒绝匹配非 pilot 名称。

- [ ] **Step 4: 运行 GREEN**

```bash
./scripts/pilot-up.sh
python -m pytest -q tests/e2e/test_pilot_health.py
./scripts/pilot-down.sh --destroy-test-data
```

Expected: tests PASS；服务停止；只删除带本次随机 pilot label 的测试 volume。

- [ ] **Step 5: 提交**

```bash
git add apps/champion_follow_platform/ops/compose/pilot.compose.yml \
  apps/champion_follow_platform/ops/env/pilot.env.example \
  apps/champion_follow_platform/ops/run/.gitignore \
  apps/champion_follow_platform/ops/migrations/check_schema.py \
  apps/champion_follow_platform/scripts/pilot-up.sh \
  apps/champion_follow_platform/scripts/pilot-down.sh \
  apps/champion_follow_platform/tests/e2e/test_pilot_health.py
git commit -m "test: compose isolated champion pilot"
```

### Task 2: 固化脱敏历史导入与逐期盲跟黄金回放

**Files:**
- Create: `apps/champion_follow_platform/tests/e2e/fixtures/history-golden.jsonl`
- Create: `apps/champion_follow_platform/tests/e2e/fixtures/history-golden-expected.json`
- Create: `apps/champion_follow_platform/tests/e2e/test_history_bootstrap.py`
- Create: `apps/champion_follow_platform/scripts/run-e2e.sh`

- [ ] **Step 1: 写未来信息泄漏 RED 测试**

```python
def test_history_import_reproduces_as_of_rankings_without_future_rows(api, golden):
    imported = api.import_history(golden.events, namespace_version="fixture-v1")
    assert imported == {"inserted": 84, "duplicates": 4, "incomplete_issues": 1}
    snapshots = api.list_as_of_snapshots()
    assert [s["issue"] for s in snapshots] == golden.expected_issues
    for snapshot in snapshots:
        assert snapshot["source_max_event_ms"] < snapshot["draw_opened_ms"]
    assert api.profile("actor-added-at-last-row")["blind_follow_count"] == 0
```

加入：同 event key 去重、命名空间不一致进入历史基线分区、不合并匿名画像、无法归属撤单整期排除、追加后撤单再反向下注正确计净方向、Wilson和30/200/500边界的黄金断言。

- [ ] **Step 2: 运行 RED**

```bash
./scripts/pilot-up.sh
python -m pytest -q tests/e2e/test_history_bootstrap.py
```

Expected: FAIL until fixture importer and expected file are wired.

- [ ] **Step 3: 建立可人工复核的小型 fixture**

`history-golden.jsonl` 使用 `actor_key = SHA-256("fixture-N")`，覆盖 12 个期次、5球三组玩法、已识别撤单、无法归属撤单、重复、缺口和开奖结果。`history-golden-expected.json` 明确列出每期开奖前榜首、当时样本数、Wilson下界、任务资格和开奖后固定 1 元收益；值由独立测试公式生成后固化，运行时不能重新把实现结果写回 expected。

`run-e2e.sh` 每次创建新 schema，先跑 migration，再导入 fixture，测试完清理；脚本不接受生产数据库 URL。

- [ ] **Step 4: 运行 GREEN 两次验证可复现**

```bash
./scripts/run-e2e.sh tests/e2e/test_history_bootstrap.py
./scripts/run-e2e.sh tests/e2e/test_history_bootstrap.py
sha256sum tests/e2e/fixtures/history-golden-expected.json
```

Expected: 两次 PASS；数据库生成的 snapshot digest 相同；expected 文件 hash 不变。

- [ ] **Step 5: 提交**

```bash
git add apps/champion_follow_platform/tests/e2e/fixtures/history-golden.jsonl \
  apps/champion_follow_platform/tests/e2e/fixtures/history-golden-expected.json \
  apps/champion_follow_platform/tests/e2e/test_history_bootstrap.py \
  apps/champion_follow_platform/scripts/run-e2e.sh
git commit -m "test: freeze blind champion history replay"
```

### Task 3: 验证采集到任务再到结算的完整合成闭环

**Files:**
- Create: `apps/champion_follow_platform/tests/e2e/conftest.py`
- Create: `apps/champion_follow_platform/tests/e2e/fixtures/live-flow.json`
- Create: `apps/champion_follow_platform/tests/e2e/test_live_champion_flow.py`
- Create: `apps/champion_follow_platform/tests/e2e/test_revision_cancel_flow.py`

- [ ] **Step 1: 写端到端 RED 测试**

```python
@pytest.mark.e2e
async def test_live_event_becomes_one_settled_device_order(stack):
    await stack.collector.append_bet(actor="fixture-a", issue="2607270001", play="P1:SMALL")
    await stack.collector.flush_and_wait_ack()
    task = await stack.device.wait_task(issue="2607270001")
    assert task["action"] == "BET"
    assert task["period_id"] == "2607270001"
    assert task["payload"]["direction"] == "SMALL"
    receipt = await stack.device.confirm_fixture_order(task, stake_minor=100)
    await stack.draw.publish(issue="2607270001", digits=[3, 8, 4, 6, 0])
    settled = await stack.device.wait_settlement(receipt)
    assert settled["net_pnl_minor"] == 96
    assert stack.db.confirmed_orders("device-fixture", "2607270001") == 1
```

```python
@pytest.mark.e2e
async def test_unattributed_cancel_emits_higher_cancel_tombstone(stack):
    bet = await stack.signal_and_task(revision=4)
    await stack.collector.append_unattributed_cancel(issue=bet["period_id"])
    cancel = await stack.device.wait_revision(5)
    assert cancel["action"] == "CANCEL"
    await stack.device.deliver_out_of_order(bet)
    assert stack.device.current_task()["action"] == "CANCEL"
    assert stack.platform.submit_count == 0
```

- [ ] **Step 2: 运行 RED**

```bash
./scripts/run-e2e.sh \
  tests/e2e/test_live_champion_flow.py \
  tests/e2e/test_revision_cancel_flow.py
```

Expected: FAIL until process fixtures and fake platform are connected.

- [ ] **Step 3: 实现合成平台和固定时钟**

`conftest.py` 启动真实 server/collector uploader/desktop domain adapter，但平台端替换为进程外 fake HTTPS endpoint；fake 只实现公开合同状态、订单接受/拒绝/未知和历史订单查询。所有时间来自测试 `MonotonicClock + Asia/Shanghai wall clock`，不读取未来开奖。fixture task 使用测试签名键，私钥只存在测试进程内且不写证据。

每个隔离 e2e run 先创建 `actor-hmac-v1` 活跃命名空间，再调用 Plan 01 `register-collector --collector-id collector-e2e-<run-id> --credential-handoff <ops/run/...>`。交接目录必须属于当前用户且为 `0700`，文件必须为 `0600`。Node/Electron 测试驱动只通过 Plan 02 `CollectorCredentialStore.importFromFile()` 导入；在发出第一个 `/v1/collector/session` 前必须断言原文件已删除、密文已落盘。Bearer 不经 Python fixture、环境变量、argv、pytest 输出或 evidence；断线重连必须从同一 OS 保护存储加载。

- [ ] **Step 4: 运行 GREEN 和数据库不变量查询**

```bash
./scripts/run-e2e.sh tests/e2e/test_live_champion_flow.py tests/e2e/test_revision_cancel_flow.py
```

Expected: 全部 PASS；`confirmed_orders <= 1`；CANCEL后无提交；画像、signal、task、order、settlement均可按 correlation id 回放。

- [ ] **Step 5: 提交**

```bash
git add apps/champion_follow_platform/tests/e2e/conftest.py \
  apps/champion_follow_platform/tests/e2e/fixtures/live-flow.json \
  apps/champion_follow_platform/tests/e2e/test_live_champion_flow.py \
  apps/champion_follow_platform/tests/e2e/test_revision_cancel_flow.py
git commit -m "test: close synthetic champion order loop"
```

### Task 4: 建立重启、断线、未知订单和时钟漂移故障矩阵

**Files:**
- Create: `apps/champion_follow_platform/tests/e2e/test_recovery_matrix.py`
- Create: `apps/champion_follow_platform/ops/runbooks/incident-stop.md`

- [ ] **Step 1: 写参数化故障 RED 测试**

```python
@pytest.mark.parametrize("failure", [
    "collector_before_ack", "server_after_event_commit", "database_restart",
    "client_after_send", "client_after_confirm", "websocket_partition",
    "collector_heartbeat_stale", "platform_session_expired", "clock_jump",
])
async def test_failure_never_duplicates_or_advances_unknown_money(stack, failure):
    result = await stack.run_failure_scenario(failure)
    assert result.confirmed_orders_per_device_issue <= 1
    assert not result.stale_bet_revived_after_cancel
    if result.order_outcome == "UNKNOWN":
        assert result.bankroll_state == "FROZEN_UNKNOWN_SETTLEMENT"
```

- [ ] **Step 2: 运行 RED**

```bash
./scripts/run-e2e.sh tests/e2e/test_recovery_matrix.py
```

Expected: one failure per missing recovery hook; no scenario is silently skipped.

- [ ] **Step 3: 补齐故障控制器和事故步骤**

测试控制器通过进程信号和 Compose restart 注入故障，不 monkeypatch 领域不变量。`incident-stop.md` 固定步骤：全局停止 → 保存最高 revision/订单摘要 → 不重发未知订单 → 查平台历史 → 确认结算或保持冻结 → 恢复采集心跳/期号 → 用户再次显式开启。文档禁止复制 Cookie/Token/完整请求到工单。

- [ ] **Step 4: 运行 GREEN**

```bash
./scripts/run-e2e.sh tests/e2e/test_recovery_matrix.py
```

Expected: 所有参数 PASS；collector ACK 重放不丢不重；server/database/client重启不产生第二个确认订单。

- [ ] **Step 5: 提交**

```bash
git add apps/champion_follow_platform/tests/e2e/test_recovery_matrix.py \
  apps/champion_follow_platform/ops/runbooks/incident-stop.md
git commit -m "test: prove fail-closed recovery matrix"
```

### Task 5: 验证分周期报表与余额复核

**Files:**
- Create: `apps/champion_follow_platform/tests/e2e/test_reporting_boundaries.py`

- [ ] **Step 1: 写 Asia/Shanghai 边界 RED 测试**

```python
def test_reports_day_week_month_quarter_year_and_lifetime(api, settled_fixture):
    report = api.user_report("device-fixture", as_of="2026-07-27T12:00:00+08:00")
    assert report["periods"]["today"]["net_pnl_minor"] == 96
    assert report["periods"]["yesterday"]["net_pnl_minor"] == -100
    assert report["periods"]["week"]["settled_bet_count"] == 3
    assert report["periods"]["month"]["turnover_minor"] == 400
    assert report["periods"]["quarter"]["net_pnl_minor"] == -4
    assert report["periods"]["year"]["net_pnl_minor"] == -4
    assert report["periods"]["cumulative"]["net_pnl_minor"] == -4
```

加入上海时区午夜、周一、月初、季度初、年初；充值/提现/赠送/返点余额差额只进入 `unrecognized_balance_adjustment_minor`，不改订单盈亏的测试。

- [ ] **Step 2: 运行 RED**

```bash
./scripts/run-e2e.sh tests/e2e/test_reporting_boundaries.py
```

Expected: FAIL until report endpoint uses calendar boundaries.

- [ ] **Step 3: 固化 fixture 与查询期望**

fixture 在每个边界前后各放一笔已确认结算，并加入一个 +10,000 分无法识别余额调整。测试同时校验普通用户只能看自己、管理员可看全部、管理员合计与逐用户加总一致。

- [ ] **Step 4: 运行 GREEN**

```bash
./scripts/run-e2e.sh tests/e2e/test_reporting_boundaries.py
```

Expected: 全部 PASS；报告只基于平台已结算订单，余额差异单列。

- [ ] **Step 5: 提交**

```bash
git add apps/champion_follow_platform/tests/e2e/test_reporting_boundaries.py
git commit -m "test: verify shanghai reporting boundaries"
```

### Task 6: 完成 100 客户端并发与采集积压压测

**Files:**
- Create: `apps/champion_follow_platform/tests/load/device-client.js`
- Create: `apps/champion_follow_platform/tests/load/collector-burst.js`
- Create: `apps/champion_follow_platform/tests/load/assertions.py`
- Create: `apps/champion_follow_platform/scripts/run-100-client-load.sh`

- [ ] **Step 1: 写压测后不变量 RED 检查**

```python
def test_load_invariants(db):
    assert db.scalar("""
      select count(*) from (
        select device_id, period_id from orders
        group by device_id, period_id having count(*) > 1
      ) duplicated
    """) == 0
    assert db.scalar("select count(*) from device_task_revisions where revision < 1") == 0
```

- [ ] **Step 2: 运行基线 RED**

```bash
./scripts/run-100-client-load.sh --devices 5 --issues 10
```

Expected: FAIL until k6 scripts and post-run assertions exist.

- [ ] **Step 3: 实现真实合同压测**

`device-client.js` 建 100 个独立设备会话，消费真实签名任务、ACK revision、回传合成订单与结算；不使用同一个 device id。`collector-burst.js` 每期发送追加、撤单和 heartbeat，包含断线后 journal 重放。阈值固定：

```js
export const options = { thresholds: {
  http_req_failed: ["rate<0.001"],
  http_req_duration: ["p(95)<250"],
  ws_session_duration: ["p(95)<90000"],
}};
```

服务端还要输出各链路 P50/P95/P99；测试报告记录环境 CPU/内存和 Git SHA，不把合成结果伪称真实平台性能。

- [ ] **Step 4: 跑 100 设备门禁**

```bash
./scripts/run-100-client-load.sh --devices 100 --issues 300
```

Expected: k6 thresholds PASS；数据库不变量 PASS；0重复确认、0串号、0跨设备金额链污染；任务与结算回传在约定 p95 内。

- [ ] **Step 5: 提交**

```bash
git add apps/champion_follow_platform/tests/load \
  apps/champion_follow_platform/scripts/run-100-client-load.sh
git commit -m "test: load champion platform with 100 devices"
```

### Task 7: 验证 2～3 设备确定性分流与金额链隔离

**Files:**
- Create: `apps/champion_follow_platform/tests/e2e/test_three_device_allocation.py`
- Create: `apps/champion_follow_platform/ops/runbooks/multi-device-pilot.md`

- [ ] **Step 1: 写分流 RED 测试**

```python
async def test_three_devices_rotate_first_priority_and_keep_separate_bankrolls(stack):
    result = await stack.run_issues(count=300, devices=3, qualified_signals=3)
    first_counts = result.first_priority_counts
    assert max(first_counts.values()) - min(first_counts.values()) <= 1
    assert result.max_same_exact_direction_devices == 1
    assert result.max_pair_same_executed_sequence <= 3
    assert result.bankroll_cycle_ids_are_device_scoped
```

加入两个不同正式冠军同向时最多2台、设备上下线重放一致、双方共同跳过不增加也不重置连续计数、无其他信号则跳过、不得随机或反买的测试。

- [ ] **Step 2: 运行 RED**

```bash
./scripts/run-e2e.sh tests/e2e/test_three_device_allocation.py
```

Expected: FAIL if assignment seed/device ordering is not reproducible.

- [ ] **Step 3: 编写人工多设备验收清单**

`multi-device-pilot.md` 要求记录三台 Windows 设备公钥摘要短标签、每期期号/候选版本/任务 revision/最终方向/确认订单、第一优先级次数、相同序列计数和各自金额链。不得记录平台账号、原始第三方身份或会话。

- [ ] **Step 4: 运行 GREEN 和 300 期回放**

```bash
./scripts/run-e2e.sh tests/e2e/test_three_device_allocation.py
```

Expected: PASS；固定 seed 重跑得到相同 assignment digest；改变 seed version 才允许改变分配。

- [ ] **Step 5: 提交**

```bash
git add apps/champion_follow_platform/tests/e2e/test_three_device_allocation.py \
  apps/champion_follow_platform/ops/runbooks/multi-device-pilot.md
git commit -m "test: verify deterministic three-device routing"
```

### Task 8: 完成数据库和匿名命名空间备份恢复演练

**Files:**
- Create: `apps/champion_follow_platform/scripts/backup-postgres.sh`
- Create: `apps/champion_follow_platform/scripts/restore-postgres.sh`
- Create: `apps/champion_follow_platform/scripts/verify-restore.py`
- Create: `apps/champion_follow_platform/ops/runbooks/backup-restore.md`

- [ ] **Step 1: 写恢复一致性 RED 测试**

```python
def verify(before, after):
    assert after.core_migration_digests == before.core_migration_digests
    assert after.auth_alembic_head == before.auth_alembic_head
    assert after.authoritative_event_count == before.authoritative_event_count
    assert after.event_digest == before.event_digest
    assert after.profile_digest == before.profile_digest
    assert after.highest_task_revisions == before.highest_task_revisions
    assert after.identity_namespace_version == before.identity_namespace_version
    assert after.identity_probe_digest == before.identity_probe_digest
    assert after.service_secret_fingerprints == before.service_secret_fingerprints
```

- [ ] **Step 2: 运行 RED**

```bash
./scripts/backup-postgres.sh --pilot
./scripts/restore-postgres.sh --pilot --to-new-volume
python scripts/verify-restore.py --before evidence/before.json --after evidence/after.json
```

Expected: FAIL until scripts create signed manifests and restore namespace material.

- [ ] **Step 3: 实现加密备份与实际恢复**

数据库使用 custom-format `pg_dump`。匿名命名空间只通过 Plan 02 `IdentityStore.exportRecovery()` 生成的 RSA-OAEP 加密恢复信封备份，并记录不可变 namespace version；恢复私钥由独立受控存储保管，不挂载到日常 server 容器。服务端的 Ed25519 任务私钥、vault key 和 allocation seed 另外封装成受控恢复公钥加密的 secret bundle；备份进程只读一次并立即清零临时缓冲，不保存明文中间文件。Collector Bearer 明文和 `collector-credential.enc` 均不进入服务端备份；数据库只恢复其 SHA-256 摘要，恢复验证使用原主采集机现有的 OS 保护凭据执行一次认证 `/session`，不导出或复制 Bearer。manifest 只保存文件 hash、核心 SQL digest、Alembic head、行数、namespace version、二次哈希后的合成 identity probe digest 和非秘密的服务密钥指纹，不保存密钥、actor key 或原始 probe 输入。恢复只能写新数据库/新安全存储槽，验证完成后才允许切换。任何 identity probe digest 或服务密钥指纹不一致都拒绝启用画像/任务服务，不能建立猜测别名或静默更换密钥。

- [ ] **Step 4: 完成恢复演练**

```bash
./scripts/backup-postgres.sh --pilot
./scripts/restore-postgres.sh --pilot --to-new-volume
python scripts/verify-restore.py --before evidence/before.json --after evidence/after.json
```

Expected: PASS；核心 SQL digest 与 auth Alembic head 均一致；合成 identity probe digest 和服务密钥指纹一致；任务最高 revision、画像和事件 digest 一致；备份目录秘密扫描无明文密钥。

- [ ] **Step 5: 提交**

```bash
git add apps/champion_follow_platform/scripts/backup-postgres.sh \
  apps/champion_follow_platform/scripts/restore-postgres.sh \
  apps/champion_follow_platform/scripts/verify-restore.py \
  apps/champion_follow_platform/ops/runbooks/backup-restore.md
git commit -m "ops: verify champion backup recovery"
```

### Task 9: 建立全仓隐私扫描与可审计证据索引

**Files:**
- Create: `apps/champion_follow_platform/tests/e2e/test_privacy_boundary.py`
- Create: `apps/champion_follow_platform/scripts/build-evidence-index.py`
- Create: `apps/champion_follow_platform/evidence/.gitignore`

- [ ] **Step 1: 写隐私 RED 测试**

```python
FORBIDDEN_KEYS = {
    "cookie", "authorization", "password", "refresh_token", "access_token",
    "platform_actor_id", "private_key", "collector_bearer", "raw_request", "raw_response",
}

def test_api_database_logs_and_fixtures_have_no_private_fields(scan_targets):
    violations = scan_structured_targets(scan_targets, forbidden_keys=FORBIDDEN_KEYS)
    assert violations == []
```

测试另放可识别 canary 值到浏览器隔离层，证明 canary 不出现在 server request、PostgreSQL、diagnostic log、pytest output 和 evidence。

- [ ] **Step 2: 运行 RED**

```bash
./scripts/run-e2e.sh tests/e2e/test_privacy_boundary.py
```

Expected: 测试会先证明注入一个故意违规 fixture 时 FAIL，删除违规 fixture 后系统扫描 PASS。

- [ ] **Step 3: 实现证据索引**

`build-evidence-index.py` 仅收集：Git SHA、合同/镜像/fixture hash、测试命令、开始结束时间、PASS/FAIL、性能分位数、数据库不变量计数和人工签字状态；不复制原始日志。证据文件以测试 run UUID 命名，诊断日志仍按30天删除。

- [ ] **Step 4: 运行全套隐私门禁**

```bash
./scripts/run-e2e.sh tests/e2e/test_privacy_boundary.py
python scripts/build-evidence-index.py --run-dir evidence/current
```

Expected: 0 violations；索引 JSON schema 校验 PASS；没有凭据、原始UID或完整请求。

- [ ] **Step 5: 提交**

```bash
git add apps/champion_follow_platform/tests/e2e/test_privacy_boundary.py \
  apps/champion_follow_platform/scripts/build-evidence-index.py \
  apps/champion_follow_platform/evidence/.gitignore
git commit -m "test: enforce champion privacy boundary"
```

### Task 10: 在 Windows 完成单设备 1 元受控真实闭环

**Files:**
- Create: `apps/champion_follow_platform/ops/runbooks/single-device-pilot.md`

- [ ] **Step 1: 写不可跳过的前置清单**

清单必须逐项记录 PASS：全自动测试、Windows安装包签名、平台合同版本、管理员已显式保存门槛、全局停止可用、客户端默认OFF、起始金额1元、单注上限1元、当前未追回0、余额足够、未知订单0、主采集端凭据已在 OS 保护存储且交接原文件不存在、主采集心跳新鲜、日志隐私门禁、用户本人在场。

- [ ] **Step 2: 先做不提交的完整演练**

在 Windows 客户端保持 autoBet OFF，等待一个合格冠军信号，核对 UI 中期号、球位、方向、revision、金额、预计提交时间和阻断状态；确认平台 adapter 没有被调用。保存脱敏 evidence index，不截取账号或余额之外的隐私页面。

Expected: 计划完整显示；0平台提交；倒计时与平台页面一致。

- [ ] **Step 3: 用户显式开启并只允许一笔 1 元订单**

在 runbook 中启用一次性 pilot gate：`max_confirmed_orders=1`、`base=1.00`、`cap=1.00`。用户在 App 内开启；系统只在一个合格信号的安全提前量执行一次。若任务变化、CANCEL、期号不符或时间不足则跳过，不为了完成验收强行下注。

Expected: 最多一个平台确认订单；方向、金额、期号与冻结任务完全一致。

- [ ] **Step 4: 等待真实结算并核对三方状态**

核对平台已结算订单、客户端金额链、服务器订单/结算和余额快照。赢则净收益按平台 1.96 合同记录；输则因 cap=1 元触发轮次上限重置；余额差额不一致单列，不改写订单盈亏。订单未知时停止，不执行第二单。

- [ ] **Step 5: 提交 runbook，不提交真实证据中的敏感数据**

```bash
git add apps/champion_follow_platform/ops/runbooks/single-device-pilot.md
git commit -m "docs: define one-yuan windows pilot gate"
```

### Task 11: 完成 2～3 台 Windows 小规模试点和最终门禁

**Files:**
- Create: `.github/workflows/champion-follow-ci.yml`
- Create: `.github/workflows/champion-follow-windows.yml`
- Modify: `apps/champion_follow_platform/ops/runbooks/multi-device-pilot.md`

- [ ] **Step 1: 配置跨平台CI但分开声称结果**

Linux job 运行 server/collector/contracts/e2e/load-smoke；Windows job 运行 Electron、CNG/Credential Manager、NSIS、late-window合成测试。CI secrets 只通过平台 secret store注入，命令不回显，PR artifact 不包含 `.env`、profile、journal或原始日志。

- [ ] **Step 2: 在两台设备运行只读分流观察**

两台 Windows 客户端 autoBet OFF 连续观察至少100个合格信号，核对确定性轮换、同方向上限、最高 revision、连续相同序列限制和各自安全提前量。任何串号或分配不可复现都返回修复，不进入真实小额试点。

- [ ] **Step 3: 执行两台、再三台 1 元小额试点**

每台起始1元，先把单注上限保持1元，验证各自订单、结算、余额和金额链完全隔离；稳定后才按用户明确决定提高上限。设备下线/重连时必须产生可回放的高 revision 任务，已进入提交的设备不再收到第二项。

- [ ] **Step 4: 生成首版验收索引**

```bash
python apps/champion_follow_platform/scripts/build-evidence-index.py \
  --run-dir apps/champion_follow_platform/evidence/release-candidate
```

Expected: 首版10项成功标准全部有 PASS 证据；100客户端是合成压测、Windows 1元是受控真实闭环，两者标签不得混淆；任何 FAIL 都阻断发布。

- [ ] **Step 5: 提交CI与最终runbook**

```bash
git add .github/workflows/champion-follow-ci.yml \
  .github/workflows/champion-follow-windows.yml \
  apps/champion_follow_platform/ops/runbooks/multi-device-pilot.md
git commit -m "ci: gate champion windows pilot release"
```

## 本计划完成后的总验证

```bash
cd apps/champion_follow_platform
./scripts/pilot-up.sh
./scripts/run-e2e.sh
./scripts/run-100-client-load.sh --devices 100 --issues 300
python scripts/build-evidence-index.py --run-dir evidence/release-candidate
./scripts/pilot-down.sh
```

随后只在 Windows 实机按 `ops/runbooks/single-device-pilot.md` 和 `ops/runbooks/multi-device-pilot.md` 逐项执行。完成并不代表保证盈利；它只证明本规格要求的数据时序、冠军跟随、金额链、一期一单、故障恢复、隐私和可审计性达到已定义门禁。
