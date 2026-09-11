import { Link, useParams } from "react-router-dom";
import { useFinding, useFindingHistory } from "../api/hooks";
import { Q } from "../components/states";
import { Copy, Sev, Status, Time } from "../components/bits";
import { DiffView } from "../components/DiffView";
import type { ThreatModel } from "../api/types";

export function FindingDetail() {
  const { findingId = "" } = useParams();
  const q = useFinding(findingId);

  return (
    <Q q={q} notFound={<div className="state-msg">Finding <code>{findingId}</code> not found.</div>}>
      {(f) => {
        const tm = (f.threat_model || {}) as Partial<ThreatModel>;
        return (
          <>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h1 style={{ marginBottom: 0 }}>
                <Sev v={f.severity as string} /> {f.title}
              </h1>
              <Status v={f.status} />
            </div>
            <div className="dim" style={{ marginBottom: 12 }}>
              <code className="mono">
                {f.file_path}
                {f.line_start ? `:${f.line_start}-${f.line_end}` : ""}
              </code>{" "}
              · run <Link to={`/runs/${f.run_id}`}>{f.run_id}</Link> · <Time v={f.created_at} />
            </div>

            {f.status === "duplicate" && f.duplicate_of && (
              <div className="banner warn">
                Folded into canonical <Link to={`/findings/${f.duplicate_of}`}>{f.duplicate_of}</Link>.
              </div>
            )}

            <SeenBefore stableKey={f.stable_key} selfId={f.finding_id} />

            <div className="panel">
              <h3>Threat model</h3>
              <dl className="kv">
                <dt>attacker</dt>
                <dd>{tm.attacker || "—"}</dd>
                <dt>boundary crossed</dt>
                <dd>{tm.boundary_crossed || "—"}</dd>
                <dt>assumption broken</dt>
                <dd>{tm.assumption_broken || "—"}</dd>
              </dl>
            </div>

            <h2>Description</h2>
            <div className="panel" style={{ whiteSpace: "pre-wrap" }}>{f.description || "—"}</div>

            <h2>PoC test</h2>
            <div>
              <Copy text={f.poc_test} label="copy poc" />
              <pre className="code">{f.poc_test || "—"}</pre>
            </div>

            <h2>Proposed patch</h2>
            <DiffView patch={f.proposed_patch} />

            <h2>Provenance</h2>
            <pre className="code">{JSON.stringify(f.provenance, null, 2)}</pre>

            <h2>Validation trail</h2>
            {f.validation_trail && f.validation_trail.length ? (
              <div className="trail">
                {f.validation_trail.map((v, i) => {
                  const ok = v.verdict === "upheld" || v.verdict === "mechanical_passed";
                  return (
                    <div key={i} className={`step ${ok ? "ok" : "bad"}`}>
                      <div className="row">
                        <strong>{v.pass_name}</strong>
                        <span className={`tag ${ok ? "tag-ok" : "tag-bad"}`}>{v.verdict}</span>
                        {v.model && <span className="dim mono">{v.model}</span>}
                        {v.response_class && <span className="dim">{v.response_class}</span>}
                      </div>
                      {v.reasoning && <div style={{ marginTop: 4, whiteSpace: "pre-wrap" }}>{v.reasoning}</div>}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="dim">No validation recorded.</div>
            )}
          </>
        );
      }}
    </Q>
  );
}

function SeenBefore({ stableKey, selfId }: { stableKey: string; selfId: string }) {
  const h = useFindingHistory(stableKey);
  const others = (h.data?.items ?? []).filter((x) => x.finding_id !== selfId);
  if (!others.length) return null;
  return (
    <div className="banner warn">
      This structural bug (<code className="mono">{stableKey}</code>) also appears in:{" "}
      {others.map((o, i) => (
        <span key={o.finding_id}>
          {i > 0 && ", "}
          <Link to={`/findings/${o.finding_id}`}>{o.run_id}</Link> ({o.status})
        </span>
      ))}
    </div>
  );
}
