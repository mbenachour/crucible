import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, apiFetch, auth } from "../api/client";
import type { Coverage, Finding, Page } from "../api/types";
import { parseLine, parseLog, type LogEvent } from "./logEvents";

export type LogStatus = "loading" | "waiting" | "live" | "ended" | "error";

const OVERLAP = 400; // lines of history re-sent on each (re)connect, deduped client-side

/**
 * Structured progress events for a run, read from its run.log: the whole file
 * once (`GET /runs/{id}/log`), then the live tail (`/log/stream`) for as long
 * as the run is going. Only parsed events are kept — raw lines never leave
 * this hook except as a short rolling window used to de-duplicate the
 * history/stream overlap.
 */
export function useRunLog(runId: string, finished: boolean) {
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [status, setStatus] = useState<LogStatus>("loading");
  const finishedRef = useRef(finished);
  finishedRef.current = finished;

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    let recent: string[] = [];
    setEvents([]);
    setStatus("loading");

    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
    const remember = (lines: string[]) => {
      recent = recent.concat(lines).slice(-OVERLAP);
    };

    async function history(): Promise<boolean> {
      for (;;) {
        try {
          const text = await apiFetch<string>(`/runs/${runId}/log`, { text: true });
          if (signal.aborted) return false;
          setEvents(parseLog(text));
          remember(text.split("\n").filter(Boolean));
          return true;
        } catch (e) {
          if (signal.aborted) return false;
          // 404 = run.log not created yet (still cloning / queued)
          if (e instanceof ApiError && e.status === 404 && !finishedRef.current) {
            setStatus("waiting");
            await sleep(3000);
            continue;
          }
          setStatus(e instanceof ApiError && e.status === 404 ? "ended" : "error");
          return false;
        }
      }
    }

    async function follow() {
      while (!signal.aborted && !finishedRef.current) {
        try {
          const headers: Record<string, string> = {};
          const token = auth.getToken();
          if (token) headers["Authorization"] = `Bearer ${token}`;
          const res = await fetch(`${auth.getBase()}/runs/${runId}/log/stream?tail_lines=${OVERLAP}`, { headers, signal });
          if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
          setStatus("live");
          const seen = new Set(recent);
          let catchingUp = true;
          let buf = "";
          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            const parts = buf.split("\n");
            buf = parts.pop() ?? "";
            const fresh: string[] = [];
            for (const line of parts) {
              if (!line) continue;
              if (catchingUp && seen.has(line)) continue;
              catchingUp = false;
              fresh.push(line);
            }
            if (!fresh.length) continue;
            remember(fresh);
            const evs = fresh.map(parseLine).filter((x): x is LogEvent => x !== null);
            if (evs.length) setEvents((prev) => prev.concat(evs));
          }
        } catch {
          if (signal.aborted) return;
        }
        // stream ended (server time cap / hiccup) — reconnect unless the run is done
        if (!finishedRef.current) await sleep(2000);
      }
      if (!signal.aborted) setStatus("ended");
    }

    (async () => {
      if (!(await history())) return;
      if (finishedRef.current) setStatus("ended");
      else await follow();
    })();

    return () => controller.abort();
  }, [runId]);

  return { events, status };
}

/** Re-render every `ms` while `on`. */
export function useNow(on: boolean, ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [on, ms]);
  return on ? now : Date.now();
}

export const useLiveFindings = (runId: string, live: boolean) =>
  useQuery({
    queryKey: ["progress-findings", runId],
    queryFn: () => apiFetch<Page<Finding>>(`/runs/${runId}/findings`, { query: { order: "created_at", limit: 60 } }),
    refetchInterval: live ? 5000 : false,
  });

export const useLiveCoverage = (runId: string, live: boolean) =>
  useQuery({
    queryKey: ["progress-coverage", runId],
    queryFn: () => apiFetch<Coverage>(`/runs/${runId}/coverage`),
    retry: false,
    refetchInterval: live ? 8000 : false,
  });
