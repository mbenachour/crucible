import { describe, expect, it } from "vitest";
import { attackClass, outcomeClass, severityClass, statusClass } from "./format";

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
