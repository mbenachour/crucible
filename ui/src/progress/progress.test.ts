import { describe, expect, it } from "vitest";
import type { Run, RunState } from "../api/types";
import { buildTimeline, parseLine, parseLog } from "./logEvents";
import { buildProgress, feedItem, huntEta, type ProgressVM } from "./model";
import { PIPELINE_LOG } from "./pipelineLog.fixture";

// Lines as the backend actually formats them (crucible/graph/nodes/hunt.py,
// validate_bug.py, loop_control.py) — the fixture's registry-less run never
// reaches these paths.
const L = (ms: number, logger: string, msg: string) =>
  `2026-09-26 15:40:${String(Math.floor(ms / 1000)).padStart(2, "0")},${String(ms % 1000).padStart(3, "0")} INFO    ${logger}:1  ${msg}`;
const HUNT = [
  L(0, "crucible.graph", "→ hunt"),
  L(10, "crucible.hunt", "hunt start  pending=24  continuation=1/3"),
  L(20, "crucible.hunt", "hunt batch  4 task(s)  workers=2  (max_tasks_per_run=4, rest=20)"),
  L(1000, "crucible.hunt", "hunt task t001  START  sql_injection          taint      area=api/payments  scope=q() concatenates id"),
  L(1001, "crucible.hunt", "hunt task t002  START  ssrf                   risk       area=api/payments  scope=(none)"),
  L(41000, "crucible.hunt", "hunt task t001  DONE   sql_injection          taint  40.0s  findings=2 forks=0"),
  L(42000, "crucible.hunt", "hunt task t003  START  path_traversal         surface    area=web  scope=(none)"),
  L(51000, "crucible.hunt", "hunt task t002  DONE   ssrf                   risk  50.0s  findings=0 forks=1  [shallow]"),
  L(52000, "crucible.hunt", "hunt task t004  START  xxe                    catch_all  area=web  scope=(none)"),
];

function run(over: Partial<Run> = {}): Run {
  return {
    run_id: "r1", repo_path: "/x", repo_commit: "", primary_language: "python",
    created_at: null, finished_at: null, status: "running", outcome: "",
    recon_quality: "", workspace_path: "", report_available: false, counts: {},
    source_spec: "", clone_status: "cloned", clone_error: "", model_override: {},
    ...over,
  };
}

describe("parseLine", () => {
  it("ignores lines that aren't progress markers", () => {
    expect(parseLine("")).toBeNull();
    expect(parseLine("Traceback (most recent call last):")).toBeNull();
    expect(parseLine(L(0, "crucible.agents", "tool call bash ls -la"))).toBeNull();
  });

  it("reads node transitions, including failures", () => {
    expect(parseLine(L(0, "crucible.graph", "✓ hunt  12.5s"))).toMatchObject({ kind: "node_done", node: "hunt", secs: 12.5 });
    expect(parseLine(L(0, "crucible.graph", "✗ hunt failed after 3.0s: RuntimeError: docker down")))
      .toMatchObject({ kind: "node_failed", node: "hunt", error: "RuntimeError: docker down" });
  });

  it("reads per-task hunt lines", () => {
    expect(parseLine(HUNT[3])).toMatchObject({ kind: "task_start", taskId: "t001", attackClass: "sql_injection", area: "api/payments" });
    expect(parseLine(HUNT[7])).toMatchObject({ kind: "task_done", taskId: "t002", secs: 50, findings: 0, shallow: true });
  });

  it("reads validator verdicts and the loop decision", () => {
    expect(parseLine(L(0, "crucible.validate_bug", "validate_bug  f-123  upheld   looks real")))
      .toMatchObject({ kind: "verdict", pass: "bug", upheld: true });
    expect(parseLine(L(0, "crucible.validate_bug", "validate_bug  3 candidate(s): 2 upheld, 1 refuted")))
      .toMatchObject({ kind: "validate_summary", pass: "bug", candidates: 3, upheld: 2 });
    expect(parseLine(L(0, "crucible.loop", "loop  cycle 1/2 complete — re-hunting 7 re-queued cell(s); findings so far=0")))
      .toMatchObject({ kind: "loop_rehunt", cycle: 1, cap: 2, requeued: 7 });
  });
});

describe("real pipeline log", () => {
  const events = parseLog(PIPELINE_LOG);
  const tl = buildTimeline(events);

  it("recognises every stage and the recon sub-steps", () => {
    const nodes = new Set(tl.spans.map((s) => s.node));
    expect([...nodes]).toEqual(expect.arrayContaining([
      "recon", "hunt", "dedup", "validate_mechanical", "gapfill", "feedback", "loop_control",
      "validate_bug", "validate_reachability", "report",
    ]));
    expect(tl.spans.every((s) => s.end !== null)).toBe(true);
    expect(tl.current).toBeNull();
    expect(tl.seed).toEqual({ files: 1, entryPoints: 2 });
    expect(tl.partition).toEqual({ count: 1, names: ["api"] });
    expect(tl.maps?.skipped).toBe(true);
    expect(tl.planned).toBe(14);
    expect(tl.round).toBe(2);
    expect(tl.roundCap).toBe(2);
    expect(tl.loopDone).toEqual({ cycles: 2, findings: 0 });
    expect(tl.report).toEqual({ upheld: 0, total: 0 });
  });

  it("renders as a finished run with every agent done", () => {
    const vm = buildProgress({
      run: run({ finished_at: "2026-09-26T15:35:06Z", outcome: "completed", report_available: true, counts: { total: 0 } }),
      state: null, tl, hasLog: true, now: tl.last!,
    });
    expect(vm.phase).toBe("finished");
    expect(vm.overall).toBe(1);
    expect(vm.agents.map((a) => a.status)).toEqual(["done", "done", "done", "done"]);
    expect(vm.headline).toBe("Done — no confirmed vulnerabilities");
    expect(vm.agents[0].headline).toBe("Mapped the code and planned 14 areas to hunt");
  });

  it("never leaks stage names or ids into the feed", () => {
    const text = events.map(feedItem).filter(Boolean).map((f) => f!.text).join("\n");
    expect(text).not.toMatch(/validate_|loop_control|gapfill|dedup|R\d[a-c]?\b|t\d{3}/);
    expect(text).toContain("Planned 14 areas to hunt");
  });
});

describe("mid-hunt", () => {
  const recon = parseLog(PIPELINE_LOG).filter((e) => e.t <= parseLog(PIPELINE_LOG).find((x) => x.kind === "node_done" && x.node === "recon")!.t);
  const tl = buildTimeline([...recon, ...parseLog(HUNT.join("\n"))]);
  const now = tl.last! + 5000;
  const state = { pending_hunt_count: 24, next_node: "hunt" } as RunState;
  const vm = buildProgress({ run: run({ counts: { total: 2, raw: 2 } }), state, tl, hasLog: true, now });

  it("describes what is being hunted in plain words with a count", () => {
    const hunt = vm.agents[1];
    expect(hunt.status).toBe("active");
    expect(hunt.headline).toBe("Hunting path traversal and XML external entities in web — 2 of 24 areas done");
    expect(vm.headline).toBe(hunt.headline);
    expect(vm.agents[0].status).toBe("done");
    expect(vm.agents[2].status).toBe("waiting");
  });

  it("estimates time left from observed task durations", () => {
    // avg 45s, 2 left, 2 workers, both in flight for a while already
    const eta = huntEta(tl, now)!;
    expect(eta).toBeGreaterThan(0);
    expect(eta).toBeLessThan(45);
    expect(vm.overall).toBeGreaterThan(0.15);
    expect(vm.overall).toBeLessThan(0.5);
  });
});

describe("without a log", () => {
  it("falls back to the checkpoint's next node", () => {
    const vm = buildProgress({
      run: run(), state: { next_node: "validate_bug", pending_hunt_count: 0 } as RunState,
      tl: buildTimeline([]), hasLog: false, now: Date.now(),
    });
    expect(vm.agents.map((a) => a.status)).toEqual(["done", "done", "active", "waiting"]);
  });

  it("shows the download step while cloning", () => {
    const vm = buildProgress({ run: run({ clone_status: "cloning", source_spec: "octo/app" }), state: null, tl: buildTimeline([]), hasLog: false, now: 0 });
    expect(vm.phase).toBe("launching");
    expect(vm.headline).toBe("Downloading the repository");
  });

  it("marks agents a stopped run never reached as not run", () => {
    const vm = buildProgress({
      run: run({ finished_at: "2026-09-26T15:00:00Z", outcome: "stopped_after_stage" }),
      state: { next_node: "hunt", pending_hunt_count: 5 } as RunState, tl: buildTimeline([]), hasLog: false, now: 0,
    });
    expect(vm.phase).toBe("stopped");
    expect(vm.agents.map((a) => a.status)).toEqual(["done", "skipped", "skipped", "skipped"]);
    expect(vm.headline).toBe("Stopped early after Recon");
  });

  it("doesn't call a cancelled agent an error", () => {
    const tl = buildTimeline(parseLog(HUNT.join("\n")));
    const vm = buildProgress({
      run: run({ finished_at: "2026-09-26T15:00:00Z", outcome: "cancelled" }), state: null, tl, hasLog: true, now: tl.last!,
    });
    expect(vm.phase).toBe("stopped");
    expect(vm.agents[1].status).toBe("skipped");
    expect(vm.agents[1].headline).toMatch(/^Stopped partway — hunting /);
  });
});

describe("apiInstant", () => {
  it("reads the API's zone-less timestamps as UTC", async () => {
    const { apiInstant } = await import("./humanize");
    expect(apiInstant("2026-09-26T20:09:29.705922")).toBe(Date.UTC(2026, 8, 26, 20, 9, 29, 705));
    expect(apiInstant("2026-09-26T20:09:29+02:00")).toBe(Date.UTC(2026, 8, 26, 18, 9, 29));
    expect(apiInstant(null)).toBeNull();
  });
});

// issue #103 — nothing on this page may show config values (worker counts,
// caps), stage/node ids, task ids, or raw backend enums.
const LEAK = /\bat a time\b|\bworkers?\b|\bparallel(ism)?\b|max_tasks|\bnode\b|validate_|loop_control|gapfill|dedup|\bt\d{3}\b|\b\w+_\w+\b/i;

function vmStrings(vm: ProgressVM): string[] {
  return [
    vm.headline, vm.subline,
    ...vm.agents.flatMap((a) => [a.title, a.role, a.headline, a.badge ?? "", ...a.steps.flatMap((s) => [s.label, s.detail ?? ""])]),
  ].filter(Boolean);
}

describe("copy leaks", () => {
  const recon = parseLog(PIPELINE_LOG).filter((e) => e.t <= parseLog(PIPELINE_LOG).find((x) => x.kind === "node_done" && x.node === "recon")!.t);
  const tl = buildTimeline([...recon, ...parseLog(HUNT.join("\n"))]);
  const state = { pending_hunt_count: 24, next_node: "hunt" } as RunState;

  it("says how far the sweep is without the worker count", () => {
    const vm = buildProgress({ run: run({ counts: { total: 2, raw: 2 } }), state, tl, hasLog: true, now: tl.last! + 5000 });
    expect(vm.agents[1].steps[0].detail).toBe("2 of 24 areas checked");
    expect(feedItem(parseLine(HUNT[2])!)!.text).toBe("Started a hunting sweep over 4 areas");
  });

  it("pluralizes the areas left for the next sweep", () => {
    const e = parseLine(L(0, "crucible.hunt", "hunt done  new_findings=0  forks=0  queued_for_next=1"))!;
    expect(feedItem(e)!.text).toBe("Sweep finished with 0 new leads; 1 area left for the next sweep");
  });

  it("names a repo-root component in words, not as a path", () => {
    // as logged by a registry-less run of a single-package repo
    const e = parseLine(L(0, "crucible.recon", "R1a partition  1 subsystem(s) via fallback  .(16)"))!;
    expect(e).toMatchObject({ kind: "partition", names: ["."] });
    expect(feedItem(e)!.text).toBe("Split the code into 1 component: whole codebase");
    const tl = buildTimeline([e]);
    const vm = buildProgress({ run: run(), state: null, tl, hasLog: true, now: tl.last! });
    expect(vm.agents[0].steps[1].detail).toBe("Split into 1 component: whole codebase");
  });

  it("never shows a raw enum for an unknown recon quality", () => {
    const done = buildTimeline(parseLog(PIPELINE_LOG));
    const vm = buildProgress({
      run: run({ finished_at: "2026-09-26T15:35:06Z", outcome: "completed", recon_quality: "brand_new_level", counts: {} }),
      state: null, tl: done, hasLog: true, now: done.last!,
    });
    expect(vm.agents[0].badge).toBe("brand new level");
  });

  it("keeps every string in every state free of internals", () => {
    const full = buildTimeline(parseLog(PIPELINE_LOG));
    const vms = [
      buildProgress({ run: run({ counts: { total: 2, raw: 2 } }), state, tl, hasLog: true, now: tl.last! + 5000 }),
      buildProgress({ run: run({ finished_at: "2026-09-26T15:35:06Z", outcome: "completed", report_available: true, recon_quality: "seed_only", counts: { total: 0 } }), state: null, tl: full, hasLog: true, now: full.last! }),
      buildProgress({ run: run({ finished_at: "2026-09-26T15:00:00Z", outcome: "cancelled" }), state: null, tl: buildTimeline(parseLog(HUNT.join("\n"))), hasLog: true, now: tl.last! }),
      buildProgress({ run: run({ finished_at: "2026-09-26T15:00:00Z", outcome: "stopped_after_stage" }), state: { next_node: "hunt", pending_hunt_count: 5 } as RunState, tl: buildTimeline([]), hasLog: false, now: 0 }),
    ];
    const feed = [...parseLog(PIPELINE_LOG), ...parseLog(HUNT.join("\n"))].map(feedItem).filter(Boolean).map((f) => f!.text);
    for (const s of [...vms.flatMap(vmStrings), ...feed]) expect(s).not.toMatch(LEAK);
  });
});
