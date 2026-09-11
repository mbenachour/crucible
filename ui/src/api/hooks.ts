import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "./client";
import type {
  Coverage,
  Finding,
  Health,
  Metrics,
  Page,
  Run,
  RunState,
  Validation,
  Wish,
  ArtifactMeta,
} from "./types";

type Q = Record<string, string | number | boolean | undefined | (string | number)[]>;

export const useHealth = () =>
  useQuery({ queryKey: ["health"], queryFn: () => apiFetch<Health>("/health"), retry: false });

export const useRuns = (query: Q) =>
  useQuery({
    queryKey: ["runs", query],
    queryFn: () => apiFetch<Page<Run>>("/runs", { query }),
  });

export const useRun = (runId: string) =>
  useQuery({ queryKey: ["run", runId], queryFn: () => apiFetch<Run>(`/runs/${runId}`) });

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
