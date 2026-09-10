import type { ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { ApiError } from "../api/client";

export function Loading({ rows = 5 }: { rows?: number }) {
  return (
    <div className="grid" aria-busy="true" aria-label="loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton" style={{ height: 14, width: `${90 - i * 6}%` }} />
      ))}
    </div>
  );
}

export function Empty({ message, hint, action }: { message: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="state-msg">
      <div>{message}</div>
      {hint && <div className="dim" style={{ marginTop: 6 }}>{hint}</div>}
      {action && <div style={{ marginTop: 12 }}>{action}</div>}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const e = error as ApiError | Error;
  const status = e instanceof ApiError ? e.status : undefined;
  const detail = e instanceof ApiError ? e.detail : undefined;
  return (
    <div className="state-msg">
      <div className="banner bad" style={{ display: "inline-block" }}>
        {status ? `${status} — ` : ""}
        {e?.message || "request failed"}
      </div>
      {detail && <div className="dim" style={{ marginTop: 8 }}>{detail}</div>}
      {status === 401 && <div className="dim" style={{ marginTop: 8 }}>Enter an API token in the top bar.</div>}
      {retry && (
        <div style={{ marginTop: 12 }}>
          <button onClick={retry}>Retry</button>
        </div>
      )}
    </div>
  );
}

/** Standard query render: loading / error / notFound / empty / data. */
export function Q<T>({
  q,
  children,
  empty,
  notFound,
}: {
  q: UseQueryResult<T>;
  children: (data: T) => ReactNode;
  empty?: (data: T) => boolean;
  notFound?: ReactNode;
}) {
  if (q.isLoading) return <Loading />;
  if (q.error) {
    if (notFound && q.error instanceof ApiError && q.error.status === 404) return <>{notFound}</>;
    return <ErrorState error={q.error} retry={() => q.refetch()} />;
  }
  if (q.data === undefined) return <Loading />;
  if (empty && empty(q.data)) return <Empty message="Nothing here yet." />;
  return <>{children(q.data)}</>;
}
