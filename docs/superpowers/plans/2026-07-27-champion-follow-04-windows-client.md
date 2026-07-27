# Champion Follow Windows Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建首版 Windows Electron 客户端，在独立内置 Chromium 中登录比特分分彩，安全接收版本化冠军任务，并在 Windows 实机完成一期一单、1.96 倍只追本、订单确认和恢复闭环。

**Architecture:** Electron 主进程拥有服务器连接、设备密钥、金额链、平台会话和下单状态机；沙箱化 renderer 只显示脱敏状态并通过窄 IPC 发出用户动作。Windows 原生小助手把刷新令牌放入 Credential Manager、把不可导出的 ECDSA P-256 私钥放入 CNG；平台 Cookie 仅存在独立 persistent session。所有设备任务以 `(device_id, period_id, revision)` 归并，`CANCEL` 墓碑和最高 revision 优先于 UI 与定时器。

**Tech Stack:** Electron 43.2.0、React 19.2.8、TypeScript 7.0.2、Vite 8.1.5、Vitest 4.1.10、Zod 4.4.3、.NET 10/CNG/Credential Manager、electron-builder 26.15.3、Windows 11。

---

## 依赖边界、文件结构与验收口径

本计划在前三份计划已提供下列稳定接口后执行：

- `apps/champion_follow_platform/contracts/device-task-v1.schema.json`：服务端 Ed25519 签名的 `BET/CANCEL` 任务合同；
- `apps/champion_follow_platform/contracts/client-event-v1.schema.json`：设备 ECDSA P-256 签名的回传合同；
- `apps/champion_follow_platform/contracts/fixtures/*.json`：不含密钥和真实账号的合同夹具；
- 服务端 HTTPS/WSS 端点：登录、设备绑定、状态同步、当前最高任务 revision、订单/结算回传；
- 管理员已经显式保存至少一份门槛配置，否则服务端只会发 `CANCEL`。

锁定以下文件边界；旧监控项目已移除，实施时不得恢复其庄家方向策略或复制进新客户端：

```text
apps/champion_follow_platform/desktop/
  package.json                         # 固定 Node/Electron 脚本与依赖
  package-lock.json                    # npm 可复现锁文件
  tsconfig.json                        # 主进程、preload、renderer 共用严格配置
  vite.config.ts                       # renderer 构建
  electron-builder.yml                # Windows nsis/x64 打包
  native/ChampionFollow.DeviceIdentity/
    ChampionFollow.DeviceIdentity.csproj
    Program.cs                         # JSONL 命令入口，不打印秘密
    CredentialStore.cs                 # CredRead/CredWrite/CredDelete
    DeviceKeyStore.cs                  # CNG 非导出密钥与签名
  native/ChampionFollow.DeviceIdentity.Tests/
    ChampionFollow.DeviceIdentity.Tests.csproj
    CredentialStoreTests.cs
    DeviceKeyStoreTests.cs
  src/shared/
    ipc.ts                             # renderer 可调用的最小 IPC 合同
    models.ts                          # UI 脱敏视图模型
  src/main/
    index.ts                           # BrowserWindow 生命周期和失败关闭默认值
    paths.ts                           # profile、journal、helper 的确定路径
    native-helper.ts                   # JSONL 原生助手封装
    auth-client.ts                     # App 登录、刷新和撤销
    device-identity.ts                 # 公钥注册、签名和本机绑定状态
    task-contract.ts                   # 共享 JSON Schema、Ed25519 验签、revision 归并
    task-socket.ts                     # WSS 重连与当前任务同步
    client-event-contract.ts           # 共享 client-event Schema 与 ECDSA 签名正文
    client-event-client.ts             # 持久序号、幂等回传与 ACK
    bankroll.ts                        # 整数分金额链纯函数
    bankroll-store.ts                  # 原子持久化与结算推进
    platform-session.ts                # 独立 persistent Chromium session
    platform-contract.ts               # 比特分分彩页面合同与只读状态
    platform-adapter.ts                # 唯一真实下单/确认适配器
    execution-machine.ts               # 一期一单状态机
    latency.ts                         # P50/P95/P99 与安全提前量
    scheduler.ts                       # 本地倒计时、最高 revision、末秒冻结
    app-controller.ts                  # 编排但不包含领域计算
    diagnostic-log.ts                  # 30天脱敏日志
    ipc-handlers.ts                    # renderer 命令白名单
  src/preload/index.ts                 # contextBridge 最小 API
  src/renderer/
    index.html
    main.tsx
    App.tsx
    app.css
    use-client-state.ts
    components/ConnectionStrip.tsx
    components/SignalCard.tsx
    components/BankrollCard.tsx
    components/LastOrderCard.tsx
    components/AutoBetControl.tsx
  tests/
    unit/*.test.ts
    integration/*.test.ts
    renderer/*.test.tsx
    fixtures/platform/*.json
    privacy-scan.test.ts
  scripts/
    verify-windows-prereqs.mjs
    run-win-smoke.ps1
```

成功标准：

1. App 每次启动自动下注均为关闭；renderer 卡死不影响末秒执行热路径；
2. 同设备同期至多一个平台确认订单，旧 revision 和旧 generation 永不重发；
3. 高 revision `CANCEL` 到达后，任何乱序旧 `BET` 都不能恢复；
4. 余额不足、结算未知、期号不一致、主采集过期、赔率不是 1.96、页面合同变化均失败关闭；
5. 设备刷新令牌、私钥、平台 Cookie/Token 不出现在 API、日志、SQLite/JSON journal、测试产物或 renderer；
6. Windows 实机测得安全提前量，不能拿 Mac 延迟代替；
7. Windows 打包测试和 1 元受控订单闭环均通过后才标记客户端可试点。

### Task 1: 建立可启动且默认失败关闭的 Electron 外壳

**Files:**
- Create: `apps/champion_follow_platform/desktop/package.json`
- Create: `apps/champion_follow_platform/desktop/tsconfig.json`
- Create: `apps/champion_follow_platform/desktop/vite.config.ts`
- Create: `apps/champion_follow_platform/desktop/src/main/index.ts`
- Create: `apps/champion_follow_platform/desktop/src/main/paths.ts`
- Create: `apps/champion_follow_platform/desktop/src/preload/index.ts`
- Create: `apps/champion_follow_platform/desktop/src/renderer/index.html`
- Create: `apps/champion_follow_platform/desktop/src/renderer/main.tsx`
- Create: `apps/champion_follow_platform/desktop/src/renderer/App.tsx`
- Create: `apps/champion_follow_platform/desktop/tests/unit/startup-defaults.test.ts`

- [ ] **Step 1: 写启动默认值失败测试**

```ts
// tests/unit/startup-defaults.test.ts
import { describe, expect, it } from "vitest";
import { initialRuntimeState } from "../../src/main/index";

describe("initialRuntimeState", () => {
  it("always starts disarmed and without an executable task", () => {
    expect(initialRuntimeState()).toEqual({
      generation: expect.any(String),
      autoBet: "OFF",
      executionBlock: "STARTUP_SYNC_REQUIRED",
      highestTask: null,
    });
  });
  it("creates a fresh generation for every process start", () => {
    expect(initialRuntimeState().generation).not.toBe(
      initialRuntimeState().generation,
    );
  });
});
```

- [ ] **Step 2: 创建固定依赖并运行 RED**

```json
{
  "name": "champion-follow-desktop",
  "version": "0.1.0",
  "private": true,
  "main": "dist-main/index.js",
  "engines": {"node": ">=22.0.0"},
  "scripts": {
    "build": "tsc -p tsconfig.json && vite build",
    "test": "vitest run",
    "test:unit": "vitest run tests/unit",
    "dist:win": "npm run build && electron-builder --win nsis --x64"
  },
  "dependencies": {
    "@vitejs/plugin-react": "6.0.4",
    "ajv": "8.17.1",
    "ajv-formats": "3.0.1",
    "electron": "43.2.0",
    "react": "19.2.8",
    "react-dom": "19.2.8",
    "vite": "8.1.5",
    "ws": "8.18.3",
    "zod": "4.4.3"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "7.0.0",
    "@testing-library/react": "16.3.2",
    "@types/node": "26.1.1",
    "@types/react": "19.2.17",
    "@types/react-dom": "19.2.3",
    "@types/ws": "8.18.1",
    "electron-builder": "26.15.3",
    "jsdom": "29.1.1",
    "typescript": "7.0.2",
    "vitest": "4.1.10"
  }
}
```

Run:

```bash
cd apps/champion_follow_platform/desktop
npm install --package-lock-only
npm ci
npm run test:unit -- startup-defaults.test.ts
```

Expected: FAIL because `src/main/index.ts` does not exist.

- [ ] **Step 3: 实现最小启动状态和安全窗口**

```ts
// src/main/index.ts
import { app, BrowserWindow } from "electron";
import { randomUUID } from "node:crypto";
import { join } from "node:path";

export type RuntimeState = {
  generation: string;
  autoBet: "OFF" | "ON";
  executionBlock: "STARTUP_SYNC_REQUIRED" | null;
  highestTask: null;
};

export const initialRuntimeState = (): RuntimeState => ({
  generation: randomUUID(),
  autoBet: "OFF",
  executionBlock: "STARTUP_SYNC_REQUIRED",
  highestTask: null,
});

export function createMainWindow(): BrowserWindow {
  return new BrowserWindow({
    width: 1180,
    height: 760,
    show: false,
    webPreferences: {
      preload: join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
}

if (process.env.VITEST !== "true") {
  app.whenReady().then(() => {
    const window = createMainWindow();
    window.once("ready-to-show", () => window.show());
    void window.loadFile(join(__dirname, "../renderer/index.html"));
  });
}
```

`preload/index.ts` 只暴露 `getState()` 和 `setAutoBet(enabled)` 两个占位合同，后续任务替换为类型化 IPC；renderer 显示“启动同步中 / 自动下注关闭”，不创建网页 `webview`。

- [ ] **Step 4: 运行测试和构建**

```bash
npm run test:unit -- startup-defaults.test.ts
npm run build
```

Expected: 2 tests PASS；TypeScript/Vite build exit 0；没有启动真实平台页面。

- [ ] **Step 5: 精确提交**

```bash
git add apps/champion_follow_platform/desktop/package.json \
  apps/champion_follow_platform/desktop/package-lock.json \
  apps/champion_follow_platform/desktop/tsconfig.json \
  apps/champion_follow_platform/desktop/vite.config.ts \
  apps/champion_follow_platform/desktop/src/main/index.ts \
  apps/champion_follow_platform/desktop/src/main/paths.ts \
  apps/champion_follow_platform/desktop/src/preload/index.ts \
  apps/champion_follow_platform/desktop/src/renderer \
  apps/champion_follow_platform/desktop/tests/unit/startup-defaults.test.ts
git commit -m "feat: scaffold fail-closed champion desktop"
```

### Task 2: 用 Windows Credential Manager 和 CNG 保存设备身份

**Files:**
- Create: `apps/champion_follow_platform/desktop/native/ChampionFollow.DeviceIdentity/ChampionFollow.DeviceIdentity.csproj`
- Create: `apps/champion_follow_platform/desktop/native/ChampionFollow.DeviceIdentity/Program.cs`
- Create: `apps/champion_follow_platform/desktop/native/ChampionFollow.DeviceIdentity/CredentialStore.cs`
- Create: `apps/champion_follow_platform/desktop/native/ChampionFollow.DeviceIdentity/DeviceKeyStore.cs`
- Create: `apps/champion_follow_platform/desktop/native/ChampionFollow.DeviceIdentity.Tests/ChampionFollow.DeviceIdentity.Tests.csproj`
- Create: `apps/champion_follow_platform/desktop/native/ChampionFollow.DeviceIdentity.Tests/CredentialStoreTests.cs`
- Create: `apps/champion_follow_platform/desktop/native/ChampionFollow.DeviceIdentity.Tests/DeviceKeyStoreTests.cs`
- Create: `apps/champion_follow_platform/desktop/src/main/native-helper.ts`
- Create: `apps/champion_follow_platform/desktop/src/main/device-identity.ts`
- Create: `apps/champion_follow_platform/desktop/tests/unit/native-helper.test.ts`

- [ ] **Step 1: 写 Windows 原生行为测试**

```csharp
// DeviceKeyStoreTests.cs
[Fact]
public void ReusesNonExportableCngKeyAndProducesValidSignature()
{
    var name = $"ChampionFollow-Test-{Guid.NewGuid():N}";
    using var store = new DeviceKeyStore(name);
    var publicKeyBase64 = store.GetOrCreatePublicKeySpkiDerBase64();
    var spkiDer = Convert.FromBase64String(publicKeyBase64);
    using var verifier = ECDsa.Create();
    verifier.ImportSubjectPublicKeyInfo(spkiDer, out var bytesRead);
    Assert.Equal(spkiDer.Length, bytesRead);
    Assert.Equal(spkiDer, verifier.ExportSubjectPublicKeyInfo());
    var signatureDer = store.SignSha256Der(Encoding.UTF8.GetBytes("fixture"));
    Assert.True(verifier.VerifyData(
        "fixture"u8.ToArray(), signatureDer, HashAlgorithmName.SHA256,
        DSASignatureFormat.Rfc3279DerSequence));
    Assert.ThrowsAny<CryptographicException>(() => store.ExportPrivateKey());
    store.Delete();
}
```

```csharp
// CredentialStoreTests.cs
[Fact]
public void RefreshTokenRoundTripsWithoutAppearingInEnumerationOutput()
{
    var target = $"ChampionFollow/Test/{Guid.NewGuid():N}";
    CredentialStore.Write(target, "secret-fixture");
    Assert.Equal("secret-fixture", CredentialStore.Read(target));
    CredentialStore.Delete(target);
    Assert.Null(CredentialStore.Read(target));
}
```

- [ ] **Step 2: 运行 RED（只能在 Windows）**

```powershell
cd apps/champion_follow_platform/desktop/native
dotnet test .\ChampionFollow.DeviceIdentity.Tests\ChampionFollow.DeviceIdentity.Tests.csproj
```

Expected: FAIL because stores are undefined. Mac/Linux CI marks this project `windows-only` and does not claim a pass.

- [ ] **Step 3: 实现最小原生边界**

`DeviceKeyStore` 使用 `CngKey.Create(CngAlgorithm.ECDsaP256, name, parameters)`；参数固定 `Provider=MicrosoftSoftwareKeyStorageProvider`、`KeyUsage=Signing`、`ExportPolicy=None`。公钥只通过 `ExportSubjectPublicKeyInfo()` 导出为规范 SPKI DER，再用带填充的标准 Base64 编码；每次导出后重新导入并比较 DER 字节，非规范结果直接失败。签名固定为 ECDSA-SHA256，输出 `DSASignatureFormat.Rfc3279DerSequence` ASN.1 DER；该签名同时用于设备注册证明和 Task 6 的客户端事件。`CredentialStore` 只通过 `CredWriteW/CredReadW/CredDeleteW` 保存泛型凭据，读出的非托管缓冲立即 `ZeroMemory` 并释放。

`Program.cs` 只接受一行一个 JSON 命令：

```json
{"command":"public_key_spki_der","keyName":"ChampionFollow/Device/<local-id>"}
{"command":"sign_ecdsa_sha256_der","keyName":"ChampionFollow/Device/<local-id>","payloadBase64":"..."}
{"command":"credential_write","target":"ChampionFollow/AppRefresh/<device-id>","value":"..."}
{"command":"credential_read","target":"ChampionFollow/AppRefresh/<device-id>"}
{"command":"credential_delete","target":"ChampionFollow/AppRefresh/<device-id>"}
```

成功输出只包含 `ok`、`publicKeySpkiDerBase64` 或 `signatureDerBase64`；两个 Base64 值均保留 `=` 填充。错误输出只含稳定错误码。任何异常都不得把输入 JSON、凭据值或栈中参数写到 stdout/stderr。

`native-helper.ts` 使用 `spawn()` 和 stdin 发送单条命令，检查 helper 文件 SHA-256 与构建清单一致，设 3 秒超时，结果只保存在内存：

```ts
export interface NativeHelper {
  publicKeySpkiDerBase64(keyName: string): Promise<string>;
  signEcdsaSha256DerBase64(keyName: string, payload: Uint8Array): Promise<string>;
  writeCredential(target: string, value: string): Promise<void>;
  readCredential(target: string): Promise<string | null>;
  deleteCredential(target: string): Promise<void>;
}
```

- [ ] **Step 4: 运行原生、Node 单测和秘密扫描**

```powershell
dotnet test .\native\ChampionFollow.DeviceIdentity.Tests\ChampionFollow.DeviceIdentity.Tests.csproj
npm run test:unit -- native-helper.test.ts
Select-String -Path .\test-results\* -Pattern 'secret-fixture' -SimpleMatch
```

Expected: xUnit/Vitest PASS；最后一条无匹配。测试 teardown 删除测试凭据和测试 CNG 键。

- [ ] **Step 5: 提交**

```bash
git add apps/champion_follow_platform/desktop/native \
  apps/champion_follow_platform/desktop/src/main/native-helper.ts \
  apps/champion_follow_platform/desktop/src/main/device-identity.ts \
  apps/champion_follow_platform/desktop/tests/unit/native-helper.test.ts
git commit -m "feat: protect device identity with windows stores"
```

### Task 3: 验证签名任务并实现最高 revision 归并

**Files:**
- Modify: `apps/champion_follow_platform/desktop/src/main/paths.ts`
- Create: `apps/champion_follow_platform/desktop/src/main/task-contract.ts`
- Create: `apps/champion_follow_platform/desktop/src/main/task-socket.ts`
- Create: `apps/champion_follow_platform/desktop/tests/unit/task-contract.test.ts`
- Create: `apps/champion_follow_platform/desktop/tests/integration/task-socket.test.ts`

- [ ] **Step 1: 写 `CANCEL` 墓碑、嵌套合同和乱序 RED 测试**

```ts
it("never lets an older BET revive a newer CANCEL", () => {
  const reducer = new HighestRevisionTasks(DEVICE_A, fixtureServerKeys, fixedClock);
  expect(reducer.accept(task({revision: 7, action: "BET"}))).toBe("accepted");
  expect(reducer.accept(task({revision: 8, action: "CANCEL"}))).toBe("accepted");
  expect(reducer.accept(task({revision: 7, action: "BET"}))).toBe("stale");
  expect(reducer.current("2607270001")?.action).toBe("CANCEL");
});

it("rejects wrong device, period expiry, and server signature", () => {
  const reducer = new HighestRevisionTasks(DEVICE_A, fixtureServerKeys, fixedClock);
  expect(reducer.accept(task({device_id: DEVICE_B}))).toBe("wrong_device");
  expect(reducer.accept(task({expires_at: "2026-07-26T00:00:00Z"}))).toBe("expired");
  expect(reducer.accept(task({signature: "invalid"}))).toBe("bad_signature");
});

it("keeps every business field inside payload", () => {
  const envelope = task({revision: 1, action: "BET"});
  expect(envelope.period_id).toBe("2607270001");
  expect(envelope.payload).toMatchObject({
    actor_ref: "A000007",
    ball: 2,
    odds_micros: 1_960_000,
  });
  expect(Object.keys(envelope).sort()).toEqual([
    "action", "device_id", "expires_at", "issued_at", "payload", "period_id",
    "revision", "signature", "signing_key_version", "task_id",
  ]);
});
```

测试中的 `task()` 从 `apps/champion_follow_platform/contracts/fixtures/` 读取脱敏任务，应用覆盖后用专用 fixture Ed25519 私钥重新签名；产品代码和打包产物不包含该私钥。加入：同 revision 不同正文拒绝、重连先同步当前最高 revision、WSS 消息乱序、未知 `signing_key_version`、非 Ed25519 公钥、服务器撤销会话后断开、日志不含 bearer token 的测试。

- [ ] **Step 2: 运行 RED**

```bash
npm run test -- tests/unit/task-contract.test.ts tests/integration/task-socket.test.ts
```

Expected: FAIL because task contract loader, Ed25519 verifier, and socket do not exist.

- [ ] **Step 3: 从共享 JSON Schema 解析嵌套 Envelope 并验证 Ed25519**

`paths.ts` 只接受白名单文件名 `device-task-v1.schema.json` 和 `client-event-v1.schema.json`。开发态从 `path.resolve(app.getAppPath(), "../contracts", name)` 读取；打包态从 `path.join(process.resourcesPath, "contracts", name)` 读取。路径不存在或 Schema 编译失败时阻断启动，不回退到内置旧字段。

`task-contract.ts` 用 `Ajv2020({strict: true, allErrors: true})` 与 `ajv-formats` 在启动时只编译一次 `apps/champion_follow_platform/contracts/device-task-v1.schema.json`。通过 Schema 后才转换为以下判别联合；不得接受 camelCase 别名、扁平业务字段或额外字段：

```ts
export type Direction = "BIG" | "SMALL" | "ODD" | "EVEN" | "PRIME" | "COMPOSITE";
export type BetPayload = {
  signal_id: string;
  signal_version: number;
  actor_ref: string;
  ball: 1 | 2 | 3 | 4 | 5;
  direction: Direction;
  threshold_version: number;
  odds_micros: 1_960_000;
  user_level: "CANDIDATE" | "FORMAL" | "CORE";
  sample_count: number;
  conservative_win_rate: string;
  conservative_unit_return: string;
  followable_rate: string;
};
export type CancelPayload = {
  reason:
    | "champion_withdrew" | "profile_downgraded" | "threshold_changed"
    | "collector_stale" | "data_gap" | "device_reassigned"
    | "account_disabled" | "device_unbound" | "global_stop";
};
type TaskBase = {
  task_id: string;
  device_id: string;
  period_id: string;
  revision: number;
  issued_at: string;
  expires_at: string;
  signing_key_version: string;
  signature: string;
};
export type DeviceTaskEnvelope = TaskBase & (
  | {action: "BET"; payload: BetPayload}
  | {action: "CANCEL"; payload: CancelPayload}
);
```

签名正文包含 Envelope 除 `signature` 外的全部顶层字段和嵌套 `payload`，按 Plan 03 的跨端 canonical JSON 规则递归排序键、紧凑 UTF-8 编码并拒绝非有限数。按 `signing_key_version` 从只读的受信服务端公钥表选取公钥；该表只接受规范 Ed25519 SPKI DER。将保留 `==` 填充的 URL-safe Base64 解码为恰好 64 字节后，用 Node `crypto.verify(null, canonicalBytes, ed25519PublicKey, signature)` 验证。这里绝不调用 CNG 设备私钥；CNG 只签设备证明和客户端事件。

受信公钥表只能由登录后的 `GET /api/v1/auth/task-signing-keys` 响应构建：先校验响应结构、`sha256` 与 SPKI DER 字节一致，再用 `createPublicKey({key: der, format: "der", type: "spki"})` 并确认键类型是 Ed25519。只保存当前 App 会话内存副本，重连先刷新公钥再处理任务；接口失败、摘要不符或未知版本均保持自动下注 OFF/阻断状态。v1 的初始信任边界是已认证 HTTPS，不允许从 WSS 任务本身学习新公钥。

`HighestRevisionTasks.accept()` 的顺序固定为：共享 Schema 解析 → `device_id` → UTC 有效期 → `signing_key_version` 与 Ed25519 签名 → `(device_id, period_id, revision)` 归并。等 revision 仅允许 canonical 正文完全相同的幂等重放；任一更高 `CANCEL` 永久压住较低 `BET`。

`task-socket.ts` 只在 Electron 主进程使用锁定的 `ws` 客户端连接 `/ws/v1/device-tasks`，通过建连选项添加内存中的 `Authorization: Bearer ...` 请求头；不把 access token 放入 URL、renderer、Cookie 或连接日志。连接后先发送：

```json
{"type":"SYNC","period_id":"2607270001","known_revision":7}
```

只有把服务端返回的 `TASK`、`UP_TO_DATE` 或 `NO_TASK` 归并完，才处理后续流式 `TASK`。客户端不发送 `device_id`，服务端以认证会话绑定设备；任何 token 只由请求头工厂临时提供，不写对象日志。

- [ ] **Step 4: 运行 GREEN**

```bash
npm run test -- tests/unit/task-contract.test.ts tests/integration/task-socket.test.ts
```

Expected: 全部 PASS；共享 Schema 拒绝旧扁平字段；Ed25519 fixture 验签通过；revision 8 `CANCEL` 后 revision 7 `BET` 永远为 stale。

- [ ] **Step 5: 提交**

```bash
git add apps/champion_follow_platform/desktop/src/main/paths.ts \
  apps/champion_follow_platform/desktop/src/main/task-contract.ts \
  apps/champion_follow_platform/desktop/src/main/task-socket.ts \
  apps/champion_follow_platform/desktop/tests/unit/task-contract.test.ts \
  apps/champion_follow_platform/desktop/tests/integration/task-socket.test.ts
git commit -m "feat: enforce signed highest-revision tasks"
```

### Task 4: 实现整数金额链和原子恢复

**Files:**
- Create: `apps/champion_follow_platform/desktop/src/main/bankroll.ts`
- Create: `apps/champion_follow_platform/desktop/src/main/bankroll-store.ts`
- Create: `apps/champion_follow_platform/desktop/tests/unit/bankroll.test.ts`
- Create: `apps/champion_follow_platform/desktop/tests/integration/bankroll-store.test.ts`

- [ ] **Step 1: 写只追回本金的 RED 测试**

```ts
it("recovers accumulated principal without adding a profit target", () => {
  let state = freshBankroll({baseFen: 1_000, capFen: 100_000, stakeUnitFen: 100});
  state = settleLoss(state, {orderId: "o1", stakeFen: 1_000});
  expect(nextStakeFen(state)).toBe(1_100); // ceil到平台1元单位
  state = settleLoss(state, {orderId: "o2", stakeFen: 1_100});
  expect(nextStakeFen(state)).toBe(2_200); // ceil(2100 / 0.96)到1元单位
  state = settleWin(state, {orderId: "o3", stakeFen: 2_200, netFen: 2_112});
  expect(state.unrecoveredFen).toBe(0);
  expect(nextStakeFen(state)).toBe(1_000);
});

it("closes a cycle at the cap without erasing historical loss", () => {
  const state = {...freshBankroll({baseFen: 100, capFen: 500, stakeUnitFen: 100}), unrecoveredFen: 600};
  const result = planNextStake(state);
  expect(result).toEqual({kind: "RESET_AT_CAP", realizedLossFen: 600, nextStakeFen: 100});
});
```

加入：跳过不清零、换冠军不清零、未知结算冻结、余额不足不缩注、相同 orderId 幂等、修改起始金额开新轮但累计盈亏不变、1分舍入边界的测试。

- [ ] **Step 2: 运行 RED**

```bash
npm run test -- tests/unit/bankroll.test.ts tests/integration/bankroll-store.test.ts
```

Expected: FAIL because bankroll functions do not exist.

- [ ] **Step 3: 实现纯整数公式与原子 journal**

```ts
export const ceilDiv = (numerator: bigint, denominator: bigint): bigint =>
  (numerator + denominator - 1n) / denominator;

export const ceilToUnit = (value: bigint, unit: bigint): bigint =>
  ceilDiv(value, unit) * unit;

export const recoveryStakeFen = (lossFen: bigint, stakeUnitFen: bigint): bigint =>
  ceilToUnit(ceilDiv(lossFen * 100n, 96n), stakeUnitFen);
```

状态只使用 `bigint` 分，不使用 JS `number` 或浮点赔率。`bankroll-store.ts` 采用“写临时文件 → flush → 原子 rename”，每个状态带 `generation`、`cycleId`、`lastSettlementId` 和递增 `version`。启动时若临时文件与正式文件冲突，只选择校验和正确且 version 更高的一份；结算未知保持 `FROZEN_UNKNOWN_SETTLEMENT`。

- [ ] **Step 4: 运行 GREEN 与属性测试**

```bash
npm run test -- tests/unit/bankroll.test.ts tests/integration/bankroll-store.test.ts
```

Expected: 全部 PASS；对 1..1,000,000 分和平台下注单位 1/10/100 分的属性测试证明 `stake*96/100 >= loss`，且少一个下注单位时不满足或已经低于最小可行平台金额。

- [ ] **Step 5: 提交**

```bash
git add apps/champion_follow_platform/desktop/src/main/bankroll.ts \
  apps/champion_follow_platform/desktop/src/main/bankroll-store.ts \
  apps/champion_follow_platform/desktop/tests/unit/bankroll.test.ts \
  apps/champion_follow_platform/desktop/tests/integration/bankroll-store.test.ts
git commit -m "feat: persist principal-only recovery chain"
```

### Task 5: 固化独立平台 Session 和被动页面合同

**Files:**
- Create: `apps/champion_follow_platform/desktop/src/main/platform-session.ts`
- Create: `apps/champion_follow_platform/desktop/src/main/platform-contract.ts`
- Create: `apps/champion_follow_platform/desktop/tests/fixtures/platform/ffc-page-contract-v1.json`
- Create: `apps/champion_follow_platform/desktop/tests/unit/platform-contract.test.ts`
- Create: `apps/champion_follow_platform/desktop/tests/integration/platform-session.test.ts`

- [ ] **Step 1: 写安全边界 RED 测试**

```ts
it("uses a dedicated persistent partition with no Node capability", () => {
  expect(platformPartition("device-A")).toBe("persist:champion-platform-device-A");
  expect(platformWebPreferences("device-A")).toMatchObject({
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    webSecurity: true,
  });
});

it("fails closed when odds or period contract changes", () => {
  expect(parsePlatformState(fixture({odds: "1.96"})).ok).toBe(true);
  expect(parsePlatformState(fixture({odds: "1.95"}))).toEqual({ok: false, code: "ODDS_MISMATCH"});
  expect(parsePlatformState(fixture({periodId: ""}))).toEqual({ok: false, code: "PERIOD_ID_MISSING"});
});
```

测试夹具只含期号、倒计时、赔率、玩法编码和脱敏订单状态；不得含 URL 查询秘密、Cookie、Token、账号、房间私有 ID 或完整请求。

- [ ] **Step 2: 运行 RED**

```bash
npm run test -- tests/unit/platform-contract.test.ts tests/integration/platform-session.test.ts
```

Expected: FAIL because session and parser do not exist.

- [ ] **Step 3: 实现隔离会话与只读合同**

`platform-session.ts` 使用 `session.fromPartition(platformPartition(deviceId))`；权限请求默认拒绝，仅允许既定站点的基础网络/存储；禁止新窗口、下载、导航到非白名单 origin。App 登出平台或管理员解绑时调用 `clearStorageData()`、`clearAuthCache()` 和 `clearCache()`。

`platform-contract.ts` 只从预加载隔离桥接器接收：

```ts
type PlatformState = {
  periodId: string;
  countdownMs: number;
  phase: "OPEN" | "CLOSED" | "RESULT";
  oddsMicrosByDirection: Record<string, 1_960_000>;
  minStakeFen: bigint;
  currentBalanceFen: bigint | null;
  receivedMonotonicMs: number;
};
```

`device-identity.ts` 注册时只把 `publicKeySpkiDerBase64()` 原值放入 Plan 03 的 `public_key_spki_der_b64`，并把按同一 canonical 挑战字节生成的 `signEcdsaSha256DerBase64()` 结果放入 `proof_der_b64`；不允许 PEM、CNG 私钥 blob、raw `x||y`、Ed25519 公钥或 IEEE-P1363 签名进入 HTTPS 请求。

来源超 500ms、期号不匹配、玩法不全或赔率不是 1,960,000 微倍时返回具名阻断原因，不推测缺失值。

- [ ] **Step 4: 运行 GREEN 和夹具隐私扫描**

```bash
npm run test -- tests/unit/platform-contract.test.ts \
  tests/integration/platform-session.test.ts
```

Expected: 全部 PASS；fixture 递归键和值扫描不出现 `cookie|token|authorization|account|uid|password`。

- [ ] **Step 5: 提交**

```bash
git add apps/champion_follow_platform/desktop/src/main/platform-session.ts \
  apps/champion_follow_platform/desktop/src/main/platform-contract.ts \
  apps/champion_follow_platform/desktop/tests/fixtures/platform/ffc-page-contract-v1.json \
  apps/champion_follow_platform/desktop/tests/unit/platform-contract.test.ts \
  apps/champion_follow_platform/desktop/tests/integration/platform-session.test.ts
git commit -m "feat: isolate and validate ffc platform session"
```

### Task 6: 实现一期一单、确认恢复和签名事件回传

**Files:**
- Create: `apps/champion_follow_platform/desktop/src/main/client-event-contract.ts`
- Create: `apps/champion_follow_platform/desktop/src/main/client-event-client.ts`
- Create: `apps/champion_follow_platform/desktop/src/main/platform-adapter.ts`
- Create: `apps/champion_follow_platform/desktop/src/main/execution-machine.ts`
- Create: `apps/champion_follow_platform/desktop/tests/unit/client-event-contract.test.ts`
- Create: `apps/champion_follow_platform/desktop/tests/unit/execution-machine.test.ts`
- Create: `apps/champion_follow_platform/desktop/tests/integration/client-event-client.test.ts`
- Create: `apps/champion_follow_platform/desktop/tests/integration/platform-adapter.test.ts`

- [ ] **Step 1: 写重复、超时、签名回传和未知订单 RED 测试**

```ts
it("submits at most once per device period cycle", async () => {
  const platform = fakePlatform({confirmation: "CONFIRMED"});
  const machine = new ExecutionMachine(platform, store, eventClient);
  await machine.execute(command({periodId: "2607270001", revision: 4}));
  await machine.execute(command({periodId: "2607270001", revision: 5}));
  expect(platform.submitCalls).toHaveLength(1);
  expect(eventClient.types()).toEqual(["EXECUTION_STATE", "ORDER_CONFIRMED"]);
});

it("does not retry when submission outcome is unknown", async () => {
  const platform = fakePlatform({confirmation: "TIMEOUT_AFTER_SEND"});
  const machine = new ExecutionMachine(platform, store, eventClient);
  await expect(machine.execute(command())).resolves.toMatchObject({state: "UNKNOWN"});
  expect(machine.canExecuteNextPeriod()).toBe(false);
  expect(eventClient.types()).toContain("ORDER_UNKNOWN");
});

it("reports an exact settlement before advancing bankroll", async () => {
  await machine.confirmSettlement(settlement({outcome: "WIN", netPnlFen: 96n}));
  expect(eventClient.last()).toMatchObject({type: "SETTLEMENT_CONFIRMED"});
  expect(store.state.lastSettlementId).toBe(SETTLEMENT_ID);
});
```

加入：共享 Schema 拒绝未知事件字段、客户端事件使用 ECDSA P-256 ASN.1 DER 签名、平台明确拒绝不推进金额链、确认期号/方向/金额必须精确匹配、重启后查询历史订单而非重发、旧 App generation 不重发、`CANCEL` 在发送前阻止、发送后不产生第二单、网络重试重放相同事件字节和相同 `client_seq` 的测试。

- [ ] **Step 2: 运行 RED**

```bash
npm run test -- tests/unit/client-event-contract.test.ts \
  tests/unit/execution-machine.test.ts \
  tests/integration/client-event-client.test.ts \
  tests/integration/platform-adapter.test.ts
```

Expected: FAIL because event contract/client, adapter, and execution machine do not exist.

- [ ] **Step 3: 实现状态机、平台适配器和共享 client-event 合同**

允许转换固定为：

```text
PLANNED -> SUBMITTING -> CONFIRMED -> SETTLED
                    \-> REJECTED
                    \-> UNKNOWN
```

`platform-adapter.ts` 是唯一允许触发平台提交的方法，输入必须是已经冻结的：

```ts
type FrozenOrder = {
  clientOrderId: string;
  generation: string;
  taskId: string;
  deviceId: string;
  periodId: string;
  taskRevision: number;
  position: 1 | 2 | 3 | 4 | 5;
  direction: Direction;
  stakeFen: bigint;
  expectedOddsMicros: 1_960_000;
};
```

适配器提交前再读一次页面期号、phase、赔率和余额；平台返回后只保存脱敏的订单号摘要、期号、方向、金额、状态和耗时。不能记录请求头、Cookie、Token、完整响应或账号标识。未知结果必须查询本账号历史订单并精确匹配；仍不能确认则冻结金额链和自动下注。

`client-event-contract.ts` 用与 Task 3 相同的 Ajv 2020 严格设置加载并只编译一次 `apps/champion_follow_platform/contracts/client-event-v1.schema.json`。合同是 `TASK_RECEIVED`、`EXECUTION_STATE`、`ORDER_CONFIRMED`、`ORDER_REJECTED`、`ORDER_UNKNOWN`、`SETTLEMENT_CONFIRMED`、`BALANCE_SNAPSHOT`、`BANKROLL_STATE`、`LATENCY_SAMPLE` 的唯一字段和判别器来源；实现不得另造 camelCase wire 字段。事件 Envelope 按共享 Schema 填入设备 ID、binding epoch、严格递增 `client_seq`、事件 UUID、UTC 观察时间和对应 payload。

签名前删除且只删除 `signature`，使用与服务端相同的 canonical JSON 字节，调用 Task 2 的 `signEcdsaSha256DerBase64()`；把带 `=` 填充的标准 Base64 ASN.1 DER 签名放回 Envelope，再次通过共享 Schema 后才可发送。服务端任务仍只用 Ed25519 验证，两个算法不得共用验证函数或密钥。

`client-event-client.ts` 原子持久化 `binding_epoch`、下一个 `client_seq`、未 ACK 的完整已签名事件字节及摘要。`POST /v1/device/events` 超时只重放完全相同的字节；收到 `{"ack_seq": n}` 后才删除不高于 `n` 的 outbox 项。旧序号正文变化、序号跳跃或 binding epoch 变化均冻结回传与自动下注，不能生成替代事件蒙混重试。

执行机在发送平台订单前产生 `EXECUTION_STATE=SUBMITTING`；按结果产生且只产生一个 `ORDER_CONFIRMED`、`ORDER_REJECTED` 或 `ORDER_UNKNOWN`。确认平台开奖结果后产生 `SETTLEMENT_CONFIRMED`，只有该精确结算已写入本地原子 journal 才推进金额链；随后回传 `BANKROLL_STATE` 和可用的 `BALANCE_SNAPSHOT`。这些订单、结算和金额链回传一律经 `client-event-v1.schema.json`，不存在旁路 JSON API。

- [ ] **Step 4: 运行 GREEN**

```bash
npm run test -- tests/unit/client-event-contract.test.ts \
  tests/unit/execution-machine.test.ts \
  tests/integration/client-event-client.test.ts \
  tests/integration/platform-adapter.test.ts
```

Expected: 全部 PASS；同一期并发调用 100 次仍只有 1 次 `submit()`；订单和结算事件均通过共享 Schema、ECDSA P-256 DER 验签和幂等 ACK 测试。

- [ ] **Step 5: 提交**

```bash
git add apps/champion_follow_platform/desktop/src/main/client-event-contract.ts \
  apps/champion_follow_platform/desktop/src/main/client-event-client.ts \
  apps/champion_follow_platform/desktop/src/main/platform-adapter.ts \
  apps/champion_follow_platform/desktop/src/main/execution-machine.ts \
  apps/champion_follow_platform/desktop/tests/unit/client-event-contract.test.ts \
  apps/champion_follow_platform/desktop/tests/unit/execution-machine.test.ts \
  apps/champion_follow_platform/desktop/tests/integration/client-event-client.test.ts \
  apps/champion_follow_platform/desktop/tests/integration/platform-adapter.test.ts
git commit -m "feat: confirm one order and report signed client events"
```

### Task 7: 实现 P99 安全提前量和最高 revision 末秒调度

**Files:**
- Create: `apps/champion_follow_platform/desktop/src/main/latency.ts`
- Create: `apps/champion_follow_platform/desktop/src/main/scheduler.ts`
- Create: `apps/champion_follow_platform/desktop/tests/unit/latency.test.ts`
- Create: `apps/champion_follow_platform/desktop/tests/unit/scheduler.test.ts`
- Create: `apps/champion_follow_platform/desktop/tests/integration/late-revision.test.ts`

- [ ] **Step 1: 写末秒变化 RED 测试**

```ts
it("replaces a 3-second plan when a newer task arrives with enough time", async () => {
  const clock = fakeMonotonicClock();
  const scheduler = schedulerFixture(clock, {p99Ms: 900, marginMs: 400});
  scheduler.accept(task({revision: 2, action: "BET", payload: {direction: "BIG"}}), platform({countdownMs: 3000}));
  scheduler.accept(task({revision: 3, action: "BET", payload: {direction: "SMALL"}}), platform({countdownMs: 1800}));
  await clock.advanceBy(500);
  expect(scheduler.frozenOrder()?.direction).toBe("SMALL");
});

it("honors a late CANCEL and never falls back to an older BET", async () => {
  const scheduler = schedulerFixture(clock, {p99Ms: 800, marginMs: 300});
  scheduler.accept(task({revision: 4, action: "BET"}), platform({countdownMs: 1600}));
  scheduler.accept(task({revision: 5, action: "CANCEL"}), platform({countdownMs: 1200}));
  await clock.advanceBy(1500);
  expect(executor.calls).toHaveLength(0);
});
```

加入：新 `BET` 到达已小于安全提前量记录 `late_signal`、renderer 主线程阻塞模拟不改变主进程调度、倒计时跳变、服务器时钟与本地页面时钟分歧以页面为准、少于30个确认样本保持保守固定 2 秒提前量的测试。

- [ ] **Step 2: 运行 RED**

```bash
npm run test -- tests/unit/latency.test.ts tests/unit/scheduler.test.ts \
  tests/integration/late-revision.test.ts
```

Expected: FAIL because scheduler does not exist.

- [ ] **Step 3: 实现确定性调度**

`latency.ts` 只纳入真实 `SUBMITTING -> CONFIRMED/REJECTED` 链路样本，滑窗最近 500 次；少于 30 次使用 2,000ms 初始提前量。达到样本门槛后：

```ts
safeLeadMs = clamp(nearestRankP99(samples) + configuredMarginMs, 700, 3_000);
```

`scheduler.ts` 使用 `performance.now()` 单调时钟；每次平台状态或任务 revision 更新都取消旧 timer 并重算。冻结时原子读取最高 revision、页面期号、页面倒计时、金额链和 autoBet；任何一项阻断就不调用执行器。UI 状态通过非阻塞事件副本异步发布。

- [ ] **Step 4: 运行 GREEN 和性能测试**

```bash
npm run test -- tests/unit/latency.test.ts tests/unit/scheduler.test.ts \
  tests/integration/late-revision.test.ts
```

Expected: 全部 PASS；10,000 次任务更新的 p99 归并计算小于 2ms（Windows CI），没有 SQLite/网络/UI await 出现在冻结到 `submit()` 的同步路径。

- [ ] **Step 5: 提交**

```bash
git add apps/champion_follow_platform/desktop/src/main/latency.ts \
  apps/champion_follow_platform/desktop/src/main/scheduler.ts \
  apps/champion_follow_platform/desktop/tests/unit/latency.test.ts \
  apps/champion_follow_platform/desktop/tests/unit/scheduler.test.ts \
  apps/champion_follow_platform/desktop/tests/integration/late-revision.test.ts
git commit -m "feat: schedule latest champion task at safe lead"
```

### Task 8: 接入登录、恢复同步和本地自动下注开关

**Files:**
- Create: `apps/champion_follow_platform/desktop/src/main/auth-client.ts`
- Create: `apps/champion_follow_platform/desktop/src/main/app-controller.ts`
- Create: `apps/champion_follow_platform/desktop/src/main/ipc-handlers.ts`
- Create: `apps/champion_follow_platform/desktop/src/shared/ipc.ts`
- Modify: `apps/champion_follow_platform/desktop/src/preload/index.ts`
- Create: `apps/champion_follow_platform/desktop/tests/integration/startup-recovery.test.ts`
- Create: `apps/champion_follow_platform/desktop/tests/unit/ipc-handlers.test.ts`

- [ ] **Step 1: 写恢复门控 RED 测试**

```ts
it("cannot arm until device keys tasks orders settlements and page period are synchronized", async () => {
  const app = controllerFixture();
  expect(await app.setAutoBet(true)).toEqual({ok: false, code: "STARTUP_SYNC_REQUIRED"});
  await app.syncDevice();
  await app.syncTaskSigningKeys();
  await app.syncHighestTasks();
  await app.syncOrdersAndSettlements();
  await app.syncPlatformPeriod();
  expect(await app.setAutoBet(true)).toEqual({ok: true});
});

it("remote revocation disarms and clears the app refresh credential", async () => {
  const app = controllerFixture({revoked: true});
  await app.onServerRevoked();
  expect(app.state.autoBet).toBe("OFF");
  expect(nativeHelper.deleteCredential).toHaveBeenCalledOnce();
});
```

加入：断网、平台登录失效、主采集心跳过期、未知结算、全局停止、App 退出重启默认 OFF 的测试。

- [ ] **Step 2: 运行 RED**

```bash
npm run test -- tests/integration/startup-recovery.test.ts tests/unit/ipc-handlers.test.ts
```

Expected: FAIL because controller and IPC contract do not exist.

- [ ] **Step 3: 实现显式门控与窄 IPC**

renderer 只可调用：

```ts
export type RendererCommands = {
  getState(): Promise<ClientViewState>;
  register(input: {username: string; password: string; authorizationCode: string}): Promise<PublicResult>;
  login(input: {username: string; password: string}): Promise<PublicResult>;
  setAutoBet(input: {enabled: boolean}): Promise<PublicResult>;
  setStakeConfig(input: {baseYuan: string; capYuan: string; confirmNewCycle: true}): Promise<PublicResult>;
  openPlatformLogin(): Promise<PublicResult>;
  logoutPlatform(): Promise<PublicResult>;
};
```

密码只作为一次 IPC 参数进入主进程 HTTPS 请求，完成后清除引用，不保存、不回显、不记录。刷新令牌立即写入 Credential Manager；设备解绑分别撤销 App refresh、删除设备会话并清空平台 partition。`auth-client.ts` 在每次登录/刷新后调用受保护的 task-signing-keys 端点，将通过 Task 3 校验的公钥集交给任务归并器；不落盘。`setAutoBet(true)` 必须同时检查服务器、采集心跳、公钥同步、平台登录/期号、任务同步、订单同步、金额链和全局停止状态。

- [ ] **Step 4: 运行 GREEN**

```bash
npm run test -- tests/integration/startup-recovery.test.ts tests/unit/ipc-handlers.test.ts
```

Expected: 全部 PASS；任何单项同步缺失都返回一个明确阻断码，且不会触发平台 adapter。

- [ ] **Step 5: 提交**

```bash
git add apps/champion_follow_platform/desktop/src/main/auth-client.ts \
  apps/champion_follow_platform/desktop/src/main/app-controller.ts \
  apps/champion_follow_platform/desktop/src/main/ipc-handlers.ts \
  apps/champion_follow_platform/desktop/src/shared/ipc.ts \
  apps/champion_follow_platform/desktop/src/preload/index.ts \
  apps/champion_follow_platform/desktop/tests/integration/startup-recovery.test.ts \
  apps/champion_follow_platform/desktop/tests/unit/ipc-handlers.test.ts
git commit -m "feat: gate local auto bet on full recovery sync"
```

### Task 9: 实现用户可读但不阻塞热路径的 UI

**Files:**
- Create: `apps/champion_follow_platform/desktop/src/shared/models.ts`
- Modify: `apps/champion_follow_platform/desktop/src/renderer/App.tsx`
- Create: `apps/champion_follow_platform/desktop/src/renderer/use-client-state.ts`
- Create: `apps/champion_follow_platform/desktop/src/renderer/components/ConnectionStrip.tsx`
- Create: `apps/champion_follow_platform/desktop/src/renderer/components/SignalCard.tsx`
- Create: `apps/champion_follow_platform/desktop/src/renderer/components/BankrollCard.tsx`
- Create: `apps/champion_follow_platform/desktop/src/renderer/components/LastOrderCard.tsx`
- Create: `apps/champion_follow_platform/desktop/src/renderer/components/AutoBetControl.tsx`
- Create: `apps/champion_follow_platform/desktop/src/renderer/app.css`
- Create: `apps/champion_follow_platform/desktop/tests/renderer/App.test.tsx`

- [ ] **Step 1: 写完整状态展示 RED 测试**

```tsx
it("shows raw champion metrics and the exact reason for the next amount", async () => {
  render(<App api={fixtureApi({
    signal: {actorRef: "A000007", position: 1, direction: "SMALL",
      tier: "CORE", samples: 618, conservativeWinRate: "54.31%",
      conservativeUnitRoi: "6.45%", followableRate: "81.20%"},
    bankroll: {baseYuan: "1.00", capYuan: "500.00", unrecoveredYuan: "11.00",
      nextStakeYuan: "12.00", reason: "上一注输，下一注只追回累计本金"},
  }))} />);
  expect(await screen.findByText("第一球：小")).toBeVisible();
  expect(screen.getByText("保守胜率 54.31%")).toBeVisible();
  expect(screen.getByText("下一注 12.00 元")).toBeVisible();
});
```

加入：无信号明确显示“本期无合格冠军信号”、有信号但被阻断显示阻断原因、上一注输赢/金额、余额不可用、当前/今日/累计输赢、连接状态、自动下注按钮、不能显示黑盒质量分的测试。

- [ ] **Step 2: 运行 RED**

```bash
npm run test -- tests/renderer/App.test.tsx
```

Expected: FAIL because view model and components are incomplete.

- [ ] **Step 3: 实现节流视图副本**

主进程最多每 100ms 向 renderer 发布一次不可变 `ClientViewState`；订单、阻断和自动下注切换立即发布。UI 不直接访问平台 session、WebSocket、数据库或调度器。显示：服务器/采集/平台状态、期号和倒计时、冠军原始指标、计划金额/预计提交时间、上一注与调整原因、余额/本轮/今日/累计、未追回本金、起始金额/上限、开启/停止按钮。

按钮默认“开启自动下注”，开启时必须二次明确显示当前起始金额和上限；关闭按钮立即调用主进程，不能等待下一次 UI poll。

- [ ] **Step 4: 运行 GREEN 和可访问性检查**

```bash
npm run test -- tests/renderer/App.test.tsx
npm run build
```

Expected: 全部 PASS；键盘可聚焦开关；颜色不是唯一状态表达；renderer 构建不包含 Node polyfill。

- [ ] **Step 5: 提交**

```bash
git add apps/champion_follow_platform/desktop/src/shared/models.ts \
  apps/champion_follow_platform/desktop/src/renderer \
  apps/champion_follow_platform/desktop/tests/renderer/App.test.tsx
git commit -m "feat: show champion execution state in desktop ui"
```

### Task 10: 加入脱敏诊断、Windows 打包和实机门禁

**Files:**
- Create: `apps/champion_follow_platform/desktop/src/main/diagnostic-log.ts`
- Create: `apps/champion_follow_platform/desktop/tests/privacy-scan.test.ts`
- Create: `apps/champion_follow_platform/desktop/electron-builder.yml`
- Create: `apps/champion_follow_platform/desktop/scripts/verify-windows-prereqs.mjs`
- Create: `apps/champion_follow_platform/desktop/scripts/run-win-smoke.ps1`
- Modify: `apps/champion_follow_platform/desktop/package.json`

- [ ] **Step 1: 写日志留存和隐私 RED 测试**

```ts
it("redacts secrets and expires diagnostic files after 30 days", async () => {
  await log.write("platform_error", {
    periodId: "2607270001",
    authorization: "Bearer fixture-secret",
    cookie: "fixture-cookie",
    actorRef: "A000007",
  });
  const text = await readAllLogs();
  expect(text).not.toContain("fixture-secret");
  expect(text).not.toContain("fixture-cookie");
  expect(text).not.toContain("fixture-secret");
  await log.prune(nowPlusDays(31));
  expect(await listLogs()).toHaveLength(0);
});
```

- [ ] **Step 2: 运行 RED**

```bash
npm run test -- tests/privacy-scan.test.ts
```

Expected: FAIL because diagnostic logger does not exist.

- [ ] **Step 3: 实现白名单日志与 Windows 打包配置**

日志事件只允许：时间、generation、匿名设备短标签、期号、revision、状态码、方向、金额、毫秒耗时；拒绝未知键，不采用“先写后正则清洗”。`electron-builder.yml` 固定 x64 NSIS、签名占位由 CI secret store 注入且命令不回显。原生 helper 和两份共享合同 `device-task-v1.schema.json`/`client-event-v1.schema.json` 作为 `extraResources` 输出到 `resources/contracts/`，启动时校验发布清单哈希。

`electron-builder.yml` 必须明确保持与 `paths.ts` 相同的目标：

```yaml
extraResources:
  - from: ../contracts/device-task-v1.schema.json
    to: contracts/device-task-v1.schema.json
  - from: ../contracts/client-event-v1.schema.json
    to: contracts/client-event-v1.schema.json
  - from: native/ChampionFollow.DeviceIdentity/bin/Release/net10.0-windows/
    to: native/ChampionFollow.DeviceIdentity/
```

`run-win-smoke.ps1` 顺序执行：Node 测试、.NET 测试、构建、安装包启动、Credential Manager/CNG 验证、session 清除、默认 OFF 验证；真实平台提交不在该脚本中自动发生。

- [ ] **Step 4: 在 Windows 运行全套门禁**

```powershell
cd apps\champion_follow_platform\desktop
npm ci
npm test
dotnet test .\native\ChampionFollow.DeviceIdentity.Tests\ChampionFollow.DeviceIdentity.Tests.csproj
npm run dist:win
powershell -ExecutionPolicy Bypass -File .\scripts\run-win-smoke.ps1
```

Expected: 全部 exit 0；生成一个签名状态明确的 NSIS 安装包；首次启动自动下注 OFF；日志隐私扫描 0 个敏感字段命中。

- [ ] **Step 5: 提交**

```bash
git add apps/champion_follow_platform/desktop/src/main/diagnostic-log.ts \
  apps/champion_follow_platform/desktop/tests/privacy-scan.test.ts \
  apps/champion_follow_platform/desktop/electron-builder.yml \
  apps/champion_follow_platform/desktop/scripts \
  apps/champion_follow_platform/desktop/package.json \
  apps/champion_follow_platform/desktop/package-lock.json
git commit -m "build: gate champion desktop windows package"
```

## 本计划完成后的验证

```powershell
cd apps\champion_follow_platform\desktop
npm ci
npm test
npm run build
dotnet test .\native\ChampionFollow.DeviceIdentity.Tests\ChampionFollow.DeviceIdentity.Tests.csproj
npm run dist:win
```

预期：全部自动测试通过；App 默认关闭自动下注；平台凭据与 App 凭据严格隔离；乱序旧 `BET` 无法覆盖高 revision `CANCEL`；金额只在平台确认结算后推进；Mac 结果不被写成 Windows 实机性能结论。
