import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { absTime, apiInstant, attackClass, outcomeClass, relTime, repoLabel, severityClass, statusClass } from "./format";

describe("class maps", () => {
  it("severity", () => {
    expect(severityClass("HIGH")).toBe("sev sev-high");
    expect(severityClass(null)).toBe("sev sev-unknown");
  });
  it("status tone", () => {
    expect(statusClass("reach_upheld")).toBe("tag tag-ok");
    expect(statusClass("mechanical_failed")).toBe("tag tag-bad");
    expect(statusClass("duplicate")).toBe("tag tag-muted");
    expect(statusClass("weird")).toBe("tag tag-neutral");
  });
  it("outcome tone", () => {
    expect(outcomeClass("completed")).toBe("tag tag-ok");
    expect(outcomeClass("failed")).toBe("tag tag-bad");
  });
  it("attack class from provenance", () => {
    expect(attackClass({ provenance: { hunter_prompt_version: "sql_injection@1.0.0" } })).toBe("sql_injection");
    expect(attackClass({ provenance: {} })).toBe("");
  });
});

// issue #84 — a run's identity should be the repo the user actually named,
// not the basename of its local clone directory (almost always just "repo").
describe("repoLabel", () => {
  it("prefers source_spec (owner/repo) over the clone path's basename", () => {
    expect(repoLabel({ repo_path: "/tmp/x/repo", source_spec: "octocat/Hello-World" })).toBe(
      "octocat/Hello-World",
    );
  });
  it("shortens a git URL source_spec down to owner/repo", () => {
    expect(
      repoLabel({ repo_path: "/tmp/x/repo", source_spec: "https://github.com/octocat/Hello-World.git" }),
    ).toBe("octocat/Hello-World");
    expect(repoLabel({ repo_path: "/tmp/x/repo", source_spec: "git@github.com:octocat/Hello-World.git" })).toBe(
      "octocat/Hello-World",
    );
  });
  it("falls back to the clone path's basename when there's no source_spec (CLI-triggered runs)", () => {
    expect(repoLabel({ repo_path: "/tmp/x/vulnerable_react_app", source_spec: "" })).toBe(
      "vulnerable_react_app",
    );
    expect(repoLabel({ repo_path: "/tmp/x/vulnerable_react_app" })).toBe("vulnerable_react_app");
  });
});

// issue #103 — the API sends naive UTC ("2026-09-26T20:09:29.7", no zone).
// Read as browser-local time, a run that finished a moment ago showed
// "started in 4h · finished in 4h" at UTC-4. Pin a non-UTC zone so these
// tests fail against that bug even on a UTC machine.
describe("relTime", () => {
  beforeAll(() => { vi.stubEnv("TZ", "America/New_York"); });
  afterAll(() => { vi.unstubAllEnvs(); });

  const NOW = Date.UTC(2026, 8, 26, 20, 10, 0); // 16:10 EDT
  const ago = (ms: number) => new Date(NOW - ms).toISOString().replace("Z", "");

  it("reads zone-less API timestamps as UTC, not local time", () => {
    // a 1s run that finished 5 minutes ago
    expect(relTime("2026-09-26T20:04:58.900000", NOW)).toBe("5m ago");
    expect(relTime("2026-09-26T20:04:59.912000", NOW)).toBe("5m ago");
    expect(apiInstant("2026-09-26T20:09:29.705922")).toBe(Date.UTC(2026, 8, 26, 20, 9, 29, 705));
  });

  it("honours explicit zones", () => {
    expect(relTime("2026-09-26T20:05:00Z", NOW)).toBe("5m ago");
    expect(relTime("2026-09-26T22:05:00+02:00", NOW)).toBe("5m ago");
    expect(relTime("2026-09-26T16:05:00-04:00", NOW)).toBe("5m ago");
  });

  it("calls the present (and small clock skew either way) 'just now'", () => {
    expect(relTime(ago(0), NOW)).toBe("just now");
    expect(relTime(ago(1_000), NOW)).toBe("just now");
    expect(relTime(ago(-30_000), NOW)).toBe("just now"); // server clock 30s ahead
    expect(relTime(ago(5_000), NOW)).toBe("5s ago");
  });

  it("switches units at the boundaries without rounding up", () => {
    expect(relTime(ago(59_000), NOW)).toBe("59s ago");
    expect(relTime(ago(60_000), NOW)).toBe("1m ago");
    expect(relTime(ago(3_599_000), NOW)).toBe("59m ago");
    expect(relTime(ago(3_600_000), NOW)).toBe("1h ago");
    expect(relTime(ago(86_399_000), NOW)).toBe("23h ago");
    expect(relTime(ago(86_400_000), NOW)).toBe("1d ago");
  });

  it("still shows genuinely future times", () => {
    expect(relTime(ago(-2 * 3_600_000), NOW)).toBe("in 2h");
  });

  it("handles missing and malformed input", () => {
    expect(relTime(null, NOW)).toBe("—");
    expect(relTime("", NOW)).toBe("—");
    expect(relTime("not a date", NOW)).toBe("—");
    expect(absTime("not a date")).toBe("");
  });

  it("absTime shows the UTC instant in local time", () => {
    expect(absTime("2026-09-26T20:05:00")).toBe(new Date(Date.UTC(2026, 8, 26, 20, 5)).toLocaleString());
  });
});
