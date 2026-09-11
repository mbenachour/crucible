import { useState } from "react";
import { absTime, outcomeClass, relTime, severityClass, statusClass } from "../lib/format";

export const Sev = ({ v }: { v: string | null | undefined }) => (
  <span className={severityClass(v)}>{(v || "?").toUpperCase()}</span>
);

export const Status = ({ v }: { v: string }) => <span className={statusClass(v)}>{v}</span>;

export const OutcomeTag = ({ v }: { v: string }) =>
  v ? <span className={outcomeClass(v)}>{v}</span> : <span className="dim">running</span>;

/** Prefer this over OutcomeTag whenever a row might be an in-flight launch. */
export function StatusOrLaunchTag({ outcome, cloneStatus }: { outcome: string; cloneStatus?: string }) {
  switch (cloneStatus) {
    case "pending":
      return <span className="tag tag-neutral">queued</span>;
    case "cloning":
      return <span className="tag tag-neutral">cloning…</span>;
    case "clone_failed":
      return <span className="tag tag-bad">clone failed</span>;
    default:
      return <OutcomeTag v={outcome} />;
  }
}

export const Time = ({ v }: { v: string | null | undefined }) => (
  <span title={absTime(v)}>{relTime(v)}</span>
);

export function Copy({ text, label = "copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="copybtn"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1200);
        } catch {
          /* clipboard blocked */
        }
      }}
    >
      {done ? "copied" : label}
    </button>
  );
}

export function Pager({
  total,
  limit,
  offset,
  onPage,
}: {
  total: number;
  limit: number;
  offset: number;
  onPage: (offset: number) => void;
}) {
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));
  return (
    <div className="pager">
      <button disabled={offset <= 0} onClick={() => onPage(Math.max(0, offset - limit))}>
        ← prev
      </button>
      <span>
        page {page} / {pages} · {total} total
      </span>
      <button disabled={offset + limit >= total} onClick={() => onPage(offset + limit)}>
        next →
      </button>
    </div>
  );
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="row" style={{ gap: 4 }}>
      <span className="dim">{label}</span>
      {children}
    </label>
  );
}
