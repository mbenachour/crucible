import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, auth } from "../api/client";
import { useTriggerRun } from "../api/hooks";

const SPEC_HINT = "owner/repo or https://github.com/owner/repo.git";
// Loose client-side mirror of the API's shape check (crucible/repo_acquire.py) —
// the API is the real gate; this just avoids an obviously-wrong round trip.
const SPEC_RE = /^[\w.-]+\/[\w.-]+$|^https:\/\/\S+$/;
const REF_RE = /^[\w./-]{1,200}$/;

export interface NewRunPrefill {
  repo: string;
  ref: string;
}

export function NewRunModal({
  open,
  onClose,
  prefill,
}: {
  open: boolean;
  onClose: () => void;
  prefill?: NewRunPrefill;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const firstFieldRef = useRef<HTMLInputElement>(null);
  const nav = useNavigate();
  const trigger = useTriggerRun();

  const [repo, setRepo] = useState(prefill?.repo ?? "");
  const [ref, setRef] = useState(prefill?.ref ?? "");
  const [fieldError, setFieldError] = useState<string | null>(null);

  useEffect(() => {
    const d = dialogRef.current;
    if (!d) return;
    if (open) {
      setRepo(prefill?.repo ?? "");
      setRef(prefill?.ref ?? "");
      setFieldError(null);
      trigger.reset();
      if (!d.open) d.showModal();
      // focus after the dialog paints
      setTimeout(() => firstFieldRef.current?.focus(), 0);
    } else if (d.open) {
      d.close();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const hasToken = !!auth.getToken();

  function submit() {
    const spec = repo.trim();
    if (!SPEC_RE.test(spec)) {
      setFieldError(`repo must look like ${SPEC_HINT}`);
      return;
    }
    if (ref.trim() && !REF_RE.test(ref.trim())) {
      setFieldError("ref must be a plain branch/tag/commit name");
      return;
    }
    setFieldError(null);
    trigger.mutate(
      { repo: spec, ref: ref.trim() || undefined },
      {
        onSuccess: (out) => {
          onClose();
          nav(`/runs/${out.run_id}`);
        },
      },
    );
  }

  const err = trigger.error as ApiError | undefined;
  const serverMessage =
    err instanceof ApiError
      ? err.status === 422
        ? err.detail || err.message
        : err.status === 429
          ? err.detail || "too many runs in progress — try again shortly"
          : err.status === 401 || err.status === 403
            ? "needs a write token — set one in the top bar"
            : err.message
      : null;

  return (
    <dialog
      ref={dialogRef}
      onClose={onClose}
      onCancel={onClose}
      style={{
        border: "1px solid var(--border)", borderRadius: 10, padding: 0,
        background: "var(--panel)", color: "var(--text)", width: "min(440px, 92vw)",
      }}
      onClick={(e) => {
        if (e.target === dialogRef.current) onClose(); // backdrop click
      }}
    >
      <form
        method="dialog"
        style={{ padding: 18 }}
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <h2 style={{ marginTop: 0 }}>New run</h2>

        <div className="grid" style={{ gap: 10 }}>
          <label>
            <div className="dim" style={{ marginBottom: 4 }}>Repository</div>
            <input
              ref={firstFieldRef}
              placeholder={SPEC_HINT}
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              style={{ width: "100%" }}
              autoComplete="off"
            />
          </label>
          <label>
            <div className="dim" style={{ marginBottom: 4 }}>Ref (optional)</div>
            <input
              placeholder="branch or tag"
              value={ref}
              onChange={(e) => setRef(e.target.value)}
              style={{ width: "100%" }}
              autoComplete="off"
            />
          </label>

          {fieldError && <div className="banner bad">{fieldError}</div>}
          {serverMessage && <div className="banner bad">{serverMessage}</div>}
          {!hasToken && (
            <div className="dim">
              No token set — fine if this API has no auth configured; if it does, set a write token in the top bar.
            </div>
          )}
        </div>

        <div className="row" style={{ justifyContent: "flex-end", marginTop: 16 }}>
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary" disabled={trigger.isPending || !repo.trim()}>
            {trigger.isPending ? "starting…" : "Start run"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
