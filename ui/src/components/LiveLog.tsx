import { useEffect, useRef, useState } from "react";
import { auth } from "../api/client";

type Status = "connecting" | "live" | "ended" | "error";

/**
 * Tails `GET /runs/{id}/log/stream` live. Deliberately not EventSource/WebSocket
 * — a plain chunked response read via fetch()'s ReadableStream, so the same
 * Authorization header as every other request just works (EventSource can't
 * set custom headers; see crucible/api/routers/artifacts.py::stream_log).
 */
export function LiveLog({ runId, tailLines = 300 }: { runId: string; tailLines?: number }) {
  const [lines, setLines] = useState<string[]>([]);
  const [status, setStatus] = useState<Status>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [follow, setFollow] = useState(true);
  const boxRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLines([]);
    setStatus("connecting");
    setError(null);

    (async () => {
      let buffer = "";
      try {
        const headers: Record<string, string> = {};
        const token = auth.getToken();
        if (token) headers["Authorization"] = `Bearer ${token}`;

        const res = await fetch(
          `${auth.getBase()}/runs/${runId}/log/stream?tail_lines=${tailLines}`,
          { headers, signal: controller.signal },
        );
        if (!res.ok || !res.body) {
          const body = await res.json().catch(() => null);
          setError(body?.error ? `${body.error}${body.detail ? `: ${body.detail}` : ""}` : `HTTP ${res.status}`);
          setStatus("error");
          return;
        }
        setStatus("live");

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parts = buffer.split("\n");
          buffer = parts.pop() ?? "";
          if (parts.length) setLines((prev) => [...prev, ...parts]);
        }
        if (buffer) setLines((prev) => [...prev, buffer]);
        setStatus("ended");
      } catch (e) {
        if (controller.signal.aborted) return; // unmounted / runId changed — not a real error
        setError(String((e as Error)?.message ?? e));
        setStatus("error");
      }
    })();

    return () => controller.abort();
  }, [runId, tailLines]);

  useEffect(() => {
    if (follow && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
  }, [lines, follow]);

  function onScroll() {
    const el = boxRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    setFollow(atBottom);
  }

  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <span className="dim">
          {status === "connecting" && "connecting…"}
          {status === "live" && (
            <>
              <span className="tag tag-ok">live</span> tailing run.log
            </>
          )}
          {status === "ended" && "stream ended (run finished)"}
          {status === "error" && <span className="tag tag-bad">{error}</span>}
        </span>
        <label className="row" style={{ gap: 4, fontSize: 12 }}>
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
          follow
        </label>
      </div>
      <pre
        ref={boxRef}
        onScroll={onScroll}
        className="code"
        style={{ maxHeight: 480, overflow: "auto" }}
      >
        {lines.length ? lines.join("\n") : status === "connecting" ? "" : "(empty)"}
      </pre>
    </div>
  );
}
