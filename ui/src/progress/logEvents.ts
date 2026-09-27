// run.log -> structured progress events.
//
// The API has no structured progress/event endpoint (see the report on this
// view), so the Live progress page reads the same `run.log` the Logs tab
// shows and recognises the handful of INFO lines each pipeline stage emits
// (crucible/graph/build.py::_traced, crucible/graph/nodes/*.py). Unrecognised
// lines — the vast majority — are dropped; nothing raw ever reaches the UI.
// If a backend log message changes wording, the matching event just stops
// appearing and the view falls back to /state + /runs counts.

export type LogEvent = { t: number } & (
  | { kind: "node_enter"; node: string }
  | { kind: "node_done"; node: string; secs: number }
  | { kind: "node_failed"; node: string; secs: number; error: string }
  | { kind: "seed"; files: number; entryPoints: number }
  | { kind: "orient"; proposed: number | null }
  | { kind: "partition"; count: number; names: string[] }
  | { kind: "maps"; contributed: number; total: number; skipped: boolean }
  | { kind: "threat_model"; attackers: number; assets: number; failed: boolean }
  | { kind: "synthesis"; quality: string; surface: number }
  | { kind: "decompose"; queued: number }
  | { kind: "hunt_start"; pending: number; continuation: number; maxContinuations: number }
  | { kind: "hunt_batch"; tasks: number; workers: number; rest: number }
  | { kind: "task_start"; taskId: string; attackClass: string; area: string }
  | { kind: "task_done"; taskId: string; attackClass: string; secs: number; findings: number; shallow: boolean }
  | { kind: "hunt_done"; newFindings: number; queuedForNext: number }
  | { kind: "dedup_done"; folded: number; remaining: number }
  | { kind: "mech_done"; total: number; passed: number; failed: number }
  | { kind: "gapfill_done"; requeued: number }
  | { kind: "feedback_done"; rewrote: number }
  | { kind: "loop_rehunt"; cycle: number; cap: number; requeued: number }
  | { kind: "loop_done"; cycles: number; findings: number }
  | { kind: "verdict"; pass: "bug" | "reach"; findingId: string; upheld: boolean }
  | { kind: "validate_summary"; pass: "bug" | "reach"; candidates: number; upheld: number; refuted: number }
  | { kind: "report_done"; upheld: number; total: number }
);

// file-handler format (crucible/obs.py):
//   "%(asctime)s %(levelname)-7s %(name)s:%(lineno)d  %(message)s"
//   2026-09-26 14:02:11,512 INFO    crucible.graph:170  → recon
const LINE = /^(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d):(\d\d)[,.](\d{3})\s+([A-Z]+)\s+\S+\s+(.*)$/;

type Rule = [RegExp, (m: RegExpMatchArray) => Omit<LogEvent, "t"> | null];

const n = (s: string | undefined) => (s === undefined ? 0 : Number(s));

const RULES: Rule[] = [
  [/^→ (\w+)$/, (m) => ({ kind: "node_enter", node: m[1] })],
  [/^✓ (\w+)\s+([\d.]+)s$/, (m) => ({ kind: "node_done", node: m[1], secs: n(m[2]) })],
  [/^✗ (\w+) failed after ([\d.]+)s: (.*)$/, (m) => ({ kind: "node_failed", node: m[1], secs: n(m[2]), error: m[3] })],
  [/^R0 seed .*files=(\d+) entry_points=(\d+)/, (m) => ({ kind: "seed", files: n(m[1]), entryPoints: n(m[2]) })],
  [/^R1a orient .*?(\d+) subsystem\(s\) proposed/, (m) => ({ kind: "orient", proposed: n(m[1]) })],
  [/^R1a orient .*failed/, () => ({ kind: "orient", proposed: null })],
  [
    /^R1a partition\s+(\d+) subsystem\(s\) via \S+\s*(.*)$/,
    (m) => ({
      kind: "partition",
      count: n(m[1]),
      names: m[2].split(/,\s*/).map((x) => x.replace(/\(\d+\)$/, "").trim()).filter(Boolean),
    }),
  ],
  [/^R1b subsystem maps .*?(\d+)\/(\d+) subsystems contributed/, (m) => ({ kind: "maps", contributed: n(m[1]), total: n(m[2]), skipped: false })],
  [/^R1b subsystem maps\s+skipped/, () => ({ kind: "maps", contributed: 0, total: 0, skipped: true })],
  [/^R2 threat model .*attackers=(\d+) assets=(\d+)/, (m) => ({ kind: "threat_model", attackers: n(m[1]), assets: n(m[2]), failed: false })],
  [/^R2 threat model .*failed/, () => ({ kind: "threat_model", attackers: 0, assets: 0, failed: true })],
  [/^R1c synthesis\s+quality=(\w+)\s+subsystems=\d+\s+attack_surface=(\d+)/, (m) => ({ kind: "synthesis", quality: m[1], surface: n(m[2]) })],
  [/^R3 decompose\s+(\d+)\/\d+ chunks queued/, (m) => ({ kind: "decompose", queued: n(m[1]) })],
  [/^hunt start\s+pending=(\d+)\s+continuation=(\d+)\/(\d+)/, (m) => ({ kind: "hunt_start", pending: n(m[1]), continuation: n(m[2]), maxContinuations: n(m[3]) })],
  [/^hunt batch\s+(\d+) task\(s\)\s+workers=(\d+).*rest=(\d+)/, (m) => ({ kind: "hunt_batch", tasks: n(m[1]), workers: n(m[2]), rest: n(m[3]) })],
  [/^hunt task (\S+)\s+START\s+(\S+)\s+\S+\s+area=(\S+)/, (m) => ({ kind: "task_start", taskId: m[1], attackClass: m[2], area: m[3] })],
  [
    /^hunt task (\S+)\s+DONE\s+(\S+)\s+\S+\s+([\d.]+)s\s+findings=(\d+)\s+forks=\d+(\s+\[shallow\])?/,
    (m) => ({ kind: "task_done", taskId: m[1], attackClass: m[2], secs: n(m[3]), findings: n(m[4]), shallow: !!m[5] }),
  ],
  [/^hunt done\s+new_findings=(\d+)\s+forks=\d+\s+queued_for_next=(\d+)/, (m) => ({ kind: "hunt_done", newFindings: n(m[1]), queuedForNext: n(m[2]) })],
  [/^dedup done .*folded=(\d+).*findings_out=(\d+)/, (m) => ({ kind: "dedup_done", folded: n(m[1]), remaining: n(m[2]) })],
  [/^validate_mechanical\s+(\d+) finding\(s\): (\d+) passed, (\d+) failed/, (m) => ({ kind: "mech_done", total: n(m[1]), passed: n(m[2]), failed: n(m[3]) })],
  [/^gapfill done .*requeued=(\d+)/, (m) => ({ kind: "gapfill_done", requeued: n(m[1]) })],
  [/^feedback done\s+rewrote (\d+)\//, (m) => ({ kind: "feedback_done", rewrote: n(m[1]) })],
  [/^loop\s+cycle (\d+)\/(\d+) complete — re-hunting (\d+)/, (m) => ({ kind: "loop_rehunt", cycle: n(m[1]), cap: n(m[2]), requeued: n(m[3]) })],
  [/^loop\s+producer-consumer loop done after (\d+) cycle.*?(\d+) finding\(s\)/, (m) => ({ kind: "loop_done", cycles: n(m[1]), findings: n(m[2]) })],
  [
    /^validate_(bug|reachability)\s+(\d+) candidate\(s\): (\d+) upheld, (\d+) refuted/,
    (m) => ({ kind: "validate_summary", pass: m[1] === "bug" ? "bug" : "reach", candidates: n(m[2]), upheld: n(m[3]), refuted: n(m[4]) }),
  ],
  [/^validate_(bug|reachability)\s+0 candidate\(s\)$/, (m) => ({ kind: "validate_summary", pass: m[1] === "bug" ? "bug" : "reach", candidates: 0, upheld: 0, refuted: 0 })],
  [
    /^validate_(bug|reachability)\s+(\S+)\s+(upheld|refuted)\b/,
    (m) => ({ kind: "verdict", pass: m[1] === "bug" ? "bug" : "reach", findingId: m[2], upheld: m[3] === "upheld" }),
  ],
  [/^report\s+(\d+) upheld \/ (\d+) findings/, (m) => ({ kind: "report_done", upheld: n(m[1]), total: n(m[2]) })],
];

/** Parse one run.log line. Returns null for anything that isn't a known progress marker. */
export function parseLine(line: string): LogEvent | null {
  const m = LINE.exec(line);
  if (!m) return null;
  const [, y, mo, d, h, mi, s, ms, , msg] = m;
  // run.log timestamps carry no zone; read them as local time. Only
  // differences between them are shown, and "now" is skew-corrected against
  // live lines (see useRunLog), so the server's zone doesn't matter.
  const t = new Date(n(y), n(mo) - 1, n(d), n(h), n(mi), n(s), n(ms)).getTime();
  const text = msg.trim();
  for (const [re, make] of RULES) {
    const mm = re.exec(text);
    if (mm) {
      const ev = make(mm);
      return ev ? ({ ...ev, t } as LogEvent) : null;
    }
  }
  return null;
}

export function parseLog(text: string): LogEvent[] {
  const out: LogEvent[] = [];
  for (const line of text.split("\n")) {
    const ev = parseLine(line);
    if (ev) out.push(ev);
  }
  return out;
}

// --------------------------------------------------------------- timeline

export interface Span {
  node: string;
  start: number;
  end: number | null;
  failed: boolean;
}

export interface TaskRun {
  taskId: string;
  attackClass: string;
  area: string;
  start: number;
  end: number | null;
  secs: number | null;
  findings: number;
}

export interface Timeline {
  first: number | null;
  last: number | null;
  spans: Span[];
  /** node currently executing (entered, not yet finished), per the log */
  current: string | null;
  failure: { node: string; error: string } | null;

  seed?: { files: number; entryPoints: number };
  orient?: { proposed: number | null };
  partition?: { count: number; names: string[] };
  maps?: { contributed: number; total: number; skipped: boolean };
  threat?: { attackers: number; assets: number; failed: boolean };
  synthesis?: { quality: string; surface: number };
  planned?: number;

  /** 1-based hunt round (outer producer-consumer cycle) */
  round: number;
  roundCap: number | null;
  continuation: number;
  maxContinuations: number;
  /** current sweep: the batch the Hunt node is working through right now */
  batch: { total: number; workers: number; rest: number; start: number; done: number; findings: number } | null;
  /** this round's totals across sweeps */
  roundDone: number;
  tasks: TaskRun[];
  huntFindings: number;

  dedup?: { folded: number; remaining: number };
  mech?: { total: number; passed: number; failed: number };
  gapfill?: { requeued: number };
  feedback?: { rewrote: number };
  loopDone?: { cycles: number; findings: number };
  verdicts: { bug: { upheld: number; refuted: number }; reach: { upheld: number; refuted: number } };
  summary: { bug?: { candidates: number; upheld: number; refuted: number }; reach?: { candidates: number; upheld: number; refuted: number } };
  report?: { upheld: number; total: number };
}

export function emptyTimeline(): Timeline {
  return {
    first: null, last: null, spans: [], current: null, failure: null,
    round: 1, roundCap: null, continuation: 0, maxContinuations: 3,
    batch: null, roundDone: 0, tasks: [], huntFindings: 0,
    verdicts: { bug: { upheld: 0, refuted: 0 }, reach: { upheld: 0, refuted: 0 } },
    summary: {},
  };
}

/** Fold events (in log order) into a timeline snapshot. Pure. */
export function buildTimeline(events: LogEvent[]): Timeline {
  const tl = emptyTimeline();
  const open = new Map<string, TaskRun>();

  for (const e of events) {
    tl.first ??= e.t;
    tl.last = e.t;
    switch (e.kind) {
      case "node_enter":
        tl.spans.push({ node: e.node, start: e.t, end: null, failed: false });
        tl.current = e.node;
        break;
      case "node_done":
      case "node_failed": {
        const sp = [...tl.spans].reverse().find((s) => s.node === e.node && s.end === null);
        if (sp) {
          sp.end = e.t;
          sp.failed = e.kind === "node_failed";
        }
        if (tl.current === e.node) tl.current = null;
        if (e.kind === "node_failed") tl.failure = { node: e.node, error: e.error };
        break;
      }
      case "seed": tl.seed = { files: e.files, entryPoints: e.entryPoints }; break;
      case "orient": tl.orient = { proposed: e.proposed }; break;
      case "partition": tl.partition = { count: e.count, names: e.names }; break;
      case "maps": tl.maps = { contributed: e.contributed, total: e.total, skipped: e.skipped }; break;
      case "threat_model": tl.threat = { attackers: e.attackers, assets: e.assets, failed: e.failed }; break;
      case "synthesis": tl.synthesis = { quality: e.quality, surface: e.surface }; break;
      case "decompose": tl.planned = e.queued; break;
      case "hunt_start":
        tl.continuation = e.continuation;
        tl.maxContinuations = e.maxContinuations;
        break;
      case "hunt_batch":
        tl.batch = { total: e.tasks, workers: e.workers, rest: e.rest, start: e.t, done: 0, findings: 0 };
        break;
      case "task_start": {
        const tr: TaskRun = { taskId: e.taskId, attackClass: e.attackClass, area: e.area, start: e.t, end: null, secs: null, findings: 0 };
        open.set(e.taskId, tr);
        tl.tasks.push(tr);
        break;
      }
      case "task_done": {
        const tr = open.get(e.taskId);
        if (tr) {
          tr.end = e.t;
          tr.secs = e.secs;
          tr.findings = e.findings;
          open.delete(e.taskId);
        } else {
          tl.tasks.push({ taskId: e.taskId, attackClass: e.attackClass, area: "", start: e.t, end: e.t, secs: e.secs, findings: e.findings });
        }
        if (tl.batch) {
          tl.batch.done += 1;
          tl.batch.findings += e.findings;
        }
        tl.roundDone += 1;
        tl.huntFindings += e.findings;
        break;
      }
      case "hunt_done":
        open.clear();
        break;
      case "dedup_done": tl.dedup = { folded: e.folded, remaining: e.remaining }; break;
      case "mech_done": tl.mech = { total: e.total, passed: e.passed, failed: e.failed }; break;
      case "gapfill_done": tl.gapfill = { requeued: e.requeued }; break;
      case "feedback_done": tl.feedback = { rewrote: e.rewrote }; break;
      case "loop_rehunt":
        tl.round = e.cycle + 1;
        tl.roundCap = e.cap;
        tl.roundDone = 0;
        tl.batch = null;
        tl.dedup = tl.mech = tl.gapfill = tl.feedback = undefined;
        break;
      case "loop_done": tl.loopDone = { cycles: e.cycles, findings: e.findings }; break;
      case "verdict": tl.verdicts[e.pass][e.upheld ? "upheld" : "refuted"] += 1; break;
      case "validate_summary": tl.summary[e.pass] = { candidates: e.candidates, upheld: e.upheld, refuted: e.refuted }; break;
      case "report_done": tl.report = { upheld: e.upheld, total: e.total }; break;
    }
  }
  return tl;
}

/** Tasks currently being hunted (started, not finished). */
export function activeTasks(tl: Timeline): TaskRun[] {
  return tl.current === "hunt" ? tl.tasks.filter((t) => t.end === null) : [];
}
