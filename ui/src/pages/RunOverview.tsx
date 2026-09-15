import { Link, useParams } from "react-router-dom";
import { useConfigModels, useMetrics, useRun } from "../api/hooks";

function Tile({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="tile">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
    </div>
  );
}

const ROLE_LABELS: Record<string, string> = {
  recon: "Recon", hunter: "Hunter", validator_bug: "Validator (bug)", validator_reach: "Validator (reach)",
};
const FIELDS = ["model", "temperature", "base_url"] as const;

/** Compact per-role effective config + provenance (issue #77) — a field
 * sourced from this run's own override is tagged distinctly from one that
 * just reflects the host's config.yaml/env/default. */
function ModelsPanel({ runId }: { runId: string }) {
  const cfg = useConfigModels(runId);
  if (!cfg.data) return null;
  return (
    <div>
      <h3>Models</h3>
      <div className="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th>role</th>
              <th>model</th>
              <th>temp</th>
              <th>base url</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(cfg.data.roles).map(([role, e]) => (
              <tr key={role}>
                <td>{ROLE_LABELS[role] ?? role}</td>
                {FIELDS.map((f) => (
                  <td key={f} className="mono" title={`source: ${e.source[f]}`}>
                    {String(e[f])}
                    {e.source[f] === "run override" && (
                      <span className="tag tag-ok" style={{ marginLeft: 6, fontSize: 10 }}>override</span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function RunOverview() {
  const { runId = "" } = useParams();
  const run = useRun(runId);
  const m = useMetrics(runId);
  const c = run.data?.counts ?? {};

  return (
    <div className="grid" style={{ marginTop: 12 }}>
      <div>
        <h3>Funnel</h3>
        <div className="tiles">
          <Tile k="total" v={c.total ?? 0} />
          <Tile k="raw" v={c.raw ?? 0} />
          <Tile k="mechanical failed" v={c.mechanical_failed ?? 0} />
          <Tile k="bug upheld" v={c.bug_upheld ?? 0} />
          <Tile k="reach upheld" v={<span className="tag tag-ok" style={{ fontSize: 16 }}>{c.reach_upheld ?? 0}</span>} />
          <Tile k="duplicate" v={c.duplicate ?? 0} />
        </div>
      </div>

      <div>
        <h3>Run</h3>
        <div className="tiles">
          <Tile k="recon quality" v={run.data?.recon_quality || "—"} />
          <Tile k="fork rate" v={m.data?.fork_rate ?? "—"} />
          <Tile k="cycles" v={m.data?.cycles ?? "—"} />
          <Tile k="continuations" v={m.data?.continuations ?? "—"} />
          <Tile k="token spend" v={m.data?.token_spend?.toLocaleString() ?? "—"} />
        </div>
      </div>

      <div>
        <h3>Tool usage</h3>
        {m.data && Object.keys(m.data.tool_usage).length > 0 ? (
          <div className="tbl-wrap">
            <table>
              <thead>
                <tr>
                  <th>tool</th>
                  <th>count</th>
                  <th>errors</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(m.data.tool_usage).map(([t, u]) => (
                  <tr key={t}>
                    <td className="mono">{t}</td>
                    <td>{u.count}</td>
                    <td>{u.errors}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="dim">No tool-usage rows.</div>
        )}
      </div>

      <ModelsPanel runId={runId} />

      <div className="dim">
        Jump to{" "}
        <Link to={`/runs/${runId}/findings`}>findings</Link> ·{" "}
        <Link to={`/runs/${runId}/report`}>report</Link> ·{" "}
        <Link to={`/runs/${runId}/coverage`}>coverage</Link> ·{" "}
        <Link to={`/runs/${runId}/state`}>state</Link>
      </div>
    </div>
  );
}
