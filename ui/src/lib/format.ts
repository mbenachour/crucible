/** An API timestamp as epoch ms. The API serializes naive UTC datetimes
 * ("2026-09-26T20:09:29.7", no zone), which `new Date()` would read as
 * browser-local time — so a zone-less value is taken as UTC. */
export function apiInstant(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const zoned = /(Z|[+-]\d\d:?\d\d)$/.test(iso) ? iso : `${iso}Z`;
  const t = new Date(zoned).getTime();
  return Number.isNaN(t) ? null : t;
}

/** "5m ago" / "in 2h". Reading a zone-less API timestamp as local time used
 * to put every timestamp hours off (issue #103: "started in 4h" for a run
 * that had just finished); a few seconds of clock skew reads as "just now". */
export function relTime(iso: string | null | undefined, now: number = Date.now()): string {
  const t = apiInstant(iso);
  if (t === null) return "—";
  const s = Math.floor((now - t) / 1000);
  if (s > -60 && s < 5) return "just now";
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
  // floor, not round: 59m 50s is "59m ago", never "60m ago"
  const n = Math.floor(abs / div);
  return s >= 0 ? `${n}${label} ago` : `in ${n}${label}`;
}

export function absTime(iso: string | null | undefined): string {
  const t = apiInstant(iso);
  return t === null ? "" : new Date(t).toLocaleString();
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
    cancelled: "neutral",
  };
  return `tag tag-${map[outcome] ?? "neutral"}`;
}

export function shortCommit(c: string): string {
  return (c || "").slice(0, 12) || "—";
}

export function baseName(p: string): string {
  return p.split("/").filter(Boolean).pop() || p;
}

/** A run's human-recognizable identity (issue #84) — prefer `source_spec`
 * (what the user actually typed: "owner/repo" or a git URL) over
 * `baseName(repo_path)`, which for most clones is just the literal string
 * "repo" (the checkout directory name) and tells you nothing. A git URL
 * source_spec is shortened to "owner/repo" to match the other form; only a
 * CLI-triggered run with no source_spec at all falls back to the clone path. */
export function repoLabel(r: { repo_path: string; source_spec?: string }): string {
  const spec = (r.source_spec || "").trim();
  if (spec) {
    const m = spec.match(/[:/]([\w.-]+\/[\w.-]+?)(\.git)?\/?$/);
    return m ? m[1] : spec;
  }
  return baseName(r.repo_path);
}

/** attack class recovered from `provenance.hunter_prompt_version` ("<class>@<ver>") */
export function attackClass(f: { provenance?: Record<string, unknown> }): string {
  const pv = String(f.provenance?.["hunter_prompt_version"] ?? "");
  return pv.split("@")[0] || "";
}
