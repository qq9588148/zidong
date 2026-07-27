import { z } from "zod";

export const PLAYS = [
  "P1:大",
  "P1:小",
  "P1:单",
  "P1:双",
  "P1:质",
  "P1:合",
  "P2:大",
  "P2:小",
  "P2:单",
  "P2:双",
  "P2:质",
  "P2:合",
  "P3:大",
  "P3:小",
  "P3:单",
  "P3:双",
  "P3:质",
  "P3:合",
  "P4:大",
  "P4:小",
  "P4:单",
  "P4:双",
  "P4:质",
  "P4:合",
  "P5:大",
  "P5:小",
  "P5:单",
  "P5:双",
  "P5:质",
  "P5:合",
] as const;

const digestSchema = z.string().regex(/^[0-9a-f]{64}$/);
export const eventKeySchema = z
  .string()
  .max(80)
  .regex(/^[0-9a-f]{64}(?::(?:block|close|[0-9]{1,15}))?$/);
const issueSchema = z.string().regex(/^\d{8,16}$/);
const common = {
  eventKey: eventKeySchema,
  issue: issueSchema,
  sourceMs: z.number().int().nonnegative(),
  receivedAtMs: z.number().int().nonnegative(),
  source: z.enum(["realtime", "history"]),
  parserVersion: z.literal("btcffc-1"),
  namespaceVersion: z.literal("actor-hmac-v1"),
};
const money = {
  ...common,
  actorKey: digestSchema,
  play: z.enum(PLAYS),
  amountMinor: z.string().regex(/^[1-9]\d*$/),
};

export const capturedEventSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("BET"), ...money }).strict(),
  z.object({ kind: z.literal("CANCEL"), ...money }).strict(),
  z.object({ kind: z.literal("CANCEL_UNATTRIBUTED"), ...common }).strict(),
  z.object({ kind: z.literal("CLOSE"), ...common }).strict(),
  z
    .object({
      kind: z.literal("RESULT"),
      ...common,
      digits: z.tuple([
        z.number().int().min(0).max(9),
        z.number().int().min(0).max(9),
        z.number().int().min(0).max(9),
        z.number().int().min(0).max(9),
        z.number().int().min(0).max(9),
      ]),
    })
    .strict(),
  z
    .object({
      kind: z.literal("CAPTURE_GAP"),
      ...common,
      reason: z.enum([
        "decrypt_failure",
        "history_anchor_missing",
        "journal_torn_tail",
        "journal_write_failed",
        "issue_uncertain",
        "cancel_overdraw",
        "opposite_net_conflict",
      ]),
    })
    .strict(),
  z
    .object({
      kind: z.literal("ISSUE_STATUS"),
      ...common,
      complete: z.boolean(),
      reasons: z.array(z.string().regex(/^[a-z0-9_]+$/)).max(16),
    })
    .strict()
    .superRefine((status, context) => {
      if (status.complete !== (status.reasons.length === 0)) {
        context.addIssue({
          code: "custom",
          message: "issue status mismatch",
        });
      }
    }),
]);
export type CapturedEvent = z.infer<typeof capturedEventSchema>;

export const journalRecordSchema = z
  .object({
    seq: z.number().int().positive(),
    event: capturedEventSchema,
    digest: digestSchema,
  })
  .strict();
export type JournalRecord = z.infer<typeof journalRecordSchema>;

export const eventBatchSchema = z
  .object({
    collector_id: z.string().regex(/^collector-[a-z0-9-]{3,64}$/),
    namespace_version: z.literal("actor-hmac-v1"),
    from_seq: z.number().int().positive(),
    to_seq: z.number().int().positive(),
    records: z.array(journalRecordSchema).min(1).max(200),
  })
  .strict()
  .superRefine((batch, context) => {
    if (
      batch.records[0]?.seq !== batch.from_seq ||
      batch.records.at(-1)?.seq !== batch.to_seq
    ) {
      context.addIssue({ code: "custom", message: "batch bounds mismatch" });
    }
    if (
      batch.records.some(
        (record, index) => record.seq !== batch.from_seq + index,
      )
    ) {
      context.addIssue({ code: "custom", message: "batch is not contiguous" });
    }
  });

export const ackSchema = z
  .object({ ack_seq: z.number().int().nonnegative() })
  .strict();
export const sessionResponseSchema = z
  .object({
    ack_seq: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),
    ack_event_key: eventKeySchema.nullable(),
    history_anchor_event_key: eventKeySchema.nullable(),
    namespace_empty: z.boolean(),
  })
  .strict()
  .superRefine((session, context) => {
    if ((session.ack_seq === 0) !== (session.ack_event_key === null)) {
      context.addIssue({ code: "custom", message: "session ACK mismatch" });
    }
    if (
      (session.history_anchor_event_key === null) !== session.namespace_empty
    ) {
      context.addIssue({
        code: "custom",
        message: "session history mismatch",
      });
    }
  });
export const heartbeatSchema = z
  .object({
    collector_id: z.string().regex(/^collector-[a-z0-9-]{3,64}$/),
    issue: issueSchema.nullable(),
    phase: z.enum(["BETTING", "CLOSED", "UNKNOWN"]),
    countdown_ms: z.number().int().nonnegative(),
    observed_at_ms: z.number().int().nonnegative(),
    last_journal_seq: z.number().int().nonnegative(),
    capture_healthy: z.boolean(),
  })
  .strict();
export type Heartbeat = z.infer<typeof heartbeatSchema>;
