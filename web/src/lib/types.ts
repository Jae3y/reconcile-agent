/**
 * Mirrors api/models.py exactly. A change there is a change here.
 * No `any` anywhere in this file or anything that consumes it.
 */

export type RunKind = "run" | "eval";
export type RunStatus = "queued" | "running" | "succeeded" | "failed";
export type StageName = "ingest" | "candidates" | "judge" | "policy" | "execute";
export type StageStatus = "pending" | "active" | "done" | "error";
export type EventLevel = "info" | "success" | "warning" | "error";
export type IntegrationStatus = "online" | "degraded" | "offline" | "unconfigured";

export interface Health {
  ok: boolean;
  service: string;
  version: string;
}

export interface Integration {
  name: string;
  label: string;
  ok: boolean;
  status: IntegrationStatus;
  detail: string;
  endpoint: string;
  latency_ms: number | null;
  checked_at: string;
}

export interface IntegrationsResponse {
  backend: string;
  simulated: boolean;
  integrations: Integration[];
  checked_at: string;
}

export interface Scenario {
  n: number;
  name: string;
  category: string;
  expected_tier: string;
  expected_type: string;
  decoy: boolean;
  why: string;
}

export interface StageState {
  name: StageName;
  status: StageStatus;
  detail: string;
  count: number | null;
  seconds: number | null;
}

export type EventData = Record<string, string | number | boolean | null | undefined>;

export interface RunEvent {
  seq: number;
  run_id: string;
  at: string;
  type: string;
  stage: StageName | null;
  level: EventLevel;
  title: string;
  message: string;
  data: EventData;
}

export interface JudgedItem {
  subject: string;
  discrepancy_type: string;
  is_match: boolean;
  confidence: number;
  reasoning: string;
  cached: boolean;
}

export interface ActionItem {
  subject: string;
  discrepancy_type: string;
  tier: string;
  status: string;
  description: string;
  detail: string;
  attempts: number;
  verified: boolean | null;
  verify_field: string;
  verify_expected: string | number | boolean | null;
  verify_observed: string | number | boolean | null;
}

export interface Metrics {
  valid: boolean;
  invalid_reason: string;
  recall: number;
  precision: number;
  f1: number;
  decoy_false_action_rate: string;
  decoys_acted: number;
  decoy_total: number;
  tier_accuracy: number;
  post_state_correctness: number;
  detected_real: number;
  real_total: number;
  missed: string[];
  judge_failures: number;
  quota_failures: number;
  cached_verdicts: number;
  fresh_verdicts: number;
  injected_subject: string | null;
  injected_caught: boolean;
  verified_ok: number;
}

export interface RunSummary {
  run_id: string;
  kind: RunKind;
  status: RunStatus;
  started_at: string;
  finished_at: string | null;
  duration_seconds: number | null;
  trace_id: string;
  error: string;
  candidates: number;
  applied: number;
  verify_failed: number;
  skipped: number;
  no_action: number;
  metrics: Metrics | null;
}

export interface RunDetail extends RunSummary {
  stages: StageState[];
  counts: Record<string, number>;
  judged: JudgedItem[];
  actions: ActionItem[];
  events: RunEvent[];
}

export interface StartRunResponse {
  run_id: string;
  kind: RunKind;
  status: RunStatus;
  stream: string;
}

export interface SeedResponse {
  ok: boolean;
  seeded: number;
  errors: string[];
  counts: Record<string, number>;
  lines: string[];
}

export const STAGE_ORDER: StageName[] = [
  "ingest",
  "candidates",
  "judge",
  "policy",
  "execute",
];

export const STAGE_LABEL: Record<StageName, string> = {
  ingest: "Ingest",
  candidates: "Candidates",
  judge: "Judge",
  policy: "Policy",
  execute: "Executor",
};

export const STAGE_SUBTITLE: Record<StageName, string> = {
  ingest: "Stripe + HubSpot",
  candidates: "deterministic · no LLM",
  judge: "Claude Sonnet 4.6",
  policy: "autonomy tiers",
  execute: "write + read back",
};
