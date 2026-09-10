import { Link, useParams } from "react-router-dom";
import { useCoverage } from "../api/hooks";
import { Q } from "../components/states";
import { CoverageMatrix } from "../components/CoverageMatrix";

export function Coverage() {
  const { runId = "" } = useParams();
  const q = useCoverage(runId);

  return (
    <div style={{ marginTop: 12 }}>
      <Q
        q={q}
        notFound={<div className="state-msg">No coverage yet — recon / hunt have not produced coverage for this run.</div>}
      >
        {(cov) => {
          const cls = (bucket: string) => `attack_class=${bucket.split("::")[1] ?? ""}`;
          return (
            <>
              <div className="dim" style={{ marginBottom: 10 }}>
                {cov.covered_cells} / {cov.matrix_cells} cells covered
                <span style={{ marginLeft: 16 }}>
                  <span className="tag tag-ok">productive</span>{" "}
                  <span className="tag tag-neutral">covered</span>{" "}
                  <span className="tag tag-warn">shallow (dashed)</span>
                </span>
              </div>

              <CoverageMatrix cells={cov.cells} />

              <h3>Gapfill buckets</h3>
              <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))" }}>
                {(["failed", "missing", "barren"] as const).map((b) => (
                  <div key={b} className="panel">
                    <strong>{b}</strong>{" "}
                    <span className="dim">({cov.gapfill_buckets[b]?.length ?? 0})</span>
                    <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                      {(cov.gapfill_buckets[b] ?? []).map((cell) => (
                        <li key={cell}>
                          <Link to={`/runs/${runId}/findings?${cls(cell)}`}>{cell}</Link>
                        </li>
                      ))}
                      {!(cov.gapfill_buckets[b] ?? []).length && <li className="dim">none</li>}
                    </ul>
                  </div>
                ))}
              </div>
            </>
          );
        }}
      </Q>
    </div>
  );
}
