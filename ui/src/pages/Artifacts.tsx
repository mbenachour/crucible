import { useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiFetch, ApiError, auth } from "../api/client";
import { useArtifacts } from "../api/hooks";
import { Q, Loading, ErrorState } from "../components/states";
import { Markdown } from "../components/Markdown";
import { bytes } from "../lib/format";
import type { ArtifactMeta } from "../api/types";

export function Artifacts() {
  const { runId = "" } = useParams();
  const q = useArtifacts(runId);
  const [sel, setSel] = useState<ArtifactMeta | null>(null);

  return (
    <div className="grid" style={{ gridTemplateColumns: "280px 1fr", marginTop: 12, alignItems: "start" }}>
      <div className="panel tree">
        <Q q={q}>
          {(list) => {
            const groups = new Map<string, ArtifactMeta[]>();
            for (const m of list) {
              const g = groups.get(m.kind) ?? [];
              g.push(m);
              groups.set(m.kind, g);
            }
            return [...groups.entries()].map(([kind, items]) => (
              <div key={kind} className="group">
                <div className="ghdr">{kind}</div>
                {items.map((m) => (
                  <div
                    key={m.path}
                    className={`file ${sel?.path === m.path ? "active" : ""}`}
                    onClick={() => setSel(m)}
                  >
                    <span className="mono">{m.path.split("/").pop()}</span>
                    <span className="dim">{bytes(m.bytes)}</span>
                  </div>
                ))}
              </div>
            ));
          }}
        </Q>
      </div>

      <div className="panel" style={{ minWidth: 0 }}>
        {sel ? <Viewer runId={runId} meta={sel} /> : <div className="dim">Select a file.</div>}
      </div>
    </div>
  );
}

function Viewer({ runId, meta }: { runId: string; meta: ArtifactMeta }) {
  const isText = /\.(md|txt|log|json|jsonl|diff|patch)$/i.test(meta.path);
  const raw = useQuery({
    queryKey: ["artifact", runId, meta.path],
    queryFn: () => apiFetch<string>(`/runs/${runId}/artifacts/${meta.path}`, { text: true }),
    retry: false,
    enabled: isText,
  });

  if (!isText) {
    const href = `${auth.getBase()}/runs/${runId}/artifacts/${meta.path}`;
    return (
      <div>
        <div className="mono">{meta.path}</div>
        <a href={href} target="_blank" rel="noreferrer">
          open / download ({bytes(meta.bytes)})
        </a>
      </div>
    );
  }
  if (raw.isLoading) return <Loading />;
  if (raw.error) {
    const e = raw.error as ApiError;
    return <ErrorState error={e} retry={() => raw.refetch()} />;
  }
  const body = raw.data ?? "";
  return (
    <div style={{ minWidth: 0 }}>
      <div className="mono dim" style={{ marginBottom: 8 }}>{meta.path}</div>
      {meta.path.endsWith(".md") ? (
        <div style={{ maxHeight: 560, overflow: "auto" }}>
          <Markdown src={body} />
        </div>
      ) : meta.path.endsWith(".json") ? (
        <pre className="code">{safePretty(body)}</pre>
      ) : (
        <pre className="code">{body}</pre>
      )}
    </div>
  );
}

function safePretty(s: string): string {
  try {
    return JSON.stringify(JSON.parse(s), null, 2);
  } catch {
    return s;
  }
}
