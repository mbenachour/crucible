import { useNavigate, useOutletContext, useParams } from "react-router-dom";
import { useRunFindings } from "../api/hooks";
import { Q } from "../components/states";
import { Pager, Sev, Status } from "../components/bits";
import { attackClass } from "../lib/format";
import { useUrlState } from "../lib/useUrlState";
import type { Run } from "../api/types";

const LIMIT = 50;
// Only findings that survived every gate are "reported"; everything else is
// raw Hunter output, kept one click away (issue #107).
const REPORTED = "reach_upheld";
const STATUSES = [
  "raw", "mechanical_failed", "mechanical_passed", "bug_upheld", "bug_refuted",
  "reach_upheld", "reach_refuted", "duplicate",
];
const SEVERITIES = ["critical", "high", "medium", "low"];

export function Findings() {
  const { runId = "" } = useParams();
  const nav = useNavigate();
  const run = (useOutletContext<{ run?: Run } | undefined>() ?? {}).run;
  const { get, getAll, getNum, set } = useUrlState();
  const offset = getNum("offset", 0);
  const showAll = get("view") === "all";
  const status = getAll("status");
  const severity = getAll("severity");

  const reportedCount = run ? run.counts[REPORTED] ?? 0 : null;
  const totalCount = run ? run.counts.total ?? 0 : null;

  const query = {
    status: showAll ? status : [REPORTED],
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
        <button
          className={showAll ? "" : "primary"}
          onClick={() => set({ view: "", status: [], offset: 0 })}
        >
          reported{reportedCount != null ? ` (${reportedCount})` : ""}
        </button>
        <button
          className={showAll ? "primary" : ""}
          onClick={() => set({ view: "all", offset: 0 })}
        >
          all raw output{totalCount != null ? ` (${totalCount})` : ""}
        </button>
      </div>
      {showAll && (
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
      )}
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
              {(showAll && status.length) || severity.length || get("attack_class") ? (
                "No findings match these filters."
              ) : showAll || !totalCount ? (
                "No findings for this run."
              ) : (
                <>
                  Nothing survived validation for this run.{" "}
                  {totalCount} raw finding{totalCount === 1 ? " was" : "s were"} rejected or
                  folded as duplicates —{" "}
                  <a href="#" onClick={(e) => { e.preventDefault(); set({ view: "all", offset: 0 }); }}>
                    view all raw output
                  </a>
                  .
                </>
              )}
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
