import { useNavigate, useParams } from "react-router-dom";
import { useRunFindings } from "../api/hooks";
import { Q } from "../components/states";
import { Pager, Sev, Status } from "../components/bits";
import { attackClass } from "../lib/format";
import { useUrlState } from "../lib/useUrlState";

const LIMIT = 50;
const STATUSES = [
  "raw", "mechanical_failed", "mechanical_passed", "bug_upheld", "bug_refuted",
  "reach_upheld", "reach_refuted", "duplicate",
];
const SEVERITIES = ["critical", "high", "medium", "low"];

export function Findings() {
  const { runId = "" } = useParams();
  const nav = useNavigate();
  const { get, getAll, getNum, set } = useUrlState();
  const offset = getNum("offset", 0);
  const status = getAll("status");
  const severity = getAll("severity");

  const query = {
    status,
    severity,
    attack_class: get("attack_class"),
    order: get("order", "severity"),
    limit: LIMIT,
    offset,
  };
  const q = useRunFindings(runId, query);

  const toggle = (key: string, val: string) => {
    const cur = getAll(key);
    set({ [key]: cur.includes(val) ? cur.filter((x) => x !== val) : [...cur, val], offset: 0 });
  };

  return (
    <div style={{ marginTop: 12 }}>
      <div className="filterbar">
        <span className="dim">status:</span>
        {STATUSES.map((s) => (
          <button
            key={s}
            onClick={() => toggle("status", s)}
            className={status.includes(s) ? "primary" : ""}
            style={{ padding: "2px 8px", fontSize: 12 }}
          >
            {s}
          </button>
        ))}
      </div>
      <div className="filterbar">
        <span className="dim">severity:</span>
        {SEVERITIES.map((s) => (
          <button
            key={s}
            onClick={() => toggle("severity", s)}
            className={severity.includes(s) ? "primary" : ""}
            style={{ padding: "2px 8px", fontSize: 12 }}
          >
            {s}
          </button>
        ))}
        <input
          placeholder="attack_class"
          defaultValue={get("attack_class")}
          onBlur={(e) => set({ attack_class: e.target.value, offset: 0 })}
          style={{ width: 160 }}
        />
        <select value={get("order", "severity")} onChange={(e) => set({ order: e.target.value })}>
          <option value="severity">by severity</option>
          <option value="created_at">newest</option>
        </select>
      </div>

      <Q q={q}>
        {(d) =>
          d.items.length === 0 ? (
            <div className="state-msg">
              {status.length || severity.length || get("attack_class")
                ? "No findings match these filters."
                : "No findings for this run."}
            </div>
          ) : (
            <>
              <div className="tbl-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>sev</th>
                      <th className="wrap">title</th>
                      <th>file</th>
                      <th>status</th>
                      <th>attack class</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.items.map((f) => (
                      <tr key={f.finding_id} className="rowlink" onClick={() => nav(`/findings/${f.finding_id}`)}>
                        <td>
                          <Sev v={f.severity as string} />
                        </td>
                        <td className="wrap">{f.title}</td>
                        <td className="mono dim">
                          {f.file_path}
                          {f.line_start ? `:${f.line_start}` : ""}
                        </td>
                        <td>
                          <Status v={f.status} />
                        </td>
                        <td className="mono dim">{attackClass(f)}</td>
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
    </div>
  );
}
