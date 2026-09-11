// Mirrors crucible/api/schemas.py. Kept in sync by review against
// tests/data/openapi.json (the API side snapshot-tests that schema).

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ErrorBody {
  error: string;
  detail?: string | null;
}

export interface Health {
  status: string;
  version: string;
  store_ok: boolean;
}

export type Outcome =
  | "completed"
  | "stopped_at_stub"
  | "stopped_after_stage"
  | "failed"
  | "";

export type CloneStatus = "" | "pending" | "cloning" | "cloned" | "clone_failed";

export interface Run {
  run_id: string;
  repo_path: string;
  repo_commit: string;
  primary_language: string;
  created_at: string | null;
  finished_at: string | null;
  status: string;
  outcome: Outcome;
  recon_quality: string;
  workspace_path: string;
  report_available: boolean;
  counts: Record<string, number>;
  // API-triggered launches (milestone: Trigger) — empty for a CLI-started run.
  source_spec: string;
  clone_status: CloneStatus;
  clone_error: string;
}

export interface TriggerRunIn {
  repo: string;
  ref?: string;
}

export interface TriggerRunOut {
  run_id: string;
  clone_status: CloneStatus;
}

export interface Metrics {
  run_id: string;
  counts: Record<string, number>;
  fork_rate: string;
  tool_usage: Record<string, { count: number; errors: number; latency_s: number }>;
  cycles: number | null;
  continuations: number | null;
  token_spend: number | null;
}

export interface CoverageCell {
  area: string;
  attack_class: string;
  passes: number;
  findings: number;
  productive: boolean;
  shallow: boolean;
}

export interface Coverage {
  run_id: string;
  matrix_cells: number;
  covered_cells: number;
  cells: CoverageCell[];
  gapfill_buckets: Record<string, string[]>;
}

export interface ThreatModel {
  attacker: string;
  boundary_crossed: string;
  assumption_broken: string;
}

export type Severity = "low" | "medium" | "high" | "critical";

export interface Validation {
  pass_name: string;
  verdict: string;
  reasoning: string;
  model: string;
  prompt_version: string;
  response_class: string;
  created_at: string | null;
}

export interface Finding {
  finding_id: string;
  run_id: string;
  stable_key: string;
  status: string;
  severity: Severity | string | null;
  title: string;
  file_path: string;
  line_start: number | null;
  line_end: number | null;
  description: string;
  threat_model: ThreatModel | Record<string, string> | null;
  poc_test: string;
  proposed_patch: string;
  provenance: Record<string, unknown>;
  duplicate_of: string | null;
  created_at: string | null;
  validation_trail: Validation[] | null;
}

export interface Wish {
  id: number;
  run_id: string;
  blocked_task_id: string;
  need: string;
  context: string;
  status: string;
  created_at: string | null;
}

export interface ArtifactMeta {
  path: string;
  kind: string;
  bytes: number;
  modified_at: string;
}

export interface HuntTask {
  task_id: string;
  area: string;
  attack_class: string;
  chunk_type: string;
  seed_path: string | null;
  scope_hint: string;
  continuation_count: number;
}

export interface RunState {
  run_id: string;
  recon_quality: string;
  subsystems: Record<string, unknown>[];
  pending_hunts: HuntTask[];
  pending_hunt_count: number;
  completed_cells: string[];
  completed_cell_count: number;
  finding_ids: string[];
  cycle_count: number;
  continuation_count: number;
  fork_count: number;
  token_spend: number;
  next_node: string | null;
  checkpoint_ts: string | null;
}
