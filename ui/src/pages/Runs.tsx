import { useNavigate } from "react-router-dom";
import { useRuns } from "../api/hooks";
import { Q } from "../components/states";
import { OutcomeTag, Pager, Time } from "../components/bits";
import { baseName, shortCommit } from "../lib/format";
import { useUrlState } from "../lib/useUrlState";

const LIMIT = 50;

export function Runs() {
  const nav = useNavigate();
  const { get, getNum, set } = useUrlState();
  const offset = getNum("offset", 0);
  const query = {
    repo: get("repo"),
    outcome: get("outcome"),
    language: get("language"),
    limit: LIMIT,
    offset,
  };
  const q = useRuns(query);

  return (
    <>
      <h1>Runs</h1>
      <div className="filterbar">
        <input
          placeholder="repo contains…"
          defaultValue={get("repo")}
          onKeyDown={(e) => e.key === "Enter" && set({ repo: (e.target as HTMLInputElement).value, offset: 0 })}
          onBlur={(e) => set({ repo: e.target.value, offset: 0 })}
        />
        <select value={get("outcome")} onChange={(e) => set({ outcome: e.target.value, offset: 0 })}>
          <option value="">any outcome</option>
          <option value="completed">completed</option>
          <option value="stopped_at_stub">stopped_at_stub</option>
          <option value="stopped_after_stage">stopped_after_stage</option>
          <option value="failed">failed</option>
        </select>
        <input
          placeholder="language"
          defaultValue={get("language")}
          onBlur={(e) => set({ language: e.target.value, offset: 0 })}
          style={{ width: 110 }}
        />
      </div>

      <Q q={q} empty={(d) => d.items.length === 0 && offset === 0}>
        {(d) =>
          d.items.length === 0 ? (
            <div className="state-msg">No runs match these filters.</div>
          ) : (
            <>
              <div className="tbl-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>repo</th>
                      <th>commit</th>
                      <th>lang</th>
                      <th>started</th>
                      <th>outcome</th>
                      <th>findings</th>
                      <th>recon</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.items.map((r) => (
                      <tr key={r.run_id} className="rowlink" onClick={() => nav(`/runs/${r.run_id}`)}>
                        <td title={r.repo_path}>{baseName(r.repo_path)}</td>
                        <td className="mono dim">{shortCommit(r.repo_commit)}</td>
                        <td>{r.primary_language || "—"}</td>
                        <td>
                          <Time v={r.created_at} />
                        </td>
                        <td>
                          <OutcomeTag v={r.outcome} />
                        </td>
                        <td>
                          {r.counts.total ?? 0}
                          {r.counts.reach_upheld ? (
                            <span className="tag tag-ok" style={{ marginLeft: 6 }}>
                              {r.counts.reach_upheld} upheld
                            </span>
                          ) : null}
                        </td>
                        <td className="dim">{r.recon_quality || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pager total={d.total} limit={LIMIT} offset={offset} onPage={(o) => set({ offset: o })} />
            </>
          )
        }
      </Q>
    </>
  );
}
