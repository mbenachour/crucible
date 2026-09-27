// Plain-language vocabulary for the Live progress view. Everything a user
// reads on that page goes through here — no stage names, node ids, or task ids.

const ATTACK_CLASS: Record<string, string> = {
  api_misuse: "misused security APIs",
  argument_injection: "argument injection",
  auth_bypass: "authentication bypasses",
  cert_pinning_bypass: "TLS certificate-pinning gaps",
  command_injection: "command injection",
  deeplink_handling: "unsafe deep links",
  dynamic_dispatch: "unsafe dynamic dispatch",
  excessive_permissions: "over-broad permissions",
  exported_component: "exposed app components",
  exposed_secret: "exposed secrets",
  format_string: "format-string bugs",
  hardcoded_secret: "hard-coded secrets",
  injection_passthrough: "injection pass-through",
  insecure_storage: "insecure data storage",
  integer_overflow: "integer overflows",
  memory_oob_read: "out-of-bounds reads",
  memory_oob_write: "out-of-bounds writes",
  misconfiguration: "misconfigurations",
  path_traversal: "path traversal",
  protocol_parsing: "parser bugs",
  sql_injection: "SQL injection",
  ssrf: "server-side request forgery",
  supply_chain: "supply-chain risks",
  template_injection: "template injection",
  unsafe_deserialization: "unsafe deserialization",
  use_after_free: "use-after-free bugs",
  webview_injection: "WebView injection",
  xss: "XSS",
  xxe: "XML external entities",
};

/** "sql_injection" -> "SQL injection"; unknown classes degrade to spaced words. */
export function attackName(cls: string): string {
  return ATTACK_CLASS[cls] ?? cls.replace(/[_-]+/g, " ").trim();
}

/** A hunt area as a phrase that follows a verb: "in api/payments", "across the codebase". */
export function areaPhrase(area: string | undefined | null): string {
  const a = (area || "").trim();
  if (!a || a === "." || a === "/") return "across the codebase";
  return `in ${a}`;
}

export function areaName(area: string | undefined | null): string {
  const a = (area || "").trim();
  return !a || a === "." || a === "/" ? "whole codebase" : a;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

/** 75 -> "1m 15s", 4000 -> "1h 6m"; coarse on purpose. */
export function duration(secs: number | null | undefined): string {
  if (secs == null || !Number.isFinite(secs) || secs < 0) return "—";
  const s = Math.round(secs);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** An ETA phrased with honest imprecision: "about 4 min", "under a minute". */
export function etaPhrase(secs: number | null | undefined): string | null {
  if (secs == null || !Number.isFinite(secs) || secs <= 0) return null;
  if (secs < 60) return "under a minute";
  const m = Math.round(secs / 60);
  if (m < 60) return `about ${m} min`;
  const h = Math.floor(m / 60);
  const rm = Math.round((m % 60) / 5) * 5;
  return rm ? `about ${h}h ${rm}m` : `about ${h}h`;
}

/** Up to `n` items joined naturally: "a, b and 3 more". */
export function listPhrase(items: string[], n = 2): string {
  const uniq = [...new Set(items)];
  if (uniq.length <= n) {
    return uniq.length <= 1 ? (uniq[0] ?? "") : `${uniq.slice(0, -1).join(", ")} and ${uniq[uniq.length - 1]}`;
  }
  return `${uniq.slice(0, n).join(", ")} and ${uniq.length - n} more`;
}

// One parser for API timestamps, shared with the run header's relative times.
export { apiInstant } from "../lib/format";

/** Hunt progress for the current sweep. Deliberately says nothing about how
 * many areas run in parallel — that's a config value, not progress (#103). */
export function areasChecked(done: number, total: number): string {
  return `${done} of ${plural(total, "area")} checked`;
}

/** Last-resort wording for a backend enum we have no phrase for: never show
 * the raw identifier ("seed_only" -> "seed only"). */
export function enumWords(v: string): string {
  return v.replace(/[_-]+/g, " ").trim();
}

export function capitalize(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}
