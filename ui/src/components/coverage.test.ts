import { describe, expect, it } from "vitest";
import { cellState } from "./CoverageMatrix";
import type { CoverageCell } from "../api/types";

const cell = (o: Partial<CoverageCell>): CoverageCell => ({
  area: "a", attack_class: "c", passes: 0, findings: 0, productive: false, shallow: false, ...o,
});

describe("cellState", () => {
  it("productive when it has a finding", () => {
    expect(cellState(cell({ passes: 2, findings: 1, productive: true }))).toBe("productive");
  });
  it("covered when hunted with no finding", () => {
    expect(cellState(cell({ passes: 1 }))).toBe("covered");
  });
  it("none when never hunted", () => {
    expect(cellState(cell({}))).toBe("none");
  });
});
