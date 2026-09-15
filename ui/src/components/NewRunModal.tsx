import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, auth } from "../api/client";
import { useConfigModels, useTriggerRun } from "../api/hooks";
import { ModelSelect } from "./ModelSelect";
import type { ModelEndpoint, ModelOverride } from "../api/types";

const SPEC_HINT = "owner/repo or https://github.com/owner/repo.git";
// Loose client-side mirror of the API's shape check (crucible/repo_acquire.py) —
// the API is the real gate; this just avoids an obviously-wrong round trip.
const SPEC_RE = /^[\w.-]+\/[\w.-]+$|^https:\/\/\S+$/;
const REF_RE = /^[\w./-]{1,200}$/;

const ROLE_LABELS: Record<string, string> = {
  recon: "Recon", hunter: "Hunter", validator_bug: "Validator (bug)", validator_reach: "Validator (reach)",
};

type FieldEdits = Partial<Record<"model" | "temperature" | "base_url", string>>;

/** Collapsed by default — "use host config", and no `GET /config/models`
 * call happens until the user actually expands it: zero added cost on the
 * default path. Prefilled from that response; a field left unchanged from
 * its effective value never enters the submitted override (issue #77). */
function ModelsOverrideSection({
  open,
  onChange,
}: {
  open: boolean;
  onChange: (models: Record<string, ModelOverride> | undefined) => void;
}) {
  const [sectionOpen, setSectionOpen] = useState(false);
  const cfg = useConfigModels(undefined, open && sectionOpen);
  const [edits, setEdits] = useState<Record<string, FieldEdits>>({});

  useEffect(() => {
    if (!open) {
      setSectionOpen(false);
      setEdits({});
    }
  }, [open]);

  useEffect(() => {
    const roles = cfg.data?.roles;
    if (!roles) {
      onChange(undefined);
      return;
    }
    const models: Record<string, ModelOverride> = {};
    for (const [role, roleEdits] of Object.entries(edits)) {
      const eff = roles[role];
      if (!eff) continue;
      const diff: ModelOverride = {};
      if (roleEdits.model !== undefined && roleEdits.model !== eff.model) {
        diff.model = roleEdits.model;
      }
      if (roleEdits.temperature !== undefined) {
        const n = Number(roleEdits.temperature);
        if (!Number.isNaN(n) && n !== eff.temperature) diff.temperature = n;
      }
      if (roleEdits.base_url !== undefined && roleEdits.base_url !== eff.base_url) {
        diff.base_url = roleEdits.base_url;
      }
      if (Object.keys(diff).length > 0) models[role] = diff;
    }
    onChange(Object.keys(models).length > 0 ? models : undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [edits, cfg.data]);

  function setField(role: string, field: keyof FieldEdits, value: string) {
    setEdits((s) => ({ ...s, [role]: { ...s[role], [field]: value } }));
  }

  return (
    <details
      style={{ marginTop: 2 }}
      onToggle={(e) => setSectionOpen(e.currentTarget.open)}
    >
      <summary className="dim" style={{ cursor: "pointer" }}>
        Models — use host config (click to override)
      </summary>
      {cfg.isLoading && <div className="dim" style={{ marginTop: 8 }}>loading effective config…</div>}
      {cfg.data?.roles && (
        <div className="grid" style={{ gap: 8, marginTop: 8 }}>
          {Object.entries(cfg.data.roles).map(([role, eff]) => (
            <RoleRow
              key={role}
              role={role}
              eff={eff}
              edits={edits[role] ?? {}}
              onEdit={(field, value) => setField(role, field, value)}
            />
          ))}
          <div className="dim" style={{ fontSize: 12 }}>
            Leave a field as shown to keep the host's config for that role.
          </div>
        </div>
      )}
    </details>
  );
}

function RoleRow({
  role,
  eff,
  edits,
  onEdit,
}: {
  role: string;
  eff: ModelEndpoint;
  edits: FieldEdits;
  onEdit: (field: keyof FieldEdits, value: string) => void;
}) {
  return (
    <fieldset style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 8 }}>
      <legend className="dim" style={{ padding: "0 4px" }}>{ROLE_LABELS[role] ?? role}</legend>
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        <ModelSelect
          ariaLabel={`${role} model`}
          value={edits.model ?? eff.model}
          onChange={(v) => onEdit("model", v)}
          style={{ width: 220 }}
        />
        <input
          aria-label={`${role} temperature`}
          title={`source: ${eff.source.temperature}`}
          value={edits.temperature ?? String(eff.temperature)}
          onChange={(e) => onEdit("temperature", e.target.value)}
          style={{ width: 56 }}
        />
        <input
          aria-label={`${role} base url`}
          title={`source: ${eff.source.base_url}`}
          value={edits.base_url ?? eff.base_url}
          onChange={(e) => onEdit("base_url", e.target.value)}
          style={{ width: 160 }}
        />
      </div>
    </fieldset>
  );
}

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
  const [modelOverride, setModelOverride] = useState<Record<string, ModelOverride> | undefined>(undefined);

  useEffect(() => {
    const d = dialogRef.current;
    if (!d) return;
    if (open) {
      setRepo(prefill?.repo ?? "");
      setRef(prefill?.ref ?? "");
      setFieldError(null);
      setModelOverride(undefined);
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
      { repo: spec, ref: ref.trim() || undefined, models: modelOverride },
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
            ? "needs a write token — set one in Settings"
            : err.message
      : null;

  return (
    <dialog
      ref={dialogRef}
      onClose={onClose}
      onCancel={onClose}
      style={{
        border: "1px solid var(--border)", borderRadius: 10, padding: 0,
        background: "var(--panel)", color: "var(--text)", width: "min(560px, 94vw)",
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

          <ModelsOverrideSection open={open} onChange={setModelOverride} />

          {fieldError && <div className="banner bad">{fieldError}</div>}
          {serverMessage && <div className="banner bad">{serverMessage}</div>}
          {!hasToken && (
            <div className="dim">
              No token set — fine if this API has no auth configured; if it does, set a write token in Settings.
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
