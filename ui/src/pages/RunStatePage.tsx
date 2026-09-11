import { useParams } from "react-router-dom";
import { useRunState } from "../api/hooks";
import { ApiError } from "../api/client";
import { ErrorState, Loading } from "../components/states";
import { Time } from "../components/bits";

export function RunStatePage() {
  const { runId = "" } = useParams();
  const q = useRunState(runId, true);

  if (q.isLoading) return <Loading />;
  if (q.error) {
    if (q.error instanceof ApiError && q.error.status === 404)
      return <div className="state-msg">This run has no execution state (it never checkpointed).</div>;
    return <ErrorState error={q.error} retry={() => q.refetch()} />;
  }
  const s = q.data!;
  const running = !!s.next_node;

  return (
    <div style={{ marginTop: 12 }} className="grid">
      <div className="row">
        <span className="tag tag-neutral" style={{ fontSize: 13 }}>
          next: {s.next_node ?? "done"}
        </span>
        {running && <span className="dim">polling every 5s</span>}
        <span className="dim">
          checkpoint <Time v={s.checkpoint_ts} />
        </span>
        <button onClick={() => q.refetch()}>refresh</button>
      </div>

      <div className="tiles">
        <div className="tile"><div className="k">recon quality</div><div className="v">{s.recon_quality || "—"}</div></div>
        <div className="tile"><div className="k">cycles</div><div className="v">{s.cycle_count}</div></div>
        <div className="tile"><div className="k">continuations</div><div className="v">{s.continuation_count}</div></div>
        <div className="tile"><div className="k">forks</div><div className="v">{s.fork_count}</div></div>
        <div className="tile"><div className="k">token spend</div><div className="v">{s.token_spend.toLocaleString()}</div></div>
        <div className="tile"><div className="k">completed cells</div><div className="v">{s.completed_cell_count}</div></div>
      </div>

      <div>
        <h3>Pending hunt queue ({s.pending_hunt_count})</h3>
        {s.pending_hunts.length ? (
          <>
            <div className="tbl-wrap">
              <table>
                <thead>
                  <tr>
                    <th>task</th><th>area</th><th>attack class</th><th>chunk</th><th>seed path</th><th className="wrap">scope hint</th>
                  </tr>
                </thead>
                <tbody>
                  {s.pending_hunts.map((t) => (
                    <tr key={t.task_id}>
                      <td className="mono">{t.task_id}</td>
                      <td>{t.area}</td>
                      <td className="mono">{t.attack_class}</td>
                      <td>{t.chunk_type}</td>
                      <td className="mono dim">{t.seed_path || "—"}</td>
                      <td className="wrap dim">{t.scope_hint}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {s.pending_hunts.length < s.pending_hunt_count && (
              <div className="dim" style={{ marginTop: 6 }}>
                showing {s.pending_hunts.length} of {s.pending_hunt_count}
              </div>
            )}
          </>
        ) : (
          <div className="dim">queue empty</div>
        )}
      </div>

      {s.completed_cells.length > 0 && (
        <details>
          <summary className="dim">completed cells ({s.completed_cell_count})</summary>
          <pre className="code" style={{ marginTop: 8 }}>{s.completed_cells.join("\n")}</pre>
        </details>
      )}
    </div>
  );
}
