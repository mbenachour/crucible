import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useResolveWish, useRunWishes, useWishes } from "../api/hooks";
import { ApiError } from "../api/client";
import { Q } from "../components/states";
import { Time } from "../components/bits";
import type { Wish } from "../api/types";

export function Wishlist() {
  const { runId } = useParams();
  const [status, setStatus] = useState(runId ? "" : "open");
  const scoped = !!runId;
  const globalQ = useWishes({ status, limit: 100 }, !scoped);
  const runQ = useRunWishes(runId ?? "", { status, limit: 100 }, scoped);
  const q = scoped ? runQ : globalQ;

  return (
    <div style={{ marginTop: scoped ? 12 : 0 }}>
      {!scoped && <h1>Wishlist</h1>}
      <div className="filterbar">
        <span className="dim">status:</span>
        {["", "open", "resolved", "requeued"].map((s) => (
          <button key={s || "any"} className={status === s ? "primary" : ""} onClick={() => setStatus(s)} style={{ padding: "2px 8px", fontSize: 12 }}>
            {s || "any"}
          </button>
        ))}
      </div>
      <Q q={q}>
        {(d) =>
          d.items.length === 0 ? (
            <div className="state-msg">No wishes.</div>
          ) : (
            <div className="grid">
              {groupByRun(d.items, scoped).map(([rid, items]) => (
                <div key={rid}>
                  {!scoped && (
                    <h3>
                      run <Link to={`/runs/${rid}`}>{rid}</Link>
                    </h3>
                  )}
                  {items.map((w) => (
                    <WishCard key={w.id} w={w} />
                  ))}
                </div>
              ))}
            </div>
          )
        }
      </Q>
    </div>
  );
}

function groupByRun(items: Wish[], scoped: boolean): [string, Wish[]][] {
  if (scoped) return [["", items]];
  const m = new Map<string, Wish[]>();
  for (const w of items) {
    const arr = m.get(w.run_id) ?? [];
    arr.push(w);
    m.set(w.run_id, arr);
  }
  return [...m.entries()];
}

function WishCard({ w }: { w: Wish }) {
  const [note, setNote] = useState("");
  const resolve = useResolveWish();
  const forbidden = resolve.error instanceof ApiError && resolve.error.status === 403;

  return (
    <div className="panel" style={{ marginBottom: 10 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>
          <span className="mono">{w.blocked_task_id}</span> · {w.need}
        </strong>
        <span className={`tag ${w.status === "open" ? "tag-warn" : "tag-muted"}`}>{w.status}</span>
      </div>
      {w.context && <div style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>{w.context}</div>}
      <div className="dim" style={{ marginTop: 6 }}>
        <Time v={w.created_at} />
      </div>
      {w.status === "open" && (
        <div className="row" style={{ marginTop: 8 }}>
          <input placeholder="note (optional)" value={note} onChange={(e) => setNote(e.target.value)} style={{ flex: 1 }} />
          <button
            className="primary"
            disabled={resolve.isPending}
            onClick={() => resolve.mutate({ id: w.id, note: note || undefined })}
          >
            {resolve.isPending ? "resolving…" : "resolve"}
          </button>
        </div>
      )}
      {forbidden && <div className="dim" style={{ marginTop: 6, color: "var(--bad-fg)" }}>needs a write token — set one in the top bar</div>}
      <div className="dim" style={{ marginTop: 6 }}>
        Resolving does not re-run the task — do that with <code>crucible run --resume {w.run_id}</code>.
      </div>
    </div>
  );
}
