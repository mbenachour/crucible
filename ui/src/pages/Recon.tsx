import { useParams } from "react-router-dom";
import { useJson, useText } from "../api/hooks";
import { ApiError } from "../api/client";
import { Markdown } from "../components/Markdown";
import { JsonView } from "../components/JsonView";
import { Loading } from "../components/states";

function Panel({
  title,
  q,
  render,
}: {
  title: string;
  q: { isLoading: boolean; error: unknown; data: unknown };
  render: (d: unknown) => React.ReactNode;
}) {
  return (
    <div className="panel" style={{ marginTop: 12 }}>
      <h3>{title}</h3>
      {q.isLoading ? (
        <Loading rows={3} />
      ) : q.error ? (
        q.error instanceof ApiError && q.error.status === 404 ? (
          <div className="dim">Not produced (recon degraded, or this stage did not run).</div>
        ) : (
          <div className="banner bad">{(q.error as Error).message}</div>
        )
      ) : (
        render(q.data)
      )}
    </div>
  );
}

export function Recon() {
  const { runId = "" } = useParams();
  const arch = useText("architecture", `/runs/${runId}/architecture`);
  const tm = useJson("recon-tm", `/runs/${runId}/recon/threat-model`);
  const surf = useJson<any[]>("recon-surf", `/runs/${runId}/recon/attack-surface`);
  const mods = useJson<any>("recon-mods", `/runs/${runId}/recon/module-map`);
  const seed = useJson("recon-seed", `/runs/${runId}/recon/seed`);
  const manifest = useJson("recon-manifest", `/runs/${runId}/recon/task-manifest`);

  return (
    <div style={{ marginTop: 12 }}>
      <Panel
        title="architecture.md"
        q={arch}
        render={(d) => (
          <div style={{ maxHeight: 520, overflow: "auto" }}>
            <Markdown src={(d as string) ?? ""} />
          </div>
        )}
      />
      <Panel title="Threat model" q={tm} render={(d) => <JsonView value={d} />} />
      <Panel
        title="Ranked attack surface"
        q={surf}
        render={(d) => {
          const rows = (d as any[]) ?? [];
          if (!rows.length) return <div className="dim">empty</div>;
          const cols = Object.keys(rows[0]);
          return (
            <div className="tbl-wrap">
              <table>
                <thead>
                  <tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i}>
                      {cols.map((c) => (
                        <td key={c} className="wrap">
                          {typeof r[c] === "object" ? JSON.stringify(r[c]) : String(r[c])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }}
      />
      <Panel title="Module map" q={mods} render={(d) => <JsonView value={d} />} />
      <Panel title="Seed" q={seed} render={(d) => <JsonView value={d} />} />
      <Panel title="Task manifest" q={manifest} render={(d) => <JsonView value={d} />} />
    </div>
  );
}
