export function relTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const s = Math.round((Date.now() - t) / 1000);
  const abs = Math.abs(s);
  const units: [number, string][] = [
    [60, "s"],
    [3600, "m"],
    [86400, "h"],
    [2592000, "d"],
    [31536000, "mo"],
    [Infinity, "y"],
  ];
  let div = 1;
  let label = "s";
  for (let i = 0; i < units.length; i++) {
    if (abs < units[i][0]) {
      label = units[i][1];
      div = i === 0 ? 1 : units[i - 1][0];
      break;
    }
  }
  const n = Math.round(abs / div);
  return s >= 0 ? `${n}${label} ago` : `in ${n}${label}`;
}

export function absTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString();
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export const SEVERITY_ORDER = ["critical", "high", "medium", "low"] as const;

export function severityClass(sev: string | null | undefined): string {
  return `sev sev-${(sev || "unknown").toLowerCase()}`;
}

// Funnel status → tone. Kept in one place so every badge agrees.
export function statusClass(status: string): string {
  const map: Record<string, string> = {
    reach_upheld: "ok",
    bug_upheld: "ok",
    mechanical_passed: "ok",
    raw: "neutral",
    duplicate: "muted",
    mechanical_failed: "bad",
    bug_refuted: "bad",
    reach_refuted: "bad",
  };
  return `tag tag-${map[status] ?? "neutral"}`;
}

export function outcomeClass(outcome: string): string {
  const map: Record<string, string> = {
    completed: "ok",
    stopped_at_stub: "warn",
    stopped_after_stage: "warn",
    failed: "bad",
  };
  return `tag tag-${map[outcome] ?? "neutral"}`;
}

export function shortCommit(c: string): string {
  return (c || "").slice(0, 12) || "—";
}

export function baseName(p: string): string {
  return p.split("/").filter(Boolean).pop() || p;
}

/** attack class recovered from `provenance.hunter_prompt_version` ("<class>@<ver>") */
export function attackClass(f: { provenance?: Record<string, unknown> }): string {
  const pv = String(f.provenance?.["hunter_prompt_version"] ?? "");
  return pv.split("@")[0] || "";
}
