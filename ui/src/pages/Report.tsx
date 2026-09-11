import { useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { useReportMd } from "../api/hooks";
import { apiFetch } from "../api/client";
import { useQuery } from "@tanstack/react-query";
import { Markdown } from "../components/Markdown";
import { JsonView } from "../components/JsonView";
import { ErrorState, Loading } from "../components/states";

export function Report() {
  const { runId = "" } = useParams();
  const [view, setView] = useState<"md" | "json">("md");
  const md = useReportMd(runId);
  const json = useQuery({
    queryKey: ["report-json", runId],
    queryFn: () => apiFetch<Record<string, unknown>>(`/runs/${runId}/report`),
    retry: false,
    enabled: view === "json",
  });

  const notRun = (e: unknown) => e instanceof ApiError && e.status === 404;

  return (
    <div style={{ marginTop: 12 }}>
      <div className="filterbar">
        <button className={view === "md" ? "primary" : ""} onClick={() => setView("md")}>
          Rendered
        </button>
        <button className={view === "json" ? "primary" : ""} onClick={() => setView("json")}>
          Structured
        </button>
      </div>

      {view === "md" ? (
        md.isLoading ? (
          <Loading />
        ) : md.error ? (
          notRun(md.error) ? (
            <div className="state-msg">No report yet — the report node has not run for this run.</div>
          ) : (
            <ErrorState error={md.error} retry={() => md.refetch()} />
          )
        ) : (
          <div className="panel">
            <Markdown src={md.data ?? ""} />
          </div>
        )
      ) : json.isLoading ? (
        <Loading />
      ) : json.error ? (
        notRun(json.error) ? (
          <div className="state-msg">No report yet.</div>
        ) : (
          <ErrorState error={json.error} retry={() => json.refetch()} />
        )
      ) : (
        <JsonView value={json.data} />
      )}
    </div>
  );
}
