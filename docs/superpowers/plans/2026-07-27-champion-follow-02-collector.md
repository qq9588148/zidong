# Champion Follow Main Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the dedicated Electron main collector that converts public Btcffc room activity into stable anonymous, replayable events and reliably delivers only complete issues to the central server.

**Architecture:** A dedicated Electron Chromium session remains logged into the Btcffc room. A page hook forwards public room messages to an isolated preload normalizer, where actors are HMAC-anonymized before strict events cross IPC; Electron main synchronously appends those events to a checksummed journal before acknowledging capture, and an idempotent uploader advances only the server’s highest contiguous ACK. A one-time collector credential handoff is imported from an owner-only file or stdin, encrypted with Electron `safeStorage`, and removed from its source before networking starts. A conservative completeness state machine applies attributable cancellations and excludes every gap, ambiguous cancellation, net-direction conflict, or incomplete result.

**Tech Stack:** Electron 43.2.0, TypeScript 7.0.2, Node.js 24 APIs, Zod 4.4.3, Vitest 4.1.10, esbuild 0.28.1, newline-delimited JSON with `fsync`, HTTPS JSON APIs

---

## Scope and fixed server contract

This is plan **02** only. It builds the continuously running main collector. It does not implement rankings, champion signals, device allocation, ordinary Windows execution, or the administrator UI.

The collector locks these server endpoints. The central-server plan must implement the same shapes:

- every endpoint below requires `Authorization: Bearer <collector bearer>`;
- `register-collector` writes the bearer only to an explicit owner-only handoff file, never stdout. The exact one-time bundle is:

```json
{"format":"champion-collector-credential-v1","collector_id":"collector-main-01","bearer":"SYNTHETIC_EXAMPLE_NOT_A_REAL_BEARER_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}
```

The bearer above is a schema-valid synthetic example only. The database stores only its SHA-256 digest. The collector accepts the bundle only through `--credential-handoff <non-secret-path>` or `--credential-stdin`; it never accepts a bearer in argv, an environment variable, a log, or a repository file.

This matches Plan 01 exactly: `register-collector` receives `--collector-id collector-main-01` and `--credential-handoff <owner-only-path>`, creates that path with `O_CREAT|O_EXCL` and mode `0600`, and returns no bearer. If the server and collector use different OS accounts or hosts, the operator must securely transfer the file so the final copy is owned by the collector account and remains mode `0600`; the collector rejects anything weaker.

- `POST /v1/collector/session`
  - request: `collector_id`, `namespace_version`;
  - response: `ack_seq`, `ack_event_key`, independent `history_anchor_event_key`, and `namespace_empty`;
  - `ack_event_key` proves the last journal receipt only and is never a history cursor；`history_anchor_event_key` is the latest eligible imported/current money-event anchor and is the only value used to resume historical backfill safely.
  - `namespace_empty` means no anchorable `current` bet/cancel exists; close/result/status/gap-only history is still empty for backfill purposes.
- `POST /v1/collector/events`
  - request: `collector_id`, `namespace_version`, `from_seq`, `to_seq`, ordered `records`;
  - response: `ack_seq`, the highest contiguous sequence transactionally committed;
  - replaying an identical `(collector_id, seq, digest)` is idempotent;
  - reusing a sequence with a different digest returns HTTP `409` and safe code `collector_sequence_conflict`.
- `POST /v1/collector/heartbeat`
  - request: current issue, page phase, countdown, last persisted sequence, capture health, and observation time;
  - the server expires executable signals after one second without a fresh heartbeat.

Production platform and server URLs must be HTTPS. Tests inject an in-memory API port rather than weakening that check.

## Locked file structure

Do not change this decomposition during implementation. The superseded legacy monitor has been removed and must not be restored or reused; historical migration remains a separate read-only plan.

```text
apps/champion_follow_platform/collector/
├── package.json                         # pinned scripts and dependencies
├── package-lock.json                    # npm lock generated from package.json
├── tsconfig.json                        # strict type checking
├── vitest.config.ts                     # Node test environment
├── scripts/
│   ├── build.mjs                        # main/preload/page-hook bundles
│   └── privacy-scan.mjs                 # built-artifact privacy scan
├── src/
│   ├── contracts.ts                     # strict local/server schemas
│   ├── canonical-json.ts                # deterministic digest input
│   ├── identity-store.ts                # safeStorage namespace key
│   ├── credential-store.ts              # one-time bearer import/safeStorage
│   ├── recovery-envelope.ts             # encrypted backup and restore
│   ├── journal.ts                       # append, recovery, ACK, compaction
│   ├── completeness.ts                  # cancellation netting/eligibility
│   ├── server-api.ts                    # HTTPS transport/redacted errors
│   ├── uploader.ts                      # ACK/retry/replay loops
│   ├── runtime.ts                       # capture-to-journal orchestration
│   ├── window-policy.ts                 # Electron security policy
│   ├── main.ts                          # Electron lifecycle/session
│   ├── preload.ts                       # isolated normalizer/IPC ingress
│   └── bridge/
│       ├── ffc-normalizer.ts            # whitelist parser/HMAC
│       └── page-hook.ts                 # room/history hook, no Node access
└── test/
    ├── contracts.test.ts
    ├── identity-store.test.ts
    ├── credential-store.test.ts
    ├── ffc-normalizer.test.ts
    ├── journal.test.ts
    ├── completeness.test.ts
    ├── uploader.test.ts
    ├── runtime.test.ts
    ├── window-policy.test.ts
    └── collector-integration.test.ts
```

The runtime directory is fixed too:

```text
<Electron userData>/main-collector-v1/
├── collector.lock                       # exclusive PID-only process lock
├── identity-key.enc                     # Electron safeStorage ciphertext
├── collector-credential.enc             # Electron safeStorage ciphertext
├── events.ndjson                        # append-only checksummed records
├── cursor.json                          # atomically replaced ACK cursor
└── events.compacting                    # non-authoritative temporary file
```

Only anonymized records may enter `events.ndjson`. Lock files, temporary files, and encrypted key material are never uploaded.

## Required invariants

1. A raw third-party actor identifier exists only in renderer memory while the isolated normalizer hashes it. It never crosses IPC or enters files, HTTP bodies, logs, errors, or snapshots.
2. Main returns capture success only after the complete journal line has been written and synchronized.
3. The uploader never advances `cursor.json` beyond a server ACK and never deletes an unacknowledged record.
4. A torn final line is repaired conservatively and creates `CAPTURE_GAP`; corruption before the final line fails closed.
5. An unattributed cancellation excludes that entire issue. No amount/time/order heuristic may assign it.
6. Attributable cancellation is applied before opposite-side validation: `bet → cancel → opposite bet` is valid; positive net on both sides is not.
7. Completeness requires a recovered history anchor, observed betting boundary, close, exactly five result digits, and no sticky fault.
8. Server/network failure does not stop local capture. Journal failure immediately stops capture and exposes only a safe code.
9. The server stores only the collector bearer digest. The collector starts no network loop until a one-time credential has been durably protected by `safeStorage` and its handoff file has been unlinked; plaintext bearer data never enters argv, environment variables, logs, journals, evidence, or source control.

### Task 1: Scaffold the collector and strict contracts

**Files:**
- Create: `apps/champion_follow_platform/collector/package.json`
- Create: `apps/champion_follow_platform/collector/package-lock.json`
- Create: `apps/champion_follow_platform/collector/tsconfig.json`
- Create: `apps/champion_follow_platform/collector/vitest.config.ts`
- Create: `apps/champion_follow_platform/collector/scripts/build.mjs`
- Create: `apps/champion_follow_platform/collector/src/canonical-json.ts`
- Create: `apps/champion_follow_platform/collector/src/contracts.ts`
- Test: `apps/champion_follow_platform/collector/test/contracts.test.ts`

- [ ] **Step 1: Create the pinned package configuration**

Create `package.json`:

```json
{
  "name": "@champion-follow/main-collector",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "main": "dist/main.mjs",
  "scripts": {
    "build": "node scripts/build.mjs",
    "start": "npm run build && electron dist/main.mjs",
    "test": "vitest run",
    "typecheck": "tsc --noEmit",
    "privacy:scan": "node scripts/privacy-scan.mjs"
  },
  "dependencies": { "zod": "4.4.3" },
  "devDependencies": {
    "@types/node": "24.13.3",
    "electron": "43.2.0",
    "esbuild": "0.28.1",
    "typescript": "7.0.2",
    "vitest": "4.1.10"
  }
}
```

Create `tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2023",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "lib": ["ES2023", "DOM"],
    "types": ["node", "electron"],
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": true,
    "noEmit": true,
    "skipLibCheck": true
  },
  "include": ["src/**/*.ts", "test/**/*.ts", "vitest.config.ts"]
}
```

Create `vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";
export default defineConfig({ test: { environment: "node", include: ["test/**/*.test.ts"], restoreMocks: true } });
```

Create `scripts/build.mjs`:

```js
import { build } from "esbuild";
await Promise.all([
  build({ entryPoints: ["src/main.ts"], outfile: "dist/main.mjs", bundle: true, platform: "node", format: "esm", external: ["electron"], sourcemap: true }),
  build({ entryPoints: ["src/preload.ts"], outfile: "dist/preload.cjs", bundle: true, platform: "node", format: "cjs", external: ["electron"], sourcemap: true }),
  build({ entryPoints: ["src/bridge/page-hook.ts"], outfile: "dist/page-hook.js", bundle: true, platform: "browser", format: "iife" })
]);
```

- [ ] **Step 2: Generate the exact lock file**

Run:

```bash
cd apps/champion_follow_platform/collector
npm install
```

Expected: exit code `0`; `package-lock.json` is created; npm reports no unresolved dependency.

- [ ] **Step 3: Write the failing contract tests**

Create `test/contracts.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { capturedEventSchema, eventBatchSchema, heartbeatSchema } from "../src/contracts.js";

const actorKey = "a".repeat(64);
const eventKey = "b".repeat(64);

describe("collector contracts", () => {
  it("accepts a canonical bet and rejects private fields", () => {
    const bet = capturedEventSchema.parse({
      kind: "BET", eventKey, actorKey, issue: "2607270001", play: "P1:大",
      amountMinor: "100", sourceMs: 1, receivedAtMs: 2, source: "realtime",
      parserVersion: "btcffc-1", namespaceVersion: "actor-hmac-v1"
    });
    expect(bet.kind).toBe("BET");
    expect(() => capturedEventSchema.parse({ ...bet, nickname: "forbidden" })).toThrow();
  });

  it("requires contiguous batch bounds and a strict heartbeat", () => {
    const event = capturedEventSchema.parse({
      kind: "CLOSE", eventKey, issue: "2607270001", sourceMs: 3, receivedAtMs: 4,
      source: "realtime", parserVersion: "btcffc-1", namespaceVersion: "actor-hmac-v1"
    });
    expect(eventBatchSchema.parse({
      collector_id: "collector-main-01", namespace_version: "actor-hmac-v1",
      from_seq: 7, to_seq: 7, records: [{ seq: 7, event, digest: "c".repeat(64) }]
    }).to_seq).toBe(7);
    expect(heartbeatSchema.parse({
      collector_id: "collector-main-01", issue: "2607270001", phase: "BETTING",
      countdown_ms: 900, observed_at_ms: 10, last_journal_seq: 7, capture_healthy: true
    }).capture_healthy).toBe(true);
  });
});
```

- [ ] **Step 4: Run the test and verify the missing-module failure**

Run: `cd apps/champion_follow_platform/collector && npx vitest run test/contracts.test.ts`

Expected: exit code `1` with an import error for `src/contracts.ts`.

- [ ] **Step 5: Implement deterministic JSON**

Create `src/canonical-json.ts`:

```ts
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const object = value as Record<string, unknown>;
  return `{${Object.keys(object).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(object[key])}`).join(",")}}`;
}
```

- [ ] **Step 6: Implement strict event, batch, ACK, and heartbeat schemas**

Create `src/contracts.ts`:

```ts
import { z } from "zod";

export const PLAYS = [
  "P1:大", "P1:小", "P1:单", "P1:双", "P1:质", "P1:合",
  "P2:大", "P2:小", "P2:单", "P2:双", "P2:质", "P2:合",
  "P3:大", "P3:小", "P3:单", "P3:双", "P3:质", "P3:合",
  "P4:大", "P4:小", "P4:单", "P4:双", "P4:质", "P4:合",
  "P5:大", "P5:小", "P5:单", "P5:双", "P5:质", "P5:合"
] as const;
const digest = z.string().regex(/^[0-9a-f]{64}$/);
export const eventKeySchema = z.string().max(80).regex(/^[0-9a-f]{64}(?::(?:block|close|[0-9]{1,15}))?$/);
const issue = z.string().regex(/^\d{8,16}$/);
const common = {
  eventKey: eventKeySchema, issue, sourceMs: z.number().int().nonnegative(),
  receivedAtMs: z.number().int().nonnegative(), source: z.enum(["realtime", "history"]),
  parserVersion: z.literal("btcffc-1"), namespaceVersion: z.literal("actor-hmac-v1")
};
const money = { ...common, actorKey: digest, play: z.enum(PLAYS), amountMinor: z.string().regex(/^[1-9]\d*$/) };

export const capturedEventSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("BET"), ...money }).strict(),
  z.object({ kind: z.literal("CANCEL"), ...money }).strict(),
  z.object({ kind: z.literal("CANCEL_UNATTRIBUTED"), ...common }).strict(),
  z.object({ kind: z.literal("CLOSE"), ...common }).strict(),
  z.object({ kind: z.literal("RESULT"), ...common, digits: z.tuple([
    z.number().int().min(0).max(9), z.number().int().min(0).max(9), z.number().int().min(0).max(9),
    z.number().int().min(0).max(9), z.number().int().min(0).max(9)
  ]) }).strict(),
  z.object({ kind: z.literal("CAPTURE_GAP"), ...common, reason: z.enum([
    "decrypt_failure", "history_anchor_missing", "journal_torn_tail", "journal_write_failed",
    "issue_uncertain", "cancel_overdraw", "opposite_net_conflict"
  ]) }).strict(),
  z.object({ kind: z.literal("ISSUE_STATUS"), ...common, complete: z.boolean(), reasons: z.array(z.string().regex(/^[a-z0-9_]+$/)).max(16) }).strict()
]);
export type CapturedEvent = z.infer<typeof capturedEventSchema>;

export const journalRecordSchema = z.object({ seq: z.number().int().positive(), event: capturedEventSchema, digest }).strict();
export type JournalRecord = z.infer<typeof journalRecordSchema>;
export const eventBatchSchema = z.object({
  collector_id: z.string().regex(/^collector-[a-z0-9-]{3,64}$/), namespace_version: z.literal("actor-hmac-v1"),
  from_seq: z.number().int().positive(), to_seq: z.number().int().positive(),
  records: z.array(journalRecordSchema).min(1).max(200)
}).strict().superRefine((batch, context) => {
  if (batch.records[0]?.seq !== batch.from_seq || batch.records.at(-1)?.seq !== batch.to_seq)
    context.addIssue({ code: "custom", message: "batch bounds mismatch" });
  if (batch.records.some((row, index) => row.seq !== batch.from_seq + index))
    context.addIssue({ code: "custom", message: "batch is not contiguous" });
});
export const ackSchema = z.object({ ack_seq: z.number().int().nonnegative() }).strict();
export const sessionResponseSchema = z.object({
  ack_seq: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),
  ack_event_key: eventKeySchema.nullable(),
  history_anchor_event_key: eventKeySchema.nullable(),
  namespace_empty: z.boolean(),
}).strict();
export const heartbeatSchema = z.object({
  collector_id: z.string().regex(/^collector-[a-z0-9-]{3,64}$/), issue: issue.nullable(),
  phase: z.enum(["BETTING", "CLOSED", "UNKNOWN"]), countdown_ms: z.number().int().nonnegative(),
  observed_at_ms: z.number().int().nonnegative(), last_journal_seq: z.number().int().nonnegative(),
  capture_healthy: z.boolean()
}).strict();
export type Heartbeat = z.infer<typeof heartbeatSchema>;
```

- [ ] **Step 7: Verify contracts and type consistency**

Run:

```bash
cd apps/champion_follow_platform/collector
npx vitest run test/contracts.test.ts
npm run typecheck
```

Expected: `2 passed`; TypeScript exits `0`.

- [ ] **Step 8: Commit the scaffold**

```bash
git add apps/champion_follow_platform/collector/package.json apps/champion_follow_platform/collector/package-lock.json apps/champion_follow_platform/collector/tsconfig.json apps/champion_follow_platform/collector/vitest.config.ts apps/champion_follow_platform/collector/scripts/build.mjs apps/champion_follow_platform/collector/src/canonical-json.ts apps/champion_follow_platform/collector/src/contracts.ts apps/champion_follow_platform/collector/test/contracts.test.ts
git commit -m "feat(collector): lock anonymous event contract"
```

### Task 2: Persist one encrypted identity namespace and collector credential

**Files:**
- Create: `apps/champion_follow_platform/collector/src/identity-store.ts`
- Create: `apps/champion_follow_platform/collector/src/credential-store.ts`
- Create: `apps/champion_follow_platform/collector/src/recovery-envelope.ts`
- Test: `apps/champion_follow_platform/collector/test/identity-store.test.ts`
- Test: `apps/champion_follow_platform/collector/test/credential-store.test.ts`

- [ ] **Step 1: Write failing stable-key and recovery tests**

Create `test/identity-store.test.ts` with an injected deterministic encryption port. Test three behaviors:

```ts
import { generateKeyPairSync } from "node:crypto";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { IdentityStore } from "../src/identity-store.js";

const encryption = {
  isEncryptionAvailable: () => true,
  encryptString: (value: string) => Buffer.from(`sealed:${value}`, "utf8"),
  decryptString: (value: Buffer) => value.toString("utf8").slice(7)
};

describe("IdentityStore", () => {
  it("keeps one stable key without writing plaintext", async () => {
    const root = await mkdtemp(join(tmpdir(), "collector-id-"));
    const store = new IdentityStore(join(root, "identity-key.enc"), encryption);
    const first = await store.loadOrCreate();
    expect(await store.loadOrCreate()).toEqual(first);
    expect((await readFile(join(root, "identity-key.enc"))).includes(first)).toBe(false);
    await rm(root, { recursive: true });
  });

  it("restores the same namespace from RSA-OAEP", async () => {
    const root = await mkdtemp(join(tmpdir(), "collector-restore-"));
    const pair = generateKeyPairSync("rsa", { modulusLength: 2048 });
    const first = new IdentityStore(join(root, "first.enc"), encryption);
    const key = await first.loadOrCreate();
    const envelope = await first.exportRecoveryEnvelope(pair.publicKey.export({ type: "spki", format: "pem" }).toString());
    const restored = new IdentityStore(join(root, "restored.enc"), encryption);
    await restored.restoreRecoveryEnvelope(envelope, pair.privateKey.export({ type: "pkcs8", format: "pem" }).toString());
    expect(await restored.loadOrCreate()).toEqual(key);
    expect(JSON.stringify(envelope)).not.toContain(key.toString("base64"));
    await rm(root, { recursive: true });
  });

  it("fails closed without OS encryption", async () => {
    const root = await mkdtemp(join(tmpdir(), "collector-no-crypto-"));
    const store = new IdentityStore(join(root, "identity.enc"), { ...encryption, isEncryptionAvailable: () => false });
    await expect(store.loadOrCreate()).rejects.toThrow("identity_encryption_unavailable");
    await rm(root, { recursive: true });
  });
});
```

- [ ] **Step 2: Write failing one-time credential import tests**

Create `test/credential-store.test.ts`. The fixture value is synthetic; do not substitute a real bearer:

```ts
import { Readable } from "node:stream";
import { chmod, lstat, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { CollectorCredentialStore, parseCredentialImportArgs } from "../src/credential-store.js";

const fixtureBearer = `synthetic_fixture_${"x".repeat(48)}`;
const bundle = {
  format: "champion-collector-credential-v1" as const,
  collector_id: "collector-main-01",
  bearer: fixtureBearer
};
const encryption = {
  isEncryptionAvailable: () => true,
  encryptString: (value: string) => Buffer.from(value, "utf8").reverse(),
  decryptString: (value: Buffer) => Buffer.from(value).reverse().toString("utf8")
};

describe("CollectorCredentialStore", () => {
  it("imports a 0600 handoff, persists ciphertext, and unlinks the source", async () => {
    if (process.platform === "win32") return; // Windows bootstrap uses --credential-stdin.
    const root = await mkdtemp(join(tmpdir(), "collector-credential-"));
    const source = join(root, "handoff.json");
    const target = join(root, "collector-credential.enc");
    await writeFile(source, JSON.stringify(bundle), { mode: 0o600 });
    const store = new CollectorCredentialStore(target, encryption);
    expect(await store.importFromFile(source)).toEqual(bundle);
    await expect(lstat(source)).rejects.toMatchObject({ code: "ENOENT" });
    expect((await readFile(target, "utf8"))).not.toContain(fixtureBearer);
    expect(await store.load()).toEqual(bundle);
    await rm(root, { recursive: true });
  });

  it("imports from stdin without accepting a token argument", async () => {
    const root = await mkdtemp(join(tmpdir(), "collector-stdin-"));
    const store = new CollectorCredentialStore(join(root, "collector-credential.enc"), encryption);
    await store.importFromStdin(Readable.from([JSON.stringify(bundle)]));
    expect(await store.load()).toEqual(bundle);
    await rm(root, { recursive: true });
  });

  it("accepts only a handoff path or stdin mode in argv", () => {
    expect(parseCredentialImportArgs(["--credential-handoff", "/private/handoff.json"]))
      .toEqual({ kind: "file", path: "/private/handoff.json" });
    expect(parseCredentialImportArgs(["--credential-stdin"])).toEqual({ kind: "stdin" });
    expect(parseCredentialImportArgs([])).toEqual({ kind: "stored" });
    expect(() => parseCredentialImportArgs(["--bearer", fixtureBearer]))
      .toThrow("collector_credential_argument_forbidden");
    expect(() => parseCredentialImportArgs(["--credential-handoff", "a", "--credential-stdin"]))
      .toThrow("collector_credential_argument_invalid");
  });

  it("rejects a group-readable handoff before reading it", async () => {
    if (process.platform === "win32") return;
    const root = await mkdtemp(join(tmpdir(), "collector-mode-"));
    const source = join(root, "handoff.json");
    await writeFile(source, JSON.stringify(bundle), { mode: 0o600 });
    await chmod(source, 0o640);
    const store = new CollectorCredentialStore(join(root, "collector-credential.enc"), encryption);
    await expect(store.importFromFile(source)).rejects.toThrow("collector_credential_permissions_invalid");
    await rm(root, { recursive: true });
  });

  it("keeps the owner-only source for retry when safeStorage persistence fails", async () => {
    if (process.platform === "win32") return;
    const root = await mkdtemp(join(tmpdir(), "collector-retry-"));
    const source = join(root, "handoff.json");
    await writeFile(source, JSON.stringify(bundle), { mode: 0o600 });
    const broken = { ...encryption, encryptString: () => { throw new Error(fixtureBearer); } };
    const store = new CollectorCredentialStore(join(root, "collector-credential.enc"), broken);
    await expect(store.importFromFile(source)).rejects.toThrow("collector_credential_store_failed");
    expect(await readFile(source, "utf8")).toContain(fixtureBearer);
    await rm(root, { recursive: true });
  });

  it("rejects Electron's unprotected Linux basic_text backend", async () => {
    const root = await mkdtemp(join(tmpdir(), "collector-basic-text-"));
    const unprotected = { ...encryption, getSelectedStorageBackend: () => "basic_text" };
    const store = new CollectorCredentialStore(join(root, "collector-credential.enc"), unprotected);
    await expect(store.importFromStdin(Readable.from([JSON.stringify(bundle)])))
      .rejects.toThrow("collector_credential_encryption_unavailable");
    await rm(root, { recursive: true });
  });
});
```

The two early `return` branches are platform assertions rather than skipped tests: POSIX file mode is enforced by the file importer; Windows must use stdin because Node mode bits cannot prove a restrictive Windows ACL.

- [ ] **Step 3: Run both tests and verify missing-module failures**

Run:

```bash
cd apps/champion_follow_platform/collector
npx vitest run test/identity-store.test.ts test/credential-store.test.ts
```

Expected: exit code `1` with missing `identity-store` and `credential-store` imports.

- [ ] **Step 4: Implement the RSA-OAEP recovery envelope**

Create `src/recovery-envelope.ts`:

```ts
import { privateDecrypt, publicEncrypt } from "node:crypto";
import { z } from "zod";
const schema = z.object({
  format: z.literal("champion-collector-identity-backup-v1"),
  namespaceVersion: z.literal("actor-hmac-v1"),
  ciphertext: z.string().regex(/^[A-Za-z0-9+/]+={0,2}$/)
}).strict();
export type RecoveryEnvelope = z.infer<typeof schema>;

export function encryptRecoveryKey(key: Buffer, publicKeyPem: string): RecoveryEnvelope {
  const plaintext = Buffer.from(JSON.stringify({ namespaceVersion: "actor-hmac-v1", key: key.toString("base64") }));
  try {
    const ciphertext = publicEncrypt({ key: publicKeyPem, oaepHash: "sha256" }, plaintext);
    return { format: "champion-collector-identity-backup-v1", namespaceVersion: "actor-hmac-v1", ciphertext: ciphertext.toString("base64") };
  } finally { plaintext.fill(0); }
}

export function decryptRecoveryKey(value: unknown, privateKeyPem: string): Buffer {
  const envelope = schema.parse(value);
  const plaintext = privateDecrypt({ key: privateKeyPem, oaepHash: "sha256" }, Buffer.from(envelope.ciphertext, "base64"));
  try {
    const parsed = JSON.parse(plaintext.toString()) as { namespaceVersion?: unknown; key?: unknown };
    if (parsed.namespaceVersion !== "actor-hmac-v1" || typeof parsed.key !== "string") throw new Error("identity_recovery_invalid");
    const key = Buffer.from(parsed.key, "base64");
    if (key.length !== 32) throw new Error("identity_recovery_invalid");
    return key;
  } finally { plaintext.fill(0); }
}
```

- [ ] **Step 5: Implement atomic safeStorage-backed identity persistence**

Create `src/identity-store.ts`:

```ts
import { randomBytes } from "node:crypto";
import { chmod, mkdir, open, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { decryptRecoveryKey, encryptRecoveryKey, type RecoveryEnvelope } from "./recovery-envelope.js";

export interface EncryptionPort {
  isEncryptionAvailable(): boolean;
  encryptString(value: string): Buffer;
  decryptString(value: Buffer): string;
  getSelectedStorageBackend?(): string;
}

export class IdentityStore {
  constructor(private readonly path: string, private readonly encryption: EncryptionPort) {}

  async loadOrCreate(): Promise<Buffer> {
    if (!this.encryption.isEncryptionAvailable() || this.encryption.getSelectedStorageBackend?.() === "basic_text") throw new Error("identity_encryption_unavailable");
    await mkdir(dirname(this.path), { recursive: true, mode: 0o700 });
    try {
      const key = Buffer.from(this.encryption.decryptString(await readFile(this.path)), "base64");
      if (key.length !== 32) throw new Error("identity_key_invalid");
      return key;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
    const key = randomBytes(32);
    await this.writeEncrypted(key);
    return key;
  }

  async exportRecoveryEnvelope(publicKeyPem: string): Promise<RecoveryEnvelope> {
    const key = await this.loadOrCreate();
    try { return encryptRecoveryKey(key, publicKeyPem); }
    finally { key.fill(0); }
  }

  async restoreRecoveryEnvelope(envelope: unknown, privateKeyPem: string): Promise<void> {
    const key = decryptRecoveryKey(envelope, privateKeyPem);
    try { await this.writeEncrypted(key); }
    finally { key.fill(0); }
  }

  private async writeEncrypted(key: Buffer): Promise<void> {
    if (!this.encryption.isEncryptionAvailable() || this.encryption.getSelectedStorageBackend?.() === "basic_text") throw new Error("identity_encryption_unavailable");
    const temporary = `${this.path}.new`;
    await rm(temporary, { force: true });
    const encrypted = this.encryption.encryptString(key.toString("base64"));
    await writeFile(temporary, encrypted, { mode: 0o600, flag: "wx" });
    await chmod(temporary, 0o600);
    const handle = await open(temporary, "r");
    try { await handle.sync(); } finally { await handle.close(); }
    await rename(temporary, this.path);
  }
}
```

- [ ] **Step 6: Implement one-time bearer import and safeStorage persistence**

Create `src/credential-store.ts`:

```ts
import { constants } from "node:fs";
import { chmod, mkdir, open, readFile, rename, rm } from "node:fs/promises";
import type { FileHandle } from "node:fs/promises";
import { dirname } from "node:path";
import { z } from "zod";
import type { EncryptionPort } from "./identity-store.js";

const MAX_CREDENTIAL_BYTES = 4096;
const schema = z.object({
  format: z.literal("champion-collector-credential-v1"),
  collector_id: z.string().regex(/^collector-[a-z0-9-]{3,64}$/),
  bearer: z.string().min(48).max(256).regex(/^[A-Za-z0-9_-]+$/)
}).strict();
export type CollectorCredential = z.infer<typeof schema>;
export type CredentialImportMode =
  | { kind: "stored" }
  | { kind: "file"; path: string }
  | { kind: "stdin" };

function safeError(code: string): Error { return new Error(code); }

function hasSystemProtection(encryption: EncryptionPort): boolean {
  return encryption.isEncryptionAvailable() && encryption.getSelectedStorageBackend?.() !== "basic_text";
}

export function parseCredentialImportArgs(argv: string[]): CredentialImportMode {
  if (argv.some((value) => value === "--bearer" || value.startsWith("--bearer="))) {
    throw safeError("collector_credential_argument_forbidden");
  }
  const fileAt = argv.indexOf("--credential-handoff");
  const stdinAt = argv.indexOf("--credential-stdin");
  if ((fileAt >= 0 && stdinAt >= 0) || argv.lastIndexOf("--credential-handoff") !== fileAt || argv.lastIndexOf("--credential-stdin") !== stdinAt) {
    throw safeError("collector_credential_argument_invalid");
  }
  if (fileAt >= 0) {
    const path = argv[fileAt + 1];
    if (!path || path.startsWith("--")) throw safeError("collector_credential_argument_invalid");
    return { kind: "file", path };
  }
  return stdinAt >= 0 ? { kind: "stdin" } : { kind: "stored" };
}

function parseCredential(raw: Buffer): CollectorCredential {
  try { return schema.parse(JSON.parse(raw.toString("utf8"))); }
  catch { throw safeError("collector_credential_invalid"); }
}

export class CollectorCredentialStore {
  constructor(private readonly path: string, private readonly encryption: EncryptionPort) {}

  async load(): Promise<CollectorCredential> {
    if (!hasSystemProtection(this.encryption)) throw safeError("collector_credential_encryption_unavailable");
    let encrypted: Buffer;
    try { encrypted = await readFile(this.path); }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") throw safeError("collector_credential_missing");
      throw safeError("collector_credential_read_failed");
    }
    try { return schema.parse(JSON.parse(this.encryption.decryptString(encrypted))); }
    catch { throw safeError("collector_credential_invalid"); }
    finally { encrypted.fill(0); }
  }

  async importFromFile(sourcePath: string): Promise<CollectorCredential> {
    if (process.platform === "win32") throw safeError("collector_credential_use_stdin_on_windows");
    const noFollow = (constants as unknown as Record<string, number>).O_NOFOLLOW ?? 0;
    let handle: FileHandle | undefined;
    let raw: Buffer | undefined;
    try {
      handle = await open(sourcePath, constants.O_RDONLY | noFollow);
      const stat = await handle.stat();
      const wrongOwner = typeof process.getuid === "function" && stat.uid !== process.getuid();
      if (!stat.isFile() || wrongOwner || (stat.mode & 0o077) !== 0) {
        throw safeError("collector_credential_permissions_invalid");
      }
      if (stat.size < 1 || stat.size > MAX_CREDENTIAL_BYTES) throw safeError("collector_credential_invalid");
      raw = await handle.readFile();
      const credential = parseCredential(raw);
      await this.persist(credential);
      await handle.close();
      handle = undefined;
      try { await rm(sourcePath); }
      catch { throw safeError("collector_credential_source_delete_failed"); }
      return credential;
    } catch (error) {
      const code = (error as Error).message;
      if (code.startsWith("collector_credential_")) throw error;
      throw safeError("collector_credential_input_failed");
    } finally {
      raw?.fill(0);
      if (handle) await handle.close().catch(() => undefined);
    }
  }

  async importFromStdin(input: AsyncIterable<string | Uint8Array>): Promise<CollectorCredential> {
    const chunks: Buffer[] = [];
    let size = 0;
    try {
      for await (const chunk of input) {
        const bytes = Buffer.from(chunk);
        chunks.push(bytes);
        size += bytes.length;
        if (size > MAX_CREDENTIAL_BYTES) throw safeError("collector_credential_invalid");
      }
      const raw = Buffer.concat(chunks);
      try {
        const credential = parseCredential(raw);
        await this.persist(credential);
        return credential;
      } finally { raw.fill(0); }
    } finally { for (const chunk of chunks) chunk.fill(0); }
  }

  private async persist(value: CollectorCredential): Promise<void> {
    if (!hasSystemProtection(this.encryption)) throw safeError("collector_credential_encryption_unavailable");
    await mkdir(dirname(this.path), { recursive: true, mode: 0o700 });
    let exists = true;
    try { await readFile(this.path); }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") exists = false;
      else throw safeError("collector_credential_store_failed");
    }
    if (exists) throw safeError("collector_credential_already_initialized");

    let encrypted: Buffer;
    try { encrypted = this.encryption.encryptString(JSON.stringify(value)); }
    catch { throw safeError("collector_credential_store_failed"); }
    const temporary = `${this.path}.new`;
    try {
      await rm(temporary, { force: true });
      const handle = await open(temporary, "wx", 0o600);
      try { await handle.writeFile(encrypted); await handle.sync(); }
      finally { await handle.close(); }
      await chmod(temporary, 0o600);
      await rename(temporary, this.path);
    } catch {
      await rm(temporary, { force: true }).catch(() => undefined);
      throw safeError("collector_credential_store_failed");
    } finally { encrypted.fill(0); }
  }
}
```

The store deliberately does not implement bearer rotation or export. A successful file import returns only after ciphertext `fsync`/rename and source unlink; a failed `safeStorage` write leaves the owner-only handoff available for an explicit retry. The caller must expose only the safe error code.

- [ ] **Step 7: Verify identity and credential behavior**

Run:

```bash
cd apps/champion_follow_platform/collector
npx vitest run test/identity-store.test.ts test/credential-store.test.ts
npm run typecheck
```

Expected: `9 passed`; type check exits `0`; no test output contains `synthetic_fixture_`.

- [ ] **Step 8: Commit both OS-protected stores**

```bash
git add apps/champion_follow_platform/collector/src/identity-store.ts apps/champion_follow_platform/collector/src/credential-store.ts apps/champion_follow_platform/collector/src/recovery-envelope.ts apps/champion_follow_platform/collector/test/identity-store.test.ts apps/champion_follow_platform/collector/test/credential-store.test.ts
git commit -m "feat(collector): protect namespace and collector credential"
```

### Task 3: Normalize Btcffc inside isolated preload

**Files:**
- Create: `apps/champion_follow_platform/collector/src/bridge/ffc-normalizer.ts`
- Create: `apps/champion_follow_platform/collector/src/bridge/page-hook.ts`
- Create: `apps/champion_follow_platform/collector/src/preload.ts`
- Test: `apps/champion_follow_platform/collector/test/ffc-normalizer.test.ts`

- [ ] **Step 1: Write privacy-first normalizer tests**

Create `test/ffc-normalizer.test.ts`. Use a fixed 32-byte test key and assert:

```ts
import { describe, expect, it } from "vitest";
import { createFfcNormalizer } from "../src/bridge/ffc-normalizer.js";
const normalize = createFfcNormalizer(Buffer.alloc(32, 7), () => 2000);

describe("Btcffc normalizer", () => {
  it("hashes actor/message identities and keeps only supported plays", async () => {
    const rows = await normalize({
      idClient: "raw-message-marker", from: "shared-robot", time: 1000,
      text: { ext: { isRobot: "1", uid: "raw-player-marker", ext: {
        model: "Btcffc", type: "1", serial: "2607270001",
        items: [{ title: "第一球", items: [{ title: "大", money: "10.50" }, { title: "7", money: "99" }] }]
      } } }
    }, "realtime");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ kind: "BET", play: "P1:大", amountMinor: "1050" });
    expect(rows[0]?.actorKey).toMatch(/^[0-9a-f]{64}$/);
    expect(JSON.stringify(rows)).not.toMatch(/raw-player-marker|raw-message-marker|shared-robot/);
  });

  it("emits exact cancel only with actor, play, and amount", async () => {
    const rows = await normalize({
      idClient: "cancel-1", time: 1001,
      text: { ext: { isRobot: "1", uid: "player-1", ext: {
        model: "Btcffc", type: "2", serial: "2607270001", tipType: "1,b", title: "已取消",
        items: [{ title: "第一球", items: [{ title: "大", money: "10.50" }] }]
      } } }
    }, "realtime");
    expect(rows[0]).toMatchObject({ kind: "CANCEL", play: "P1:大", amountMinor: "1050" });
  });

  it("never guesses ambiguous cancellation ownership", async () => {
    const rows = await normalize({
      idClient: "cancel-2", time: 1002,
      text: { ext: { isRobot: "1", ext: { model: "Btcffc", type: "2", serial: "2607270001", title: "玩家已撤单" } } }
    }, "history");
    expect(rows[0]).toMatchObject({ kind: "CANCEL_UNATTRIBUTED", issue: "2607270001" });
  });

  it("requires an exact five-digit result", async () => {
    const valid = await normalize({ idClient: "result-1", time: 1003, text: { ext: { isRobot: "1", ext: { model: "Btcffc", type: "4", serial: "2607270001", result: [1,2,3,4,5] } } } }, "realtime");
    const invalid = await normalize({ idClient: "result-2", time: 1004, text: { ext: { isRobot: "1", ext: { model: "Btcffc", type: "4", serial: "2607270002", result: [1,2,3] } } } }, "realtime");
    expect(valid[0]).toMatchObject({ kind: "RESULT", digits: [1,2,3,4,5] });
    expect(invalid).toEqual([]);
  });
});
```

- [ ] **Step 2: Run the test and verify failure**

Run: `cd apps/champion_follow_platform/collector && npx vitest run test/ffc-normalizer.test.ts`

Expected: exit code `1` with a missing normalizer import.

- [ ] **Step 3: Implement whitelist parsing and HMAC domain separation**

Create `src/bridge/ffc-normalizer.ts`. It must accept only `Btcffc`, official types `1/2/4`, positions one through five, and sides `大/小/单/双/质/合`. Use this exact amount conversion:

```ts
function toMinor(value: unknown): string | null {
  const match = String(value ?? "").match(/^(0|[1-9]\d*)(?:\.(\d{1,2}))?$/);
  if (!match) return null;
  const minor = BigInt(match[1] ?? "0") * 100n + BigInt((match[2] ?? "").padEnd(2, "0") || "0");
  return minor > 0n ? minor.toString() : null;
}
```

Use `HMAC-SHA256(key, "actor|" + rawActor)` for `actorKey` and `HMAC-SHA256(key, "event|" + stableMessageId + "|" + itemIndex)` for `eventKey`. If no stable message ID exists, hash a canonical in-memory fallback containing source time, type, actor, and normalized item; never emit that fallback input. Construct only schema fields, validate every return through `capturedEventSchema.parse`, and return `CANCEL_UNATTRIBUTED` whenever actor, play, or amount is absent. Numeric plays, 龙虎/和, profile balances, and all unrecognized messages return no event. The normalizer copies the namespace key internally before preload zeroes its temporary IPC buffer.

- [ ] **Step 4: Verify the four normalizer cases**

Run: `cd apps/champion_follow_platform/collector && npx vitest run test/ffc-normalizer.test.ts`

Expected: `4 passed`.

- [ ] **Step 5: Implement the main-world room/history hook**

Create `src/bridge/page-hook.ts` with a fixed marker, synchronous preservation of the original callback, 100 ms mount polling, and history pages limited to 100. When the SDK replaces `window.chatroom`, restore the previous callback before wrapping the replacement; a mount replacement test must prove there is exactly one wrapper per room:

```ts
const MARKER = "champion-follow-public-room-v1";
let mounted: object | null = null;
function emit(kind: string, payload: unknown): void {
  window.postMessage({ marker: MARKER, kind, payload }, location.origin);
}
function install(): void {
  const room = (window as unknown as { chatroom?: { options?: { onmsgs?: (messages: unknown[]) => unknown } } }).chatroom;
  if (!room?.options || typeof room.options.onmsgs !== "function" || room === mounted) return;
  const original = room.options.onmsgs;
  room.options.onmsgs = function (messages: unknown[]): unknown {
    const result = original.call(this, messages);
    queueMicrotask(() => emit("messages", { origin: "realtime", messages }));
    return result;
  };
  mounted = room;
}
window.addEventListener("message", (event) => {
  if (event.source !== window || event.origin !== location.origin) return;
  const data = event.data as { marker?: unknown; kind?: unknown; timetag?: unknown };
  if (data.marker !== MARKER || data.kind !== "pull-history") return;
  const room = (window as unknown as { chatroom?: { getHistoryMsgs?: (options: Record<string, unknown>) => void } }).chatroom;
  if (typeof room?.getHistoryMsgs !== "function") return emit("history-error", "history_unavailable");
  room.getHistoryMsgs({
    timetag: Number(data.timetag), limit: 100, reverse: false, msgTypes: ["text"],
    done(error: unknown, result: { msgs?: unknown[] } | unknown[]) {
      if (error) return emit("history-error", "history_failed");
      emit("messages", { origin: "history", messages: Array.isArray(result) ? result : result.msgs ?? [] });
    }
  });
});
setInterval(install, 100);
install();
```

The hook may not import Electron, receive the namespace key, persist data, or call the server.

- [ ] **Step 6: Implement isolated preload ingress**

Create `src/preload.ts`. Request the namespace key once using `ipcRenderer.invoke("collector:identity")`, create the normalizer, and immediately zero the temporary key buffer. Listen only for same-window/same-origin messages with the fixed marker. Normalize each raw message and call `ipcRenderer.invoke("collector:append", strictEvents)`; do not expose any API through `contextBridge`.

A decrypt/parse rejection with a known issue emits `CAPTURE_GAP(decrypt_failure)`. An unknown issue sends only `ipcRenderer.send("collector:unsafe-state", "issue_uncertain")`, which main treats as failed closed until a fresh boundary and history anchor.

- [ ] **Step 7: Type-check and bundle the two renderer worlds**

Run:

```bash
cd apps/champion_follow_platform/collector
npx vitest run test/ffc-normalizer.test.ts
npm run typecheck
npx esbuild src/preload.ts --outfile=dist/preload.cjs --bundle --platform=node --format=cjs --external:electron
npx esbuild src/bridge/page-hook.ts --outfile=dist/page-hook.js --bundle --platform=browser --format=iife
```

Expected: `4 passed`; type check exits `0`; both bundle commands exit `0`.

- [ ] **Step 8: Commit the anonymization boundary**

```bash
git add apps/champion_follow_platform/collector/src/bridge/ffc-normalizer.ts apps/champion_follow_platform/collector/src/bridge/page-hook.ts apps/champion_follow_platform/collector/src/preload.ts apps/champion_follow_platform/collector/test/ffc-normalizer.test.ts
git commit -m "feat(collector): anonymize ffc events before ipc"
```

### Task 4: Add the single-writer append-only local journal

**Files:**
- Create: `apps/champion_follow_platform/collector/src/journal.ts`
- Test: `apps/champion_follow_platform/collector/test/journal.test.ts`

- [ ] **Step 1: Write durability and recovery tests**

Create `test/journal.test.ts` with temporary directories and valid `CLOSE` events. Cover:

- append sequences 1 and 2, close, reopen, and assert next sequence 3;
- ACK sequence 1, compact, and assert sequence 2 remains replayable;
- append an unterminated partial final JSON line, reopen, and assert `repairedTail` is true and no partial record is fabricated;
- modify a digest in a complete middle line and assert open rejects with safe error `journal_corrupt`;
- create a second writer and assert it fails with `journal_locked` while the first lock is live.

- [ ] **Step 2: Run journal tests and verify failure**

Run: `cd apps/champion_follow_platform/collector && npx vitest run test/journal.test.ts`

Expected: exit code `1` with a missing journal import.

- [ ] **Step 3: Implement checksummed append and crash-safe cursor**

Create `src/journal.ts`. The core implementation must use `sha256(canonicalJson({seq,event}))`, append one newline-terminated JSON object through a file handle, call `sync()` before returning, and store `{ackSeq, ackEventKey}` using temporary-file `sync()` followed by atomic rename. Wrap JSON, Zod, and digest failures while reading any complete line as safe error `journal_corrupt`; only an unterminated final line may be truncated. Expose:

```ts
export interface JournalOpenResult { repairedTail: boolean; lastSeq: number; acknowledgedSeq: number; acknowledgedEventKey: string | null }
export class AppendOnlyJournal {
  readonly repairedTail: boolean;
  async start(): Promise<JournalOpenResult>;
  get lastSeq(): number;
  get acknowledgedSeq(): number;
  get acknowledgedEventKey(): string | null;
  async append(event: CapturedEvent): Promise<JournalRecord>;
  pending(limit?: number): JournalRecord[];
  async advanceAck(seq: number): Promise<void>;
  async compact(): Promise<void>;
  async close(): Promise<void>;
}
```

The journal must create `collector.lock` with `wx` and PID-only content. On `EEXIST`, read the PID and use `process.kill(pid, 0)`; an alive PID returns `journal_locked`, while a dead PID is removed and acquired. A malformed cursor, malformed complete line, digest mismatch, sequence gap, or sequence below `ackSeq + 1` returns `journal_corrupt`. Only an unterminated final line may be truncated, and `repairedTail` must be exposed to runtime.

Compaction writes only records with `seq > ackSeq` to `events.compacting`, synchronizes it, atomically renames it over `events.ndjson`, and leaves the ACK cursor untouched. An orphaned compacting file is deleted at startup before reading the authoritative journal.

- [ ] **Step 4: Run all journal cases**

Run:

```bash
cd apps/champion_follow_platform/collector
npx vitest run test/journal.test.ts
```

Expected: all five journal cases pass.

- [ ] **Step 5: Make repair visible to completeness**

Add a test that, after a torn-tail reopen, runtime appends a schema-valid `CAPTURE_GAP` with reason `journal_torn_tail` before accepting later events. Reuse the last complete journal issue only when a freshly observed page boundary proves it is still the current open issue; otherwise remain failed closed until the next explicit betting boundary establishes the affected current issue, append the gap first, and only then accept later events. Assert the derived event gets the next sequence, has a deterministic 64-hex event key, and is included in the uploader batch; never infer an issue number from the prior row alone, wall time, or amount data.

- [ ] **Step 6: Verify type and contract compatibility**

Run: `cd apps/champion_follow_platform/collector && npx vitest run test/journal.test.ts test/contracts.test.ts && npm run typecheck`

Expected: focused tests pass and type check exits `0`.

- [ ] **Step 7: Commit the durable queue**

```bash
git add apps/champion_follow_platform/collector/src/journal.ts apps/champion_follow_platform/collector/test/journal.test.ts
git commit -m "feat(collector): add durable contiguous event journal"
```

### Task 5: Apply cancellations and decide issue completeness

**Files:**
- Create: `apps/champion_follow_platform/collector/src/completeness.ts`
- Test: `apps/champion_follow_platform/collector/test/completeness.test.ts`

- [ ] **Step 1: Write the valid switch test**

Create `test/completeness.test.ts` with valid actor events. Mark the betting boundary and recovered history anchor first, then feed:

```text
BET P1:大 1000
CANCEL P1:大 1000
BET P1:小 500
CLOSE
RESULT [1,2,3,4,5]
```

Assert `evaluate("2607270001")` returns `{ complete: true, reasons: [] }`.

- [ ] **Step 2: Add exclusion cases**

Add tests asserting `complete: false` for each case:

- `CANCEL_UNATTRIBUTED` → `unattributed_cancel`;
- cancellation larger than the current matching bet → `cancel_overdraw`;
- positive net `P1:大` and `P1:小` after cancellations → `opposite_net_conflict`;
- close without result → `result_missing`;
- result without close → `close_missing`;
- no recovered anchor → `history_anchor_missing`;
- `CAPTURE_GAP` → its reason remains sticky.

- [ ] **Step 3: Run tests and verify missing implementation**

Run: `cd apps/champion_follow_platform/collector && npx vitest run test/completeness.test.ts`

Expected: exit code `1` with a missing completeness import.

- [ ] **Step 4: Implement exact minor-unit netting**

Create `src/completeness.ts` with an `IssueCompletenessTracker` using `bigint` amounts and one state per issue. Key net amounts by `actorKey|play`; add BET, subtract only matching CANCEL, and never guess an unattributed cancel. Use this fixed opposite map:

```ts
const OPPOSITE: Record<string, string> = {
  大: "小", 小: "大", 单: "双", 双: "单", 质: "合", 合: "质"
};
```

After every BET/CANCEL, compare the same actor, ball, and pair. A positive amount on both opposite plays adds `opposite_net_conflict`. Negative cancellation adds `cancel_overdraw` and does not alter the prior valid net. `CLOSE` and `RESULT` set boundaries; result shape is already validated by the event schema.

Export:

```ts
export interface CompletenessResult { complete: boolean; reasons: readonly string[] }
export class IssueCompletenessTracker {
  observeBetting(issue: string): void;
  markHistoryAnchorRecovered(issue: string): void;
  ingest(event: CapturedEvent): void;
  evaluate(issue: string): CompletenessResult;
  statusTransition(issue: string, common: Pick<CapturedEvent, "eventKey" | "sourceMs" | "receivedAtMs" | "source" | "parserVersion" | "namespaceVersion">): Extract<CapturedEvent, { kind: "ISSUE_STATUS" }> | null;
}
```

`evaluate` adds sorted missing-boundary reasons `history_anchor_missing`, `betting_boundary_missing`, `close_missing`, and `result_missing`; a reason is never removed during the process lifetime.

- [ ] **Step 5: Run completeness tests**

Run: `cd apps/champion_follow_platform/collector && npx vitest run test/completeness.test.ts`

Expected: all valid and invalid cases pass.

- [ ] **Step 6: Verify status transition idempotence**

Add a test that repeated `statusTransition` calls without new events return `null`, and that exactly one transition is returned when the issue changes from incomplete to complete. Run `npx vitest run test/completeness.test.ts test/contracts.test.ts`; expected: all focused tests pass.

- [ ] **Step 7: Commit completeness logic**

```bash
git add apps/champion_follow_platform/collector/src/completeness.ts apps/champion_follow_platform/collector/test/completeness.test.ts
git commit -m "feat(collector): exclude incomplete and ambiguous issues"
```

### Task 6: Upload contiguous batches and replay until ACK

**Files:**
- Create: `apps/champion_follow_platform/collector/src/server-api.ts`
- Create: `apps/champion_follow_platform/collector/src/uploader.ts`
- Test: `apps/champion_follow_platform/collector/test/uploader.test.ts`

- [ ] **Step 1: Write ACK, retry, and redaction tests**

Create `test/uploader.test.ts` with a fake journal and injected server port. Assert:

1. records 4–6 are sent in order and `ack_seq=6` advances the cursor;
2. a network error leaves the cursor unchanged and the next tick resends exactly 4–6;
3. `ack_seq=7` for a batch ending at 6 returns `collector_ack_invalid`;
4. a lower ACK never rewinds the cursor;
5. a heartbeat body contains no actor key, event payload, token, Cookie, or raw request;
6. a successful `204 No Content` heartbeat does not attempt JSON decoding;
7. exposed transport errors are only `collector_network_error`, `collector_auth_rejected`, `collector_sequence_conflict`, or `collector_server_error` and never response text;
8. `rejects_malformed_session_event_keys` and `rejects_oversized_or_non_ascii_session_event_keys` reject both ACK and history-anchor keys unless they match the strict 80-character event-key contract;
9. `rejects_extra_session_response_fields` proves the four-field response is strict;
10. `rejects_unsafe_or_non_integer_session_ack_seq` rejects fractions, negatives, and values above `Number.MAX_SAFE_INTEGER`;
11. invalid JSON from an otherwise successful server response maps to `collector_server_error`, not `collector_network_error`.

- [ ] **Step 2: Run tests and verify missing implementation**

Run: `cd apps/champion_follow_platform/collector && npx vitest run test/uploader.test.ts`

Expected: exit code `1` with missing server API/uploader imports.

- [ ] **Step 3: Implement the HTTPS API adapter**

Create `src/server-api.ts`:

```ts
import { ackSchema, heartbeatSchema, eventBatchSchema, sessionResponseSchema, type Heartbeat, type JournalRecord } from "./contracts.js";

export interface CollectorServerPort {
  session(request: { collector_id: string; namespace_version: "actor-hmac-v1" }): Promise<{ ack_seq: number; ack_event_key: string | null; history_anchor_event_key: string | null; namespace_empty: boolean }>;
  append(request: { collector_id: string; namespace_version: "actor-hmac-v1"; from_seq: number; to_seq: number; records: JournalRecord[] }): Promise<{ ack_seq: number }>;
  heartbeat(value: Heartbeat): Promise<void>;
}

export class HttpCollectorServer implements CollectorServerPort {
  constructor(private readonly baseUrl: string, private readonly bearer: string, private readonly fetchImpl: typeof fetch = fetch) {
    if (new URL(baseUrl).protocol !== "https:") throw new Error("collector_server_https_required");
  }
  async session(request: { collector_id: string; namespace_version: "actor-hmac-v1" }): Promise<{ ack_seq: number; ack_event_key: string | null; history_anchor_event_key: string | null; namespace_empty: boolean }> {
    const response = await this.call("/v1/collector/session", request);
    const parsed = sessionResponseSchema.safeParse(response);
    if (!parsed.success) throw new Error("collector_server_error");
    return parsed.data;
  }
  async append(request: Parameters<CollectorServerPort["append"]>[0]): Promise<{ ack_seq: number }> {
    const body = eventBatchSchema.parse(request);
    const response = await this.call("/v1/collector/events", body);
    return ackSchema.parse(response);
  }
  async heartbeat(value: Heartbeat): Promise<void> {
    await this.call("/v1/collector/heartbeat", heartbeatSchema.parse(value));
  }
  private async call(path: string, body: unknown): Promise<unknown> {
    let response: Response;
    try {
      response = await this.fetchImpl(new URL(path, this.baseUrl), {
        method: "POST", headers: { "content-type": "application/json", authorization: `Bearer ${this.bearer}` }, body: JSON.stringify(body)
      });
    } catch {
      throw new Error("collector_network_error");
    }
    if (response.status === 401 || response.status === 403) throw new Error("collector_auth_rejected");
    if (response.status === 409) throw new Error("collector_sequence_conflict");
    if (!response.ok) throw new Error("collector_server_error");
    if (response.status === 204) return undefined;
    try { return await response.json(); }
    catch { throw new Error("collector_server_error"); }
  }
}
```

The bearer value is supplied only as `credential.bearer` from `CollectorCredentialStore.load()` (or the just-completed import). No config/environment adapter may supply it. It is never logged or included in thrown errors, and the HTTP adapter must not retain request or response bodies in diagnostics.

- [ ] **Step 4: Implement one deterministic upload tick**

Create `src/uploader.ts`:

```ts
import type { AppendOnlyJournal } from "./journal.js";
import type { CollectorServerPort } from "./server-api.js";

export class ReliableUploader {
  constructor(private readonly collectorId: string, private readonly journal: AppendOnlyJournal, private readonly server: CollectorServerPort) {}
  async tick(): Promise<number> {
    const records = this.journal.pending(200);
    if (!records.length) return this.journal.acknowledgedSeq;
    const response = await this.server.append({ collector_id: this.collectorId, namespace_version: "actor-hmac-v1", from_seq: records[0]!.seq, to_seq: records.at(-1)!.seq, records });
    if (response.ack_seq < this.journal.acknowledgedSeq) return this.journal.acknowledgedSeq;
    if (response.ack_seq > records.at(-1)!.seq) throw new Error("collector_ack_invalid");
    await this.journal.advanceAck(response.ack_seq);
    return response.ack_seq;
  }
}
```

- [ ] **Step 5: Add bounded reconnect and independent heartbeat loops**

Implement `run(signal)` with deterministic delays `250, 500, 1000, 2000, 5000` ms, capped at 5000 and reset after success. Use an `AbortSignal`. Run a separate 250 ms heartbeat loop from a callback-provided state so a large backlog cannot delay health reporting. Heartbeat errors affect health but never delete local records.

- [ ] **Step 6: Run uploader tests and type check**

Run: `cd apps/champion_follow_platform/collector && npx vitest run test/uploader.test.ts && npm run typecheck`

Expected: all uploader cases pass; type check exits `0`.

- [ ] **Step 7: Commit server delivery**

```bash
git add apps/champion_follow_platform/collector/src/server-api.ts apps/champion_follow_platform/collector/src/uploader.ts apps/champion_follow_platform/collector/test/uploader.test.ts
git commit -m "feat(collector): replay events through contiguous server ack"
```

### Task 7: Wire Electron main, history recovery, and fail-closed behavior

**Files:**
- Create: `apps/champion_follow_platform/collector/src/window-policy.ts`
- Create: `apps/champion_follow_platform/collector/src/runtime.ts`
- Create: `apps/champion_follow_platform/collector/src/main.ts`
- Test: `apps/champion_follow_platform/collector/test/window-policy.test.ts`
- Test: `apps/champion_follow_platform/collector/test/runtime.test.ts`

- [ ] **Step 1: Write Electron policy tests**

Create `test/window-policy.test.ts` and assert web preferences exactly set `nodeIntegration:false`, `contextIsolation:true`, `sandbox:true`, `webSecurity:true`, and `allowRunningInsecureContent:false`. Assert partition `persist:champion-follow-main-collector-v1`, denied permission requests, denied popups, and blocked navigation outside the configured platform origin.

- [ ] **Step 2: Write runtime ordering/failure tests**

Create `test/runtime.test.ts` with injected journal, tracker, server, and renderer ports. Assert:

- renderer capture acknowledgement waits for `journal.append()`;
- a rejected append returns `journal_write_failed`, stops capture, and marks heartbeat unhealthy;
- a server outage does not reject later local appends;
- `repairedTail` causes `CAPTURE_GAP(journal_torn_tail)` before later events;
- no complete status appears before anchor, betting boundary, close, and five-digit result;
- history/live overlap with the same event key is appended once;
- reconnect starts after the persisted ACK and never resends an acknowledged record.
- `uses_history_anchor_instead_of_marker_ack_for_backfill` gives the session an ACK pointing to `ISSUE_STATUS` plus an older money anchor and proves history paging uses only the money anchor;
- a NULL history anchor allows a fresh boundary only with `namespace_empty=true`; NULL plus false fails closed;
- startup does not construct the HTTP adapter, start loops, or open the platform window until credential import has returned after source unlink;
- missing OS ciphertext, invalid credential arguments, unavailable `safeStorage`, or failed handoff deletion expose only their fixed safe codes and leave networking stopped.

- [ ] **Step 3: Run tests and verify missing implementation**

Run: `cd apps/champion_follow_platform/collector && npx vitest run test/window-policy.test.ts test/runtime.test.ts`

Expected: exit code `1` with missing policy/runtime imports.

- [ ] **Step 4: Implement immutable Electron window policy**

Create `src/window-policy.ts`:

```ts
export const COLLECTOR_PARTITION = "persist:champion-follow-main-collector-v1";
export function collectorWebPreferences(preload: string) {
  return { preload, partition: COLLECTOR_PARTITION, nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, allowRunningInsecureContent: false } as const;
}
export function sameOriginNavigation(target: string, platformOrigin: string): boolean {
  try { return new URL(target).origin === platformOrigin; } catch { return false; }
}
```

- [ ] **Step 5: Implement durability-first runtime ingestion**

Create `src/runtime.ts`. `ingest(events)` must, for each strict event: reject an already-seen `eventKey`; await `journal.append`; update `IssueCompletenessTracker`; append at most one changed `ISSUE_STATUS` without feeding that derived event back into the tracker; then return the highest persisted sequence. A rejected append sets a permanent failed-closed flag, aborts renderer capture, and exposes only `journal_write_failed`; it does not acknowledge the batch. Server upload is independent and never awaited by ingestion.

All derived records must pass the same `capturedEventSchema` before journal append. Build a derived event key as lowercase SHA-256 of `canonicalJson({kind,issue,reasonOrStatus,lastInputEventKey})`; `reasonOrStatus` is either the single gap reason or `{complete,reasons:[...sortedUniqueReasons]}`. Copy `sourceMs`, `receivedAtMs`, `source`, `parserVersion`, and `namespaceVersion` from the triggering sanitized event; a restart-only torn-tail marker uses the freshly verified current-boundary event as its trigger. This gives every changed status a reproducible 64-hex key without raw actor data. A local `ISSUE_STATUS complete=true` is only an audit hint: the server independently proves completeness and may reject the issue.

On startup, if `journal.repairedTail` is true, follow Task 4's known-issue/next-boundary rule and append `CAPTURE_GAP(journal_torn_tail)` before any later event for that issue. `observeHeartbeat` runs every 250 ms and `markHistoryAnchorRecovered` is explicit.

- [ ] **Step 6: Implement bounded history backfill**

Immediately after the page hook attaches, call `/v1/collector/session` and reconcile only its `ack_seq`/`ack_event_key` with `cursor.json`; a regression or mismatch fails closed. Use only `history_anchor_event_key` as the history replay anchor: an ACK may name `CLOSE`/`RESULT`/`CAPTURE_GAP` and therefore can never substitute for a chat-history money event. Request sanitized history pages of at most 100 messages starting after the history anchor. If it is NULL, only `namespace_empty=true` plus a complete current betting boundary may establish a fresh boundary；`namespace_empty=false` with no history anchor fails closed. Stop only when that history anchor is observed, or under the fresh-empty rule. Deduplicate only by event key. An empty page before the anchor appends `CAPTURE_GAP(history_anchor_missing)`; a history error retries after 500 ms while the issue is open and makes the issue ineligible if close arrives first. Never match by amount, actor, or timestamp similarity, never use `ack_event_key` as a history anchor, and never write the history anchor into the ACK cursor.

- [ ] **Step 7: Implement Electron lifecycle and session isolation**

Create `src/main.ts` to reject non-HTTPS URLs, create `<userData>/main-collector-v1` mode `0700`, acquire `collector.lock`, load identity through Electron `safeStorage`, import/load the collector credential, open the journal before a BrowserWindow, reconcile the session ACK before history backfill, create one visible window with the fixed partition and `dist/preload.cjs`, install `dist/page-hook.js` after every committed main-frame navigation, validate IPC sender and platform origin, deny permissions and new windows, allow manual login only at the configured platform origin, and close loops/journal on `before-quit`.

Use this exact bootstrap ordering after `app.whenReady()` and exclusive lock acquisition, but before constructing any network or browser object:

```ts
import { app, safeStorage } from "electron";
import { join } from "node:path";
import { CollectorCredentialStore, parseCredentialImportArgs } from "./credential-store.js";
import { HttpCollectorServer } from "./server-api.js";

const runtimeRoot = join(app.getPath("userData"), "main-collector-v1");
const credentialStore = new CollectorCredentialStore(
  join(runtimeRoot, "collector-credential.enc"),
  safeStorage
);
const importMode = parseCredentialImportArgs(process.argv.slice(2));
const credential = importMode.kind === "file"
  ? await credentialStore.importFromFile(importMode.path)
  : importMode.kind === "stdin"
    ? await credentialStore.importFromStdin(process.stdin)
    : await credentialStore.load();

// This is the first point at which networking may be constructed.
const server = new HttpCollectorServer(configuredServerUrl, credential.bearer);
const collectorId = credential.collector_id;
```

Do not read `COLLECTOR_BEARER` or any equivalent environment key. Do not add a bearer-valued CLI option. The only non-secret command-line value is the handoff file path. `importFromFile()` returning proves that encrypted persistence and unlink both succeeded; `importFromStdin()` returning proves encrypted persistence succeeded.

The runtime must never log IPC payloads, authorization headers, response bodies, collector bearer values, credential bundle contents, actor keys, raw IDs, or key material. Startup catches expose only the fixed error code; they do not stringify the original error or credential object. The platform partition is dedicated to the main collector and is never reused by ordinary clients.

- [ ] **Step 8: Run runtime/policy tests and build**

Run:

```bash
cd apps/champion_follow_platform/collector
npx vitest run test/window-policy.test.ts test/runtime.test.ts
npm run typecheck
npm run build
```

Expected: all focused tests pass; type check exits `0`; `dist/main.mjs`, `dist/preload.cjs`, and `dist/page-hook.js` exist.

- [ ] **Step 9: Commit the runnable collector**

```bash
git add apps/champion_follow_platform/collector/src/window-policy.ts apps/champion_follow_platform/collector/src/runtime.ts apps/champion_follow_platform/collector/src/main.ts apps/champion_follow_platform/collector/test/window-policy.test.ts apps/champion_follow_platform/collector/test/runtime.test.ts
git commit -m "feat(collector): run secure dedicated electron capture"
```

### Task 8: Prove outage replay, cancellation correctness, and privacy

**Files:**
- Create: `apps/champion_follow_platform/collector/scripts/privacy-scan.mjs`
- Create: `apps/champion_follow_platform/collector/test/collector-integration.test.ts`
- Modify: `apps/champion_follow_platform/collector/package.json`

- [ ] **Step 1: Write the end-to-end outage test**

Create `test/collector-integration.test.ts` with an in-memory server unavailable for two calls, then ACKing contiguous records. Feed recovered anchor, betting boundary, actor A BET `P1:大`, exact CANCEL `P1:大`, actor A BET `P1:小`, close, and a five-digit result; restart before server recovery. Assert one remaining prediction (`P1:小`), complete issue status, contiguous exactly-once server storage, cursor advancement only after successful transaction, and absence of every raw fixture marker from journal/API output.

- [ ] **Step 2: Add an ambiguous-cancel integration case**

Feed another issue with `CANCEL_UNATTRIBUTED`, close, and a valid result. Assert events replay, but `ISSUE_STATUS.complete` is false with `unattributed_cancel`; no actor cancellation is synthesized.

- [ ] **Step 3: Add an end-to-end credential privacy case**

Use a synthetic bearer marker and an injected `safeStorage` port. On POSIX, import the exact Plan 01 bundle from a `0600` handoff file; on Windows, feed the same bundle through stdin. Construct `HttpCollectorServer` only after import resolves. The fake fetch adapter may compare the Authorization header to the expected value and retain only a boolean result; it must not retain the header. Assert the source was unlinked after POSIX import, ciphertext does not contain the marker, authentication succeeded, and the marker is absent from the journal, cursor, serialized HTTP bodies, captured console output, and thrown errors.

- [ ] **Step 4: Run integration tests**

Run: `cd apps/champion_follow_platform/collector && npx vitest run test/collector-integration.test.ts`

Expected: all three integration scenarios pass and test output contains no synthetic bearer marker.

- [ ] **Step 5: Add a built-artifact privacy scanner**

Create `scripts/privacy-scan.mjs`:

```js
import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";
const forbidden = ["raw-player-marker", "raw-message-marker", "document.cookie", "localStorage.getItem(\"token\")"];
for (const file of (await readdir("dist")).filter((name) => /\.(?:mjs|cjs|js)$/.test(name) && !name.endsWith(".map"))) {
  const body = await readFile(join("dist", file), "utf8");
  for (const marker of forbidden) if (body.includes(marker)) throw new Error(`privacy_scan_failed:${file}`);
}
process.stdout.write("privacy scan passed\n");
```

- [ ] **Step 6: Add the aggregate verification command**

Modify `package.json`:

```json
"verify": "npm run typecheck && npm test && npm run build && npm run privacy:scan"
```

- [ ] **Step 7: Run the complete verification**

Run: `cd apps/champion_follow_platform/collector && npm run verify`

Expected: all test files pass, zero skipped tests, and final line `privacy scan passed`.

- [ ] **Step 8: Inspect scope and secret safety**

Run:

```bash
git diff --check
git status --short apps/champion_follow_platform/collector
```

Expected: no whitespace errors; only collector paths are listed; the final diff is empty. Also run `git grep -n "synthetic_fixture_" -- ':!apps/champion_follow_platform/collector/test/**'`; expected: no match. Do not copy runtime logs or a handoff bundle into evidence.

- [ ] **Step 9: Commit the end-to-end proof**

```bash
git add apps/champion_follow_platform/collector/scripts/privacy-scan.mjs apps/champion_follow_platform/collector/test/collector-integration.test.ts apps/champion_follow_platform/collector/package.json apps/champion_follow_platform/collector/package-lock.json
git commit -m "test(collector): verify replay completeness and privacy"
```

## Self-review against the approved specification

- Public event normalization, stable HMAC identity, namespace version, encrypted backup/recovery, one-time bearer handoff, and no raw UID/nickname/credential persistence are covered by Tasks 2–3 and 8.
- Append-only local sequencing, fsync, ACK cursor, overlap deduplication, restart replay, and server outage behavior are covered by Tasks 4, 6, 7, and 8.
- Attributable cancellation, conservative unknown cancellation handling, gaps, result/close boundaries, and complete-issue gating are covered by Task 5 and the integration tests.
- One-second heartbeat freshness and separate heartbeat/backlog loops are covered by Tasks 6–7.
- Dedicated Chromium session, manual login, HTTPS-only transport, OS-protected collector authentication, sender/origin checks, and failed-closed journal behavior are covered by Task 7.
- The plan does not implement forbidden alternate betting strategies, player balances, external-browser control, mobile execution, rankings, or device allocation.

The final review found no incomplete implementation step or undefined task reference. Every implementation step names exact files, tests, commands, and expected outcomes.

Plan complete and saved to `docs/superpowers/plans/2026-07-27-champion-follow-02-collector.md`. Execute with **Subagent-Driven Development** so each task receives a fresh implementer and review before the next task begins.
