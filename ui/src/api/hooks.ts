import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "./client";
import type {
  ConfigCatalog,
  ConfigModels,
  Coverage,
  Finding,
  Health,
  Metrics,
  ModelOverride,
  Page,
  Run,
  RunState,
  TriggerRunIn,
  TriggerRunOut,
  Validation,
  Wish,
  ArtifactMeta,
} from "./types";

type Q = Record<string, string | number | boolean | undefined | (string | number)[]>;

const LAUNCHING = new Set(["pending", "cloning"]);

export const useHealth = () =>
  useQuery({ queryKey: ["health"], queryFn: () => apiFetch<Health>("/health"), retry: false });

export const useRuns = (query: Q) =>
  useQuery({
    queryKey: ["runs", query],
    queryFn: () => apiFetch<Page<Run>>("/runs", { query }),
    // keep list rows (badges) live while anything in view might still be launching
    refetchInterval: (q) => (q.state.data?.items.some((r) => LAUNCHING.has(r.clone_status)) ? 2000 : false),
  });

export const useRun = (runId: string) =>
  useQuery({
    queryKey: ["run", runId],
    queryFn: () => apiFetch<Run>(`/runs/${runId}`),
    // poll while cloning, and for as long as the run hasn't finished — so the
    // "run in progress" banner (and its live-logs link) clears itself promptly
    refetchInterval: (q) =>
      LAUNCHING.has(q.state.data?.clone_status ?? "") || (q.state.data && !q.state.data.finished_at)
        ? 2000
        : false,
  });

export function useTriggerRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: TriggerRunIn) => apiFetch<TriggerRunOut>("/runs", { method: "POST", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runs"] }),
  });
}

// GET /config/models (issue #73); `runId` (issue #77) reports "run override"
// provenance for any field a run's own override touched.
export const useConfigModels = (runId?: string, enabled = true) =>
  useQuery({
    queryKey: ["config-models", runId],
    queryFn: () => apiFetch<ConfigModels>("/config/models", { query: runId ? { run_id: runId } : undefined }),
    enabled,
    retry: false,
  });

// GET /config/catalog (issue #80): the curated OpenRouter model list, grouped
// by family — what the model dropdowns render. Static-ish; a long staleTime
// avoids refetching it on every modal open.
export const useConfigCatalog = (enabled = true) =>
  useQuery({
    queryKey: ["config-catalog"],
    queryFn: () => apiFetch<ConfigCatalog>("/config/catalog"),
    enabled,
    staleTime: 5 * 60_000,
  });

// PUT /config/models (issue #80): saves (or, with `null`, clears) a
// host-default per-role override — what the Settings tab's dropdown writes
// to. Takes effect for every run started from here on.
export function useSetConfigModels() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (models: Record<string, ModelOverride | null>) =>
      apiFetch<ConfigModels>("/config/models", { method: "PUT", body: { models } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config-models"] }),
  });
}

export const useMetrics = (runId: string) =>
  useQuery({
    queryKey: ["metrics", runId],
    queryFn: () => apiFetch<Metrics>(`/runs/${runId}/metrics`),
  });

export const useCoverage = (runId: string) =>
  useQuery({
    queryKey: ["coverage", runId],
    queryFn: () => apiFetch<Coverage>(`/runs/${runId}/coverage`),
    retry: false,
  });

export const useRunFindings = (runId: string, query: Q) =>
  useQuery({
    queryKey: ["findings", runId, query],
    queryFn: () => apiFetch<Page<Finding>>(`/runs/${runId}/findings`, { query }),
  });

export const useFinding = (findingId: string) =>
  useQuery({
    queryKey: ["finding", findingId],
    queryFn: () => apiFetch<Finding>(`/findings/${findingId}`),
  });

export const useFindingHistory = (stableKey: string | undefined) =>
  useQuery({
    queryKey: ["finding-history", stableKey],
    queryFn: () => apiFetch<Page<Finding>>("/findings", { query: { stable_key: stableKey! } }),
    enabled: !!stableKey,
  });

export const useValidations = (findingId: string) =>
  useQuery({
    queryKey: ["validations", findingId],
    queryFn: () => apiFetch<Validation[]>(`/findings/${findingId}/validations`),
  });

export const useReportMd = (runId: string) =>
  useQuery({
    queryKey: ["report-md", runId],
    queryFn: () => apiFetch<string>(`/runs/${runId}/report.md`, { text: true }),
    retry: false,
  });

export const useReportJson = (runId: string) =>
  useQuery({
    queryKey: ["report-json", runId],
    queryFn: () => apiFetch<Record<string, unknown>>(`/runs/${runId}/report`),
    retry: false,
    enabled: false,
  });

export const useText = (key: string, path: string, enabled = true) =>
  useQuery({
    queryKey: [key, path],
    queryFn: () => apiFetch<string>(path, { text: true }),
    retry: false,
    enabled,
  });

export const useJson = <T,>(key: string, path: string, enabled = true) =>
  useQuery({
    queryKey: [key, path],
    queryFn: () => apiFetch<T>(path),
    retry: false,
    enabled,
  });

export const useArtifacts = (runId: string) =>
  useQuery({
    queryKey: ["artifacts", runId],
    queryFn: () => apiFetch<ArtifactMeta[]>(`/runs/${runId}/artifacts`),
  });

export const useRunState = (runId: string, poll: boolean) =>
  useQuery({
    queryKey: ["state", runId],
    queryFn: () => apiFetch<RunState>(`/runs/${runId}/state`),
    retry: false,
    refetchInterval: poll ? 5000 : false,
  });

export const useWishes = (query: Q, enabled = true) =>
  useQuery({
    queryKey: ["wishes", query],
    queryFn: () => apiFetch<Page<Wish>>("/wishes", { query }),
    enabled,
  });

export const useRunWishes = (runId: string, query: Q, enabled = true) =>
  useQuery({
    queryKey: ["run-wishes", runId, query],
    queryFn: () => apiFetch<Page<Wish>>(`/runs/${runId}/wishes`, { query }),
    enabled,
  });

export function useResolveWish() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, note }: { id: number; note?: string }) =>
      apiFetch<Wish>(`/wishes/${id}/resolve`, { method: "POST", body: { note } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["wishes"] });
      qc.invalidateQueries({ queryKey: ["run-wishes"] });
    },
  });
}
