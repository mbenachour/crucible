// Combines the three things we know about a run — the run row (/runs/{id}),
// its checkpointed graph state (/runs/{id}/state), and the parsed run.log
// timeline — into the plain-language view model the Live progress page renders.
// Pure: no fetching, no React, `now` passed in.

import type { Run, RunState } from "../api/types";
import { activeTasks, type LogEvent, type Timeline } from "./logEvents";
import { apiInstant, areaName, areaPhrase, areasChecked, attackName, capitalize, enumWords, listPhrase, plural } from "./humanize";

export type AgentKey = "recon" | "hunt" | "validate" | "report";
export type Status = "waiting" | "active" | "done" | "failed" | "skipped";

export interface Step {
  label: string;
  detail?: string;
  status: Status;
  progress?: { done: number; total: number };
}

export interface AgentVM {
  key: AgentKey;
  title: string;
  role: string;
  status: Status;
  headline: string;
  /** 0..1, or null for "working, can't tell how far" */
  progress: number | null;
  steps: Step[];
  elapsedSecs: number | null;
  etaSecs: number | null;
  badge?: string;
}

export interface ProgressVM {
  phase: "launching" | "running" | "finished" | "failed" | "stopped";
  headline: string;
  subline: string;
  overall: number;
  agents: AgentVM[];
  elapsedSecs: number | null;
  etaSecs: number | null;
}

// Stage node -> the agent that owns it. Unknown nodes (a downstream
// distribution's extra stages, crucible/graph/build.py::NodeSpec) are
// attributed to whichever agent ran before them.
export const NODE_AGENT: Record<string, AgentKey> = {
  recon: "recon",
  hunt: "hunt",
  dedup: "hunt",
  validate_mechanical: "hunt",
  gapfill: "hunt",
  feedback: "hunt",
  loop_control: "hunt",
  validate_bug: "validate",
  validate_reachability: "validate",
  report: "report",
};
const STAGE_ORDER = Object.keys(NODE_AGENT);
const AGENT_ORDER: AgentKey[] = ["recon", "hunt", "validate", "report"];
const WEIGHT: Record<AgentKey, number> = { recon: 0.15, hunt: 0.6, validate: 0.2, report: 0.05 };
/** crucible/graph/hooks.py::max_cycles default — only used until the log states the real cap. */
const DEFAULT_ROUNDS = 2;

export const AGENT_META: Record<AgentKey, { title: string; role: string }> = {
  recon: { title: "Recon", role: "Maps the codebase and plans where to look" },
  hunt: { title: "Hunt", role: "Searches each area for vulnerabilities" },
  validate: { title: "Validate", role: "Double-checks every lead before it counts" },
  report: { title: "Report", role: "Writes up what was confirmed" },
};

export interface Inputs {
  run: Run;
  state: RunState | null | undefined;
  tl: Timeline;
  hasLog: boolean;
  /** "now" on the run.log clock (ms) */
  now: number;
}

// ------------------------------------------------------------ node status

function nodeStatus(node: string, { run, state, tl, hasLog }: Inputs): Status {
  if (tl.failure?.node === node) return "failed";
  if (hasLog && tl.spans.length) {
    if (tl.current === node) return "active";
    return tl.spans.some((s) => s.node === node && s.end !== null) ? "done" : "waiting";
  }
  // No log to read: infer from the checkpoint's next node.
  if (run.outcome === "completed") return "done";
  const next = state?.next_node;
  if (!next) return "waiting";
  const i = STAGE_ORDER.indexOf(node);
  const j = STAGE_ORDER.indexOf(next);
  if (i < 0 || j < 0) return "waiting";
  return i < j ? "done" : i === j && !run.finished_at ? "active" : "waiting";
}

function agentStatus(key: AgentKey, inp: Inputs): Status {
  const nodes = STAGE_ORDER.filter((n) => NODE_AGENT[n] === key);
  const st = nodes.map((n) => nodeStatus(n, inp));
  if (st.includes("failed")) return "failed";
  if (st.includes("active")) return "active";
  // Hunt loops back on itself; it's only done once something after it started.
  const laterStarted = AGENT_ORDER.slice(AGENT_ORDER.indexOf(key) + 1).some((k) =>
    STAGE_ORDER.some((n) => NODE_AGENT[n] === k && nodeStatus(n, inp) !== "waiting"),
  );
  if (laterStarted) return "done";
  if (key === "hunt" && inp.tl.loopDone) return "done";
  if (st.every((s) => s === "done")) return "done";
  if (st.some((s) => s === "done")) return inp.run.finished_at ? "done" : "active";
  return "waiting";
}

function agentElapsed(key: AgentKey, { tl, now }: Inputs): number | null {
  const spans = tl.spans.filter((s) => NODE_AGENT[s.node] === key);
  if (!spans.length) return null;
  return (Math.max(...spans.map((s) => s.end ?? now)) - spans[0].start) / 1000;
}

function stepStatus(done: boolean, isNext: boolean, agent: Status): Status {
  if (done) return "done";
  if (agent === "failed" && isNext) return "failed";
  if (agent === "active" && isNext) return "active";
  if (agent === "done" || agent === "skipped") return "skipped";
  return "waiting";
}

/** Mark the first not-done step as the in-flight one. */
function sequence(defs: { label: string; done: boolean; detail?: string; progress?: Step["progress"] }[], agent: Status): Step[] {
  let nextSeen = false;
  return defs.map((d) => {
    const isNext = !d.done && !nextSeen;
    if (isNext) nextSeen = true;
    return { label: d.label, detail: d.detail, progress: d.progress, status: stepStatus(d.done, isNext, agent) };
  });
}

// ------------------------------------------------------------ recon

function recon(inp: Inputs): AgentVM {
  const { tl } = inp;
  const status = agentStatus("recon", inp);
  const partitionNames = (tl.partition?.names ?? []).map(areaName);
  const steps = sequence(
    [
      {
        label: "Take inventory of the code",
        done: !!tl.seed,
        detail: tl.seed && `${plural(tl.seed.files, "file")}, ${plural(tl.seed.entryPoints, "entry point")}`,
      },
      {
        label: "Sketch the architecture",
        done: !!tl.partition,
        detail: tl.partition && `Split into ${plural(tl.partition.count, "component")}${partitionNames.length ? `: ${listPhrase(partitionNames, 3)}` : ""}`,
      },
      {
        label: "Study each component",
        done: !!tl.maps,
        detail: tl.maps
          ? tl.maps.skipped
            ? "Skipped — no AI model configured"
            : `${tl.maps.contributed} of ${plural(tl.maps.total, "component")} mapped in depth`
          : tl.partition
            ? `${plural(tl.partition.count, "component")} being read in parallel`
            : undefined,
      },
      {
        label: "Think like an attacker",
        done: !!tl.threat || (!!tl.synthesis && !tl.threat),
        detail: tl.threat
          ? tl.threat.failed
            ? "Couldn't complete — continuing with the basic map"
            : `${plural(tl.threat.attackers, "kind")} of attacker, ${plural(tl.threat.assets, "asset")} worth protecting`
          : tl.synthesis && tl.maps?.skipped
            ? "Skipped — no AI model configured"
            : undefined,
      },
      {
        label: "Plan the hunt",
        done: tl.planned !== undefined,
        detail: tl.planned !== undefined ? `${plural(tl.planned, "area")} queued for hunting` : undefined,
      },
    ],
    status,
  );
  const doneN = steps.filter((s) => s.status === "done").length;
  const active = steps.find((s) => s.status === "active");

  let headline = "Waiting to start";
  if (status === "done") {
    const planned = tl.planned ?? inp.state?.pending_hunt_count;
    headline = planned ? `Mapped the code and planned ${plural(planned, "area")} to hunt` : "Mapped the codebase";
  } else if (status === "failed") headline = "Stopped with an error while mapping the code";
  else if (status === "active") {
    headline = {
      "Take inventory of the code": "Taking inventory of the code",
      "Sketch the architecture": "Sketching how the code fits together",
      "Study each component": tl.partition ? `Studying ${plural(tl.partition.count, "component")} in depth` : "Studying each component",
      "Think like an attacker": "Working out who might attack this and what they'd want",
      "Plan the hunt": "Planning where to hunt",
    }[active?.label ?? ""] ?? "Mapping the codebase";
  }

  return {
    key: "recon", ...AGENT_META.recon, status, headline, steps,
    progress: status === "done" ? 1 : inp.hasLog ? doneN / steps.length : null,
    elapsedSecs: agentElapsed("recon", inp),
    etaSecs: null,
    badge: inp.run.recon_quality && status === "done" ? qualityLabel(inp.run.recon_quality) : undefined,
  };
}

function qualityLabel(q: string): string {
  return ({ full: "thorough map", partial: "partial map", seed_only: "basic map only" } as Record<string, string>)[q] ?? enumWords(q);
}

// ------------------------------------------------------------ hunt

export function huntRemaining(tl: Timeline, state: RunState | null | undefined): number {
  if (tl.batch) return tl.batch.total - tl.batch.done + tl.batch.rest;
  return state?.pending_hunt_count ?? 0;
}

/** Seconds left in the current sweep, from observed task durations. */
export function huntEta(tl: Timeline, now: number): number | null {
  const b = tl.batch;
  if (!b || tl.current !== "hunt") return null;
  const done = tl.tasks.filter((t) => t.secs !== null);
  if (done.length < 2) return null;
  const avg = done.reduce((a, t) => a + (t.secs ?? 0), 0) / done.length;
  const left = b.total - b.done;
  if (left <= 0) return null;
  // in-flight tasks are partly done already
  const inflight = activeTasks(tl).reduce((a, t) => a + Math.min(avg * 0.9, (now - t.start) / 1000), 0);
  return Math.max(0, (left * avg - inflight) / Math.max(1, b.workers));
}

function hunt(inp: Inputs): AgentVM {
  const { tl, state, now } = inp;
  const status = agentStatus("hunt", inp);
  const remaining = huntRemaining(tl, state);
  const total = tl.roundDone + remaining;
  const rounds = tl.roundCap ?? DEFAULT_ROUNDS;
  const cur = tl.current;
  const huntingNow = cur === "hunt";
  const past = (node: string) => tl.spans.some((s) => s.node === node && s.end !== null && s.start >= roundStart(tl));

  const steps = sequence(
    [
      {
        label: "Search each area",
        done: !huntingNow && (past("hunt") || !!tl.dedup || !!tl.mech),
        detail: total ? areasChecked(tl.roundDone, total) : undefined,
        progress: total ? { done: tl.roundDone, total } : undefined,
      },
      {
        label: "Merge duplicate reports",
        done: !!tl.dedup,
        detail: tl.dedup ? (tl.dedup.folded ? `Merged ${plural(tl.dedup.folded, "duplicate")}` : "No duplicates") : undefined,
      },
      {
        label: "Try to reproduce each lead",
        done: !!tl.mech,
        detail: tl.mech ? `${tl.mech.passed} of ${plural(tl.mech.total, "lead")} reproduced` : undefined,
      },
      {
        label: "Look for blind spots",
        done: !!tl.gapfill,
        detail: tl.gapfill ? (tl.gapfill.requeued ? `${plural(tl.gapfill.requeued, "area")} queued for a second look` : "Coverage looks complete") : undefined,
      },
      {
        label: "Sharpen the approach",
        done: !!tl.feedback || !!tl.loopDone,
        detail: tl.feedback?.rewrote ? `Refined ${plural(tl.feedback.rewrote, "strategy", "strategies")} from what didn't work` : undefined,
      },
    ],
    status,
  );

  let headline = "Waiting for the map";
  const act = activeTasks(tl);
  if (status === "done") {
    const leads = inp.run.counts.total ?? tl.huntFindings;
    headline = `Finished hunting — ${plural(leads, "lead")} to double-check`;
  } else if (status === "failed") headline = "Stopped with an error while hunting";
  else if (status === "active") {
    if (huntingNow && act.length) {
      const names = act.map((t) => attackName(t.attackClass));
      const areas = [...new Set(act.map((t) => t.area))];
      const where = areas.length === 1 ? ` ${areaPhrase(areas[0])}` : "";
      headline = `Hunting ${listPhrase(names)}${where}`;
    } else if (huntingNow) headline = "Picking the next areas to search";
    else if (cur === "dedup") headline = "Merging duplicate reports";
    else if (cur === "validate_mechanical") headline = "Trying to reproduce each lead with a proof-of-concept";
    else if (cur === "gapfill") headline = "Looking for areas that deserve a second look";
    else if (cur === "feedback") headline = "Sharpening the approach from what didn't work";
    else if (cur === "loop_control") headline = "Deciding whether another round is worth it";
    else headline = "Hunting for vulnerabilities";
    if (huntingNow && total) headline += ` — ${tl.roundDone} of ${plural(total, "area")} done`;
  }

  // progress within the whole Hunt agent: rounds x (search 80% + wrap-up 20%)
  const searchFrac = total ? tl.roundDone / total : 0;
  const wrapFrac = steps.slice(1).filter((s) => s.status === "done").length / 4;
  const roundFrac = steps[0].status === "done" ? 0.8 + 0.2 * wrapFrac : 0.8 * searchFrac;
  const progress = status === "done" ? 1 : status === "waiting" ? 0 : Math.min(0.99, (tl.round - 1 + roundFrac) / Math.max(rounds, tl.round));

  return {
    key: "hunt", ...AGENT_META.hunt, status, headline, steps, progress,
    elapsedSecs: agentElapsed("hunt", inp),
    etaSecs: huntEta(tl, now),
    badge: status !== "waiting" ? `round ${tl.round}${tl.roundCap ? ` of ${tl.roundCap}` : ""}` : undefined,
  };
}

function roundStart(tl: Timeline): number {
  // spans in the current round = everything after the last loop_control that led to a rehunt
  if (tl.round <= 1) return 0;
  const lc = tl.spans.filter((s) => s.node === "loop_control" && s.end !== null);
  return lc.length ? (lc[tl.round - 2]?.end ?? lc[lc.length - 1].end ?? 0) : 0;
}

// ------------------------------------------------------------ validate

function validate(inp: Inputs): AgentVM {
  const c = inp.run.counts;
  const status = agentStatus("validate", inp);
  const reachDone = (c.reach_upheld ?? 0) + (c.reach_refuted ?? 0);
  const bugDone = (c.bug_upheld ?? 0) + (c.bug_refuted ?? 0) + reachDone;
  const bugTotal = bugDone + (c.mechanical_passed ?? 0);
  const reachTotal = reachDone + (c.bug_upheld ?? 0);
  const bugNode = nodeStatus("validate_bug", inp);
  const reachNode = nodeStatus("validate_reachability", inp);

  const steps: Step[] = [
    {
      label: "Is it a real bug?",
      status: bugNode === "done" && status !== "failed" ? "done" : bugNode,
      detail: bugTotal ? `${bugDone} of ${plural(bugTotal, "lead")} reviewed · ${(c.bug_upheld ?? 0) + reachDone} confirmed` : bugNode === "done" ? "Nothing to review" : undefined,
      progress: bugTotal ? { done: bugDone, total: bugTotal } : undefined,
    },
    {
      label: "Can an attacker actually reach it?",
      status: reachNode,
      detail: reachTotal ? `${reachDone} of ${plural(reachTotal, "bug")} traced · ${c.reach_upheld ?? 0} reachable` : reachNode === "done" ? "Nothing to trace" : undefined,
      progress: reachTotal ? { done: reachDone, total: reachTotal } : undefined,
    },
  ];
  if (status === "done" || status === "skipped") steps.forEach((s) => s.status === "waiting" && (s.status = "skipped"));

  let headline = "Waiting for leads";
  if (status === "done") headline = `${plural(c.reach_upheld ?? 0, "issue")} confirmed real and reachable`;
  else if (status === "failed") headline = "Stopped with an error while double-checking";
  else if (status === "active") {
    headline = reachNode === "active"
      ? `Checking whether an attacker can reach each bug${reachTotal ? ` — ${reachDone} of ${reachTotal} traced` : ""}`
      : `Double-checking whether each lead is a real bug${bugTotal ? ` — ${bugDone} of ${bugTotal} reviewed` : ""}`;
  }

  const frac = (s: Step) => (s.status === "done" ? 1 : s.progress ? s.progress.done / Math.max(1, s.progress.total) : 0);
  return {
    key: "validate", ...AGENT_META.validate, status, headline, steps,
    progress: status === "done" ? 1 : status === "waiting" ? 0 : (frac(steps[0]) + frac(steps[1])) / 2,
    elapsedSecs: agentElapsed("validate", inp),
    etaSecs: null,
  };
}

// ------------------------------------------------------------ report

function report(inp: Inputs): AgentVM {
  const status = agentStatus("report", inp);
  const r = inp.tl.report;
  const done = status === "done" || inp.run.report_available;
  return {
    key: "report", ...AGENT_META.report,
    status: done ? "done" : status,
    headline: done
      ? r ? `Report ready — ${plural(r.upheld, "confirmed issue")}` : "Report ready"
      : status === "active" ? "Writing up what was confirmed" : status === "failed" ? "Stopped with an error while writing the report" : "Waiting for confirmed results",
    steps: [{ label: "Write the report", status: done ? "done" : status, detail: r ? `${plural(r.upheld, "confirmed issue")} out of ${plural(r.total, "lead")}` : undefined }],
    progress: done ? 1 : status === "active" ? null : 0,
    elapsedSecs: agentElapsed("report", inp),
    etaSecs: null,
  };
}

// ------------------------------------------------------------ whole run

export function buildProgress(inp: Inputs): ProgressVM {
  const { run, tl, now } = inp;
  const agents = [recon(inp), hunt(inp), validate(inp), report(inp)];
  const launching = run.clone_status === "pending" || run.clone_status === "cloning";

  // A finished run that never reached an agent: it didn't run, it isn't waiting.
  if (run.finished_at) {
    for (const a of agents) {
      if (a.status === "waiting") {
        a.status = "skipped";
        a.headline = "Not run";
        a.steps.forEach((s) => s.status === "waiting" && (s.status = "skipped"));
      } else if (a.status === "active") {
        // interrupted mid-work: an error only if the run actually failed
        a.status = run.outcome === "completed" ? "done" : run.outcome === "failed" ? "failed" : "skipped";
        if (a.status === "skipped") a.headline = `Stopped partway — ${a.headline.charAt(0).toLowerCase()}${a.headline.slice(1)}`;
      }
    }
  }

  const overall = agents.reduce((acc, a) => {
    const f = a.status === "done" ? 1 : a.status === "active" || a.status === "failed" ? (a.progress ?? 0.5) : 0;
    return acc + WEIGHT[a.key] * f;
  }, 0);

  const c = run.counts;
  const confirmed = c.reach_upheld ?? 0;
  const active = agents.find((a) => a.status === "active");
  let phase: ProgressVM["phase"] = "running";
  let headline = active?.headline ?? "Getting started";
  let subline = active ? `${active.title} agent · ${active.role.toLowerCase()}` : "";

  if (run.clone_status === "clone_failed") {
    phase = "failed";
    headline = "Couldn't download the repository";
    subline = run.clone_error.split("\n")[0] || "";
  } else if (launching) {
    phase = "launching";
    headline = run.clone_status === "pending" ? "Queued — waiting for a free slot" : "Downloading the repository";
    subline = run.source_spec;
  } else if (run.finished_at) {
    if (run.outcome === "completed") {
      phase = "finished";
      headline = confirmed
        ? `Done — ${plural(confirmed, "confirmed vulnerability", "confirmed vulnerabilities")}`
        : "Done — no confirmed vulnerabilities";
      const real = (c.bug_upheld ?? 0) + confirmed + (c.reach_refuted ?? 0);
      subline = `${plural(c.total ?? 0, "lead")} investigated · ${plural(real, "real bug")} · ${confirmed} reachable by an attacker`;
    } else if (run.outcome === "failed") {
      phase = "failed";
      const failedAgent = agents.find((a) => a.status === "failed");
      headline = failedAgent ? `Stopped — the ${failedAgent.title} agent hit an error` : "The run stopped with an error";
      subline = (tl.failure?.error || run.clone_error.split("\n")[0] || "").slice(0, 160);
    } else {
      phase = "stopped";
      const last = [...agents].reverse().find((a) => a.status === "done");
      headline = run.outcome === "cancelled" ? "Cancelled" : `Stopped early${last ? ` after ${last.title}` : ""}`;
      subline = run.outcome === "cancelled" ? "Someone stopped this run before it finished." : "The run was configured to stop at this point.";
    }
  }

  const created = apiInstant(run.created_at);
  const elapsedSecs = tl.first !== null
    ? ((run.finished_at ? (tl.last ?? now) : now) - tl.first) / 1000
    : created !== null
      ? ((apiInstant(run.finished_at) ?? Date.now()) - created) / 1000
      : null;

  return {
    phase,
    headline: capitalize(headline),
    subline,
    overall: phase === "finished" ? 1 : launching ? 0 : Math.min(0.99, overall),
    agents,
    elapsedSecs,
    etaSecs: active?.etaSecs ?? null,
  };
}

// ------------------------------------------------------------ activity feed

export type Tone = "info" | "good" | "warn" | "bad" | "find";
export interface FeedItem {
  t: number;
  agent: AgentKey;
  text: string;
  tone: Tone;
  /** shown in "Highlights" mode; the rest only in "Everything" */
  highlight: boolean;
}

/** One plain-language sentence per meaningful event; null for events that aren't worth a line. */
export function feedItem(e: LogEvent): FeedItem | null {
  const f = (agent: AgentKey, text: string, tone: Tone = "info", highlight = true): FeedItem => ({ t: e.t, agent, text, tone, highlight });
  switch (e.kind) {
    case "node_enter":
      return {
        recon: f("recon", "Started mapping the codebase"),
        validate_bug: f("validate", "Started double-checking each lead"),
        validate_reachability: f("validate", "Started tracing whether attackers can reach each bug", "info", false),
        report: f("report", "Started writing the report"),
      }[e.node] ?? null;
    case "node_failed":
      return f(NODE_AGENT[e.node] ?? "hunt", `Hit an error: ${e.error.slice(0, 140)}`, "bad");
    case "seed":
      return f("recon", `Found ${plural(e.files, "file")} and ${plural(e.entryPoints, "entry point")}`, "info", false);
    case "partition":
      return f("recon", `Split the code into ${plural(e.count, "component")}${e.names.length ? `: ${listPhrase(e.names.map(areaName), 3)}` : ""}`);
    case "maps":
      return e.skipped ? null : f("recon", `Studied ${e.contributed} of ${plural(e.total, "component")} in depth`, "info", false);
    case "threat_model":
      return e.failed
        ? f("recon", "Couldn't build a threat model — carrying on with the basic map", "warn")
        : f("recon", `Built a threat model: ${plural(e.attackers, "kind")} of attacker, ${plural(e.assets, "asset")} worth protecting`);
    case "decompose":
      return f("recon", `Planned ${plural(e.queued, "area")} to hunt`, "good");
    case "hunt_batch":
      return f("hunt", `Started a hunting sweep over ${plural(e.tasks, "area")}`);
    case "task_start":
      return f("hunt", `Looking for ${attackName(e.attackClass)} ${areaPhrase(e.area)}`, "info", false);
    case "task_done":
      return e.findings
        ? f("hunt", `Spotted ${plural(e.findings, "possible issue")} while looking for ${attackName(e.attackClass)}`, "find")
        : f("hunt", `Nothing found for ${attackName(e.attackClass)}`, "info", false);
    case "hunt_done":
      return f("hunt", `Sweep finished with ${plural(e.newFindings, "new lead")}${e.queuedForNext ? `; ${plural(e.queuedForNext, "area")} left for the next sweep` : ""}`);
    case "dedup_done":
      return e.folded ? f("hunt", `Merged ${plural(e.folded, "duplicate report")}`, "info", false) : null;
    case "mech_done":
      return e.total ? f("hunt", `Reproduced ${e.passed} of ${plural(e.total, "lead")} with a proof-of-concept`, e.passed ? "good" : "info") : null;
    case "gapfill_done":
      return e.requeued ? f("hunt", `Queued ${plural(e.requeued, "area")} for a second look`, "info", false) : null;
    case "feedback_done":
      return e.rewrote ? f("hunt", `Refined ${plural(e.rewrote, "hunting strategy", "hunting strategies")} based on what didn't work`, "info", false) : null;
    case "loop_rehunt":
      return f("hunt", `Round ${e.cycle} of ${e.cap} done — going back for ${plural(e.requeued, "area")}`);
    case "loop_done":
      return f("hunt", `Hunting finished after ${plural(e.cycles, "round")} with ${plural(e.findings, "lead")}`, "good");
    case "verdict":
      return e.pass === "bug"
        ? f("validate", e.upheld ? "Confirmed a lead is a real bug" : "Ruled out a lead as a false alarm", e.upheld ? "find" : "info", false)
        : f("validate", e.upheld ? "Confirmed an attacker can reach a bug" : "Found a bug is not reachable by an attacker", e.upheld ? "find" : "info", e.upheld);
    case "validate_summary":
      if (!e.candidates) return null;
      return e.pass === "bug"
        ? f("validate", `Bug review done: ${e.upheld} confirmed, ${e.refuted} ruled out`, "good")
        : f("validate", `Reachability review done: ${plural(e.upheld, "bug")} an attacker can reach`, "good");
    case "report_done":
      return f("report", `Report ready with ${plural(e.upheld, "confirmed issue")}`, "good");
    default:
      return null;
  }
}
