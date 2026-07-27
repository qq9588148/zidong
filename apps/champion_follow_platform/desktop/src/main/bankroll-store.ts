import { createHash } from "node:crypto";
import {
  mkdir,
  open,
  readFile,
  rename,
  rm,
} from "node:fs/promises";
import { dirname } from "node:path";

import {
  type BankrollState,
  validateBankrollState,
} from "./bankroll";

type WireState = {
  generation: string;
  cycleId: string;
  version: number;
  baseFen: string;
  capFen: string;
  stakeUnitFen: string;
  unrecoveredFen: string;
  realizedPnlFen: string;
  status: BankrollState["status"];
  pendingUnknownOrderId: string | null;
  lastSettlementId: string | null;
  settledOrderIds: readonly string[];
};

type JournalEnvelope = {
  schemaVersion: 1;
  payload: WireState;
  checksum: string;
};

export class BankrollStore {
  readonly temporaryPath: string;

  constructor(readonly path: string) {
    this.temporaryPath = `${path}.tmp`;
  }

  async load(): Promise<BankrollState | null> {
    const [primary, temporary] = await Promise.all([
      readCandidate(this.path),
      readCandidate(this.temporaryPath),
    ]);
    const valid = [primary.state, temporary.state]
      .filter((state): state is BankrollState => state !== null)
      .sort((left, right) => right.version - left.version);
    if (valid.length === 0) {
      if (primary.exists || temporary.exists) throw new Error("bankroll_journal_corrupt");
      return null;
    }

    const selected = valid[0]!;
    if (temporary.state?.version === selected.version &&
        primary.state?.version !== selected.version) {
      await mkdir(dirname(this.path), { recursive: true });
      await rename(this.temporaryPath, this.path);
    } else if (temporary.exists) {
      await rm(this.temporaryPath, { force: true });
    }
    return selected;
  }

  async save(state: BankrollState, expectedVersion: number | null): Promise<void> {
    validateBankrollState(state);
    const current = await this.load();
    const actualVersion = current?.version ?? null;
    if (actualVersion !== expectedVersion ||
        state.version !== (expectedVersion ?? 0) + 1) {
      throw new Error("bankroll_version_conflict");
    }

    await mkdir(dirname(this.path), { recursive: true });
    const handle = await open(this.temporaryPath, "w", 0o600);
    try {
      await handle.writeFile(encodeJournal(state), "utf8");
      await handle.sync();
    } finally {
      await handle.close();
    }
    await rename(this.temporaryPath, this.path);
  }
}

function encodeJournal(state: BankrollState): string {
  const payload: WireState = {
    generation: state.generation,
    cycleId: state.cycleId,
    version: state.version,
    baseFen: state.baseFen.toString(),
    capFen: state.capFen.toString(),
    stakeUnitFen: state.stakeUnitFen.toString(),
    unrecoveredFen: state.unrecoveredFen.toString(),
    realizedPnlFen: state.realizedPnlFen.toString(),
    status: state.status,
    pendingUnknownOrderId: state.pendingUnknownOrderId,
    lastSettlementId: state.lastSettlementId,
    settledOrderIds: state.settledOrderIds,
  };
  const payloadJson = JSON.stringify(payload);
  const envelope: JournalEnvelope = {
    schemaVersion: 1,
    payload,
    checksum: createHash("sha256").update(payloadJson).digest("hex"),
  };
  return `${JSON.stringify(envelope)}\n`;
}

async function readCandidate(path: string): Promise<{
  exists: boolean;
  state: BankrollState | null;
}> {
  let text: string;
  try {
    text = await readFile(path, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return { exists: false, state: null };
    }
    throw error;
  }
  try {
    const envelope = JSON.parse(text) as JournalEnvelope;
    if (envelope.schemaVersion !== 1 || typeof envelope.checksum !== "string" ||
        typeof envelope.payload !== "object" || envelope.payload === null) {
      return { exists: true, state: null };
    }
    const digest = createHash("sha256")
      .update(JSON.stringify(envelope.payload))
      .digest("hex");
    if (digest !== envelope.checksum) return { exists: true, state: null };
    const state = decodeState(envelope.payload);
    validateBankrollState(state);
    return { exists: true, state };
  } catch {
    return { exists: true, state: null };
  }
}

function decodeState(value: WireState): BankrollState {
  const money = (field: unknown): bigint => {
    if (typeof field !== "string" || !/^-?(0|[1-9][0-9]*)$/.test(field)) {
      throw new Error("bankroll_journal_invalid");
    }
    return BigInt(field);
  };
  if (typeof value.generation !== "string" || typeof value.cycleId !== "string" ||
      !Number.isSafeInteger(value.version) ||
      (value.status !== "READY" && value.status !== "FROZEN_UNKNOWN_SETTLEMENT") ||
      !Array.isArray(value.settledOrderIds) ||
      !value.settledOrderIds.every((item) => typeof item === "string") ||
      (value.pendingUnknownOrderId !== null && typeof value.pendingUnknownOrderId !== "string") ||
      (value.lastSettlementId !== null && typeof value.lastSettlementId !== "string")) {
    throw new Error("bankroll_journal_invalid");
  }
  return {
    generation: value.generation,
    cycleId: value.cycleId,
    version: value.version,
    baseFen: money(value.baseFen),
    capFen: money(value.capFen),
    stakeUnitFen: money(value.stakeUnitFen),
    unrecoveredFen: money(value.unrecoveredFen),
    realizedPnlFen: money(value.realizedPnlFen),
    status: value.status,
    pendingUnknownOrderId: value.pendingUnknownOrderId,
    lastSettlementId: value.lastSettlementId,
    settledOrderIds: [...value.settledOrderIds],
  };
}
