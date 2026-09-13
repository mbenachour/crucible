import { Link, NavLink, Outlet, useParams } from "react-router-dom";
import { useRun } from "../api/hooks";
import { Q } from "../components/states";
import { OutcomeTag, Time } from "../components/bits";
import { baseName, shortCommit } from "../lib/format";
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
              {baseName(r.repo_path)} <span className="mono dim" style={{ fontSize: 13 }}>{shortCommit(r.repo_commit)}</span>
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
                style={{ padding: "4px 8px", borderRadius: 6 }}
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
      return null;
  }
}
