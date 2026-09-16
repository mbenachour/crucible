import { describe, expect, it } from "vitest";
import { attackClass, outcomeClass, repoLabel, severityClass, statusClass } from "./format";

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
