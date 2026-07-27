import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import Ajv2020, { type ValidateFunction } from "ajv/dist/2020";
import addFormats from "ajv-formats";

import type { NativeHelper } from "./native-helper";
import { contractPath } from "./paths";
import { canonicalTaskBytes } from "./task-contract";

export type ClientEventType =
  | "TASK_RECEIVED"
  | "EXECUTION_STATE"
  | "ORDER_CONFIRMED"
  | "ORDER_REJECTED"
  | "ORDER_UNKNOWN"
  | "SETTLEMENT_CONFIRMED"
  | "BALANCE_SNAPSHOT"
  | "BANKROLL_STATE"
  | "LATENCY_SAMPLE";

export type ClientEventEnvelope = {
  schema_version: "client-event-v1";
  device_id: string;
  binding_epoch: number;
  client_seq: number;
  event_id: string;
  observed_at: string;
  type: ClientEventType;
  payload: Record<string, unknown>;
  signature: string;
};

type ClientEventOptions = {
  deviceId: string;
  bindingEpoch: number;
  helper: NativeHelper;
  keyName: string;
  now?: () => Date;
  uuid?: () => string;
};

let eventValidator: ValidateFunction | undefined;

function validator(): ValidateFunction {
  if (eventValidator) return eventValidator;
  const schemaPath = contractPath("client-event-v1.schema.json", process.env.VITEST === "true"
    ? { packaged: false, appPath: process.cwd(), resourcesPath: process.cwd() }
    : undefined);
  const schema = JSON.parse(readFileSync(schemaPath, "utf8")) as object;
  const ajv = new Ajv2020({ strict: true, allErrors: true });
  addFormats(ajv);
  eventValidator = ajv.compile(schema);
  return eventValidator;
}

export class ClientEventContract {
  private readonly now: () => Date;
  private readonly uuid: () => string;

  constructor(private readonly options: ClientEventOptions) {
    if (!isUuid(options.deviceId) ||
        !Number.isSafeInteger(options.bindingEpoch) || options.bindingEpoch < 1) {
      throw new Error("client_event_identity_invalid");
    }
    this.now = options.now ?? (() => new Date());
    this.uuid = options.uuid ?? randomUUID;
  }

  async build(
    sequence: number,
    type: ClientEventType,
    payload: Record<string, unknown>,
  ): Promise<{ sequence: number; envelope: ClientEventEnvelope; bytes: Buffer }> {
    if (!Number.isSafeInteger(sequence) || sequence < 1 ||
        !validatePayload(type, payload)) {
      throw new Error("client_event_payload_invalid");
    }
    const eventId = this.uuid();
    if (!isUuid(eventId)) throw new Error("client_event_uuid_invalid");
    const unsigned = {
      schema_version: "client-event-v1" as const,
      device_id: this.options.deviceId,
      binding_epoch: this.options.bindingEpoch,
      client_seq: sequence,
      event_id: eventId,
      observed_at: utcMicros(this.now()),
      type,
      payload: structuredClone(payload),
    };
    const signature = await this.options.helper.signEcdsaSha256DerBase64(
      this.options.keyName,
      canonicalClientEventBytes(unsigned),
    );
    const envelope: ClientEventEnvelope = { ...unsigned, signature };
    if (!validator()(envelope) || !validatePayload(type, envelope.payload)) {
      throw new Error("client_event_contract_invalid");
    }
    return {
      sequence,
      envelope,
      bytes: Buffer.from(JSON.stringify(envelope), "utf8"),
    };
  }
}

export function canonicalClientEventBytes(value: unknown): Buffer {
  return canonicalTaskBytes(value);
}

function validatePayload(type: ClientEventType, value: unknown): value is Record<string, unknown> {
  if (!isObject(value)) return false;
  const uuid = (name: string) => typeof value[name] === "string" && isUuid(value[name] as string);
  const period = () => typeof value.period_id === "string" &&
    value.period_id.length >= 1 && value.period_id.length <= 64;
  const revision = (name: string) => safeInteger(value[name], 1);
  switch (type) {
    case "TASK_RECEIVED":
      return exact(value, ["task_id", "period_id", "revision"]) &&
        uuid("task_id") && period() && revision("revision");
    case "EXECUTION_STATE":
      return exact(value, ["task_id", "period_id", "revision", "state"]) &&
        uuid("task_id") && period() && revision("revision") && value.state === "SUBMITTING";
    case "ORDER_CONFIRMED":
      return exact(value, [
        "task_id", "period_id", "task_revision", "generation", "client_order_id",
        "platform_order_ref", "stake_minor", "confirmed_at",
      ]) && uuid("task_id") && uuid("generation") && uuid("client_order_id") &&
        period() && revision("task_revision") && safeInteger(value.stake_minor, 1) &&
        typeof value.platform_order_ref === "string" &&
        /^sha256:[0-9a-f]{64}$/.test(value.platform_order_ref) &&
        timestamp(value.confirmed_at);
    case "ORDER_REJECTED":
    case "ORDER_UNKNOWN": {
      const timeField = type === "ORDER_REJECTED" ? "rejected_at" : "unknown_at";
      return exact(value, [
        "task_id", "period_id", "task_revision", "generation", "client_order_id",
        "reason_code", timeField,
      ]) && uuid("task_id") && uuid("generation") && uuid("client_order_id") &&
        period() && revision("task_revision") &&
        typeof value.reason_code === "string" && /^[A-Z0-9_]{1,64}$/.test(value.reason_code) &&
        timestamp(value[timeField]);
    }
    case "SETTLEMENT_CONFIRMED":
      return exact(value, [
        "client_order_id", "period_id", "outcome", "net_pnl_minor", "settled_at",
      ]) && uuid("client_order_id") && period() &&
        (value.outcome === "WIN" || value.outcome === "LOSS" || value.outcome === "PUSH") &&
        safeInteger(value.net_pnl_minor, -Number.MAX_SAFE_INTEGER) &&
        timestamp(value.settled_at);
    case "BALANCE_SNAPSHOT":
      return exact(value, ["availability", "balance_minor"]) &&
        ((value.availability === "AVAILABLE" && safeInteger(value.balance_minor, 0)) ||
         (value.availability === "UNAVAILABLE" && value.balance_minor === null));
    case "BANKROLL_STATE":
      return validateBankrollPayload(value);
    case "LATENCY_SAMPLE":
      return exact(value, ["segment", "milliseconds", "task_id"]) &&
        (value.segment === "TASK_TO_CLIENT" || value.segment === "SCHEDULER_TO_SUBMIT" ||
         value.segment === "SUBMIT_TO_CONFIRM") && safeInteger(value.milliseconds, 0) &&
        (value.task_id === null || (typeof value.task_id === "string" && isUuid(value.task_id)));
  }
}

function validateBankrollPayload(value: Record<string, unknown>): boolean {
  return exact(value, [
    "base_minor", "cap_minor", "unrecovered_loss_minor", "next_stake_minor",
    "cycle_id", "cycle_version", "frozen_reason",
  ]) && safeInteger(value.base_minor, 0) && safeInteger(value.cap_minor, 0) &&
    safeInteger(value.unrecovered_loss_minor, 0) && safeInteger(value.next_stake_minor, 0) &&
    typeof value.cycle_id === "string" && isUuid(value.cycle_id) &&
    safeInteger(value.cycle_version, 1) &&
    (value.frozen_reason === null || value.frozen_reason === "UNKNOWN_SETTLEMENT" ||
     value.frozen_reason === "BALANCE_INSUFFICIENT" ||
     value.frozen_reason === "EVENT_SYNC_CONFLICT");
}

function utcMicros(value: Date): string {
  if (!Number.isFinite(value.getTime())) throw new Error("client_event_time_invalid");
  return value.toISOString().replace(/\.(\d{3})Z$/, (_match, millis: string) =>
    `.${millis}000Z`);
}

function timestamp(value: unknown): boolean {
  return typeof value === "string" &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/.test(value) &&
    Number.isFinite(Date.parse(value));
}

function safeInteger(value: unknown, minimum: number): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum;
}

function exact(value: Record<string, unknown>, keys: string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length &&
    actual.every((key, index) => key === expected[index]);
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
    .test(value);
}
