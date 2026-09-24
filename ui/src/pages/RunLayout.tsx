import { Link, NavLink, Outlet, useParams } from "react-router-dom";
import { useCancelRun, useRun } from "../api/hooks";
import { Q } from "../components/states";
import { OutcomeTag, Time } from "../components/bits";
import { ApiError } from "../api/client";
import { repoLabel, shortCommit } from "../lib/format";
import { useNewRunModal } from "../lib/newRunModal";
import type { Run } from "../api/types";

const TABS = [
  { to: "", label: "Overview", end: true },
  { to: "findings", label: "Findings" },
  { to: "report", label: "Report" },
  { to: "recon", label: "Recon" },
  { to: "coverage", label: "Coverage" },
  { to: "state", label: "State" },
  { to: "logs", label: "Logs" },
  { to: "artifacts", label: "Artifacts" },
  { to: "wishes", label: "Wishes" },
];

export function RunLayout() {
  const { runId = "" } = useParams();
  const q = useRun(runId);

  return (
    <Q q={q} notFound={<div className="state-msg">Run <code>{runId}</code> not found.</div>}>
      {(r) => (
        <>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h1 style={{ marginBottom: 0 }}>
              {repoLabel(r)} <span className="mono dim" style={{ fontSize: 13 }}>{shortCommit(r.repo_commit)}</span>
            </h1>
            <div className="row">
              <OutcomeTag v={r.outcome} />
              <span className="dim">
                started <Time v={r.created_at} />
                {r.finished_at ? (
                  <>
                    {" · finished "}
                    <Time v={r.finished_at} />
                  </>
                ) : null}
              </span>
              {!r.finished_at && <KillRunButton run={r} />}
            </div>
          </div>
          <div className="dim mono" style={{ marginBottom: 10 }}>{runId}</div>

          <LaunchBanner run={r} />

          <nav className="filterbar" style={{ borderBottom: "1px solid var(--border)", paddingBottom: 8 }}>
            {TABS.map((t) => (
              <NavLink
                key={t.to}
                to={t.to ? `/runs/${runId}/${t.to}` : `/runs/${runId}`}
                end={t.end}
                className={({ isActive }) => (isActive ? "active" : "")}
              >
                {t.label}
              </NavLink>
            ))}
          </nav>

          <Outlet context={{ run: r }} />
        </>
      )}
    </Q>
  );
}

function KillRunButton({ run }: { run: Run }) {
  const cancel = useCancelRun();
  const forbidden = cancel.error instanceof ApiError && cancel.error.status === 403;
  const failed = cancel.error instanceof ApiError && cancel.error.status !== 403;

  return (
    <span className="row" style={{ gap: 6 }}>
      <button
        disabled={cancel.isPending}
        style={{ color: "var(--bad-fg)", borderColor: "var(--bad-fg)" }}
        onClick={() => {
          if (window.confirm(`Kill run ${run.run_id}? This stops it immediately — it cannot be resumed from here.`)) {
            cancel.mutate(run.run_id);
          }
        }}
      >
        {cancel.isPending ? "killing…" : "kill run"}
      </button>
      {forbidden && <span className="dim" style={{ color: "var(--bad-fg)" }}>needs a write token</span>}
      {failed && (
        <span className="dim" style={{ color: "var(--bad-fg)" }}>
          {(cancel.error as ApiError).detail || cancel.error?.message}
        </span>
      )}
    </span>
  );
}

function LaunchBanner({ run }: { run: Run }) {
  const newRun = useNewRunModal();

  switch (run.clone_status) {
    case "pending":
      return (
        <div className="banner warn">
          <span className="dim">⟳</span> Queued — waiting to clone <code className="mono">{run.source_spec}</code>…{" "}
          <Link to={`/runs/${run.run_id}/logs`}>watch live logs</Link>
        </div>
      );
    case "cloning":
      return (
        <div className="banner warn">
          <span className="dim">⟳</span> Cloning <code className="mono">{run.source_spec}</code>…{" "}
          <Link to={`/runs/${run.run_id}/logs`}>watch live logs</Link>
        </div>
      );
    case "clone_failed":
      return (
        <div className="banner bad">
          <div>
            Failed to clone <code className="mono">{run.source_spec}</code>: {run.clone_error || "unknown error"}
          </div>
          <button
            style={{ marginTop: 8 }}
            onClick={() => newRun.open({ repo: run.source_spec, ref: "" })}
          >
            Try again
          </button>
        </div>
      );
    default:
      if (!run.finished_at) {
        return (
          <div className="banner warn">
            <span className="dim">⟳</span> Run in progress —{" "}
            <Link to={`/runs/${run.run_id}/logs`}>watch live logs</Link>
          </div>
        );
      }
      // outcome="failed" with no clone_status set means the launched
      // `crucible run` process itself crashed (e.g. Docker unreachable) —
      // clone_error carries its captured stderr tail (launcher.mark_run_crashed).
      if (run.outcome === "failed" && run.clone_error) {
        return (
          <div className="banner bad">
            <div>Run crashed: {run.clone_error.split("\n")[0]}</div>
            <details style={{ marginTop: 6 }}>
              <summary className="dim" style={{ cursor: "pointer" }}>full error</summary>
              <pre className="mono" style={{ whiteSpace: "pre-wrap", marginTop: 6 }}>{run.clone_error}</pre>
            </details>
          </div>
        );
      }
      return null;
  }
}
