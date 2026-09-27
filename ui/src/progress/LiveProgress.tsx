import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useRun, useRunState } from "../api/hooks";
import type { Coverage, Finding, Run } from "../api/types";
import { Sev } from "../components/bits";
import { Q } from "../components/states";
import { repoLabel } from "../lib/format";
import { apiInstant, areaName, attackName, capitalize, duration, etaPhrase, plural } from "./humanize";
import { useLiveCoverage, useLiveFindings, useNow, useRunLog, type LogStatus } from "./hooks";
import { activeTasks, buildTimeline, type Timeline } from "./logEvents";
import {
  AGENT_META,
  buildProgress,
  feedItem,
  NODE_AGENT,
  type AgentKey,
  type AgentVM,
  type FeedItem,
  type ProgressVM,
  type Status,
} from "./model";
import "./progress.css";

/**
 * Live progress (plain-language run view). A separate, additive view over the
 * same API the rest of the dashboard uses — see ./model.ts for how the pieces
 * are combined and ./logEvents.ts for what's read from run.log.
 */
export function LiveProgress() {
  const { runId = "" } = useParams();
  const q = useRun(runId);
  return <Q q={q}>{(run) => <LiveProgressBody run={run} />}</Q>;
}

function LiveProgressBody({ run }: { run: Run }) {
  const finished = !!run.finished_at || run.clone_status === "clone_failed";
  const log = useRunLog(run.run_id, finished);
  const state = useRunState(run.run_id, !finished);
  const findings = useLiveFindings(run.run_id, !finished);
  const coverage = useLiveCoverage(run.run_id, !finished);
  const wall = useNow(!finished);

  const tl = useMemo(() => buildTimeline(log.events), [log.events]);
  // run.log timestamps are zone-less local time on the server. Line them up
  // with the browser clock via run.created_at (a real instant), snapping to
  // the nearest 15 min — every UTC offset is a multiple of that.
  const skew = useMemo(() => {
    const created = apiInstant(run.created_at);
    return tl.first === null || created === null ? 0 : Math.round((tl.first - created) / 900_000) * 900_000;
  }, [tl.first, run.created_at]);
  const now = finished ? (tl.last ?? wall + skew) : Math.max(wall + skew, tl.last ?? 0);

  const vm = useMemo(
    () => buildProgress({ run, state: state.data, tl, hasLog: log.events.length > 0, now }),
    [run, state.data, tl, log.events.length, now],
  );

  useTabTitle(vm, repoLabel(run));

  return (
    <div className="lp">
      <Hero vm={vm} run={run} logStatus={log.status} />
      <Pipeline agents={vm.agents} />
      <div className="lp-cols">
        <div className="lp-col-main">
          <NowHunting tl={tl} now={now} />
          <HuntMap tl={tl} coverage={coverage.data} />
          <Leads run={run} findings={findings.data?.items ?? []} />
        </div>
        <div className="lp-col-side">
          <Feed events={log.events} first={tl.first} status={log.status} />
        </div>
      </div>
      <RunTimeline tl={tl} now={now} />
      <div className="dim lp-footer">
        Want the technical detail? <Link to={`/runs/${run.run_id}/logs`}>raw logs</Link> ·{" "}
        <Link to={`/runs/${run.run_id}/state`}>execution state</Link> ·{" "}
        <Link to={`/runs/${run.run_id}/coverage`}>coverage matrix</Link>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ hero

const PHASE_TONE: Record<ProgressVM["phase"], string> = {
  launching: "active", running: "active", finished: "done", failed: "failed", stopped: "skipped",
};

function Hero({ vm, run, logStatus }: { vm: ProgressVM; run: Run; logStatus: LogStatus }) {
  const pct = Math.round(vm.overall * 100);
  const eta = etaPhrase(vm.etaSecs);
  const live = vm.phase === "running" || vm.phase === "launching";
  return (
    <section className={`lp-hero lp-tone-${PHASE_TONE[vm.phase]}`}>
      <div className="lp-hero-top">
        <StatusOrb status={PHASE_TONE[vm.phase] as Status} big />
        <div className="lp-hero-text">
          <div className="lp-headline" aria-live="polite">{vm.headline}</div>
          {vm.subline && <div className="lp-subline">{vm.subline}</div>}
        </div>
        {live && <NotifyButton vm={vm} />}
        {vm.phase === "finished" && run.report_available && (
          <Link className="lp-cta" to={`/runs/${run.run_id}/report`}>Open the report →</Link>
        )}
      </div>

      <SegmentedBar agents={vm.agents} />

      <div className="lp-hero-meta">
        <span className="lp-pct">{pct}%</span>
        {vm.elapsedSecs !== null && <span>{live ? "running for" : "took"} {duration(vm.elapsedSecs)}</span>}
        {eta && <span title="Estimated from how long each area has taken so far">⏱ {eta} left in this sweep</span>}
        {live && logStatus === "live" && <span className="lp-livepill"><i /> live</span>}
        {logStatus === "waiting" && <span>waiting for the run to start writing progress…</span>}
      </div>
    </section>
  );
}

/** Whole-run bar: one segment per agent, sized by its share of the run. */
function SegmentedBar({ agents }: { agents: AgentVM[] }) {
  const share: Record<AgentKey, number> = { recon: 15, hunt: 60, validate: 20, report: 5 };
  const overall = agents.reduce((a, g) => a + share[g.key] * fill(g), 0);
  return (
    <div
      className="lp-segbar"
      role="progressbar"
      aria-label="Overall progress"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(overall)}
    >
      {agents.map((a) => (
        <div key={a.key} className={`lp-seg lp-st-${a.status}`} style={{ flexGrow: share[a.key] }} title={`${a.title}: ${a.headline}`}>
          <div className="lp-seg-fill" style={{ width: `${fill(a) * 100}%` }} />
          <span className="lp-seg-label">{a.title}</span>
        </div>
      ))}
    </div>
  );
}

const fill = (a: AgentVM) => (a.status === "done" ? 1 : a.status === "waiting" || a.status === "skipped" ? 0 : (a.progress ?? 0.5));

function NotifyButton({ vm }: { vm: ProgressVM }) {
  const [on, setOn] = useState(false);
  const prev = useRef(vm.phase);
  useEffect(() => {
    const was = prev.current;
    prev.current = vm.phase;
    if (!on || was === vm.phase || vm.phase === "running" || vm.phase === "launching") return;
    try {
      new Notification("Crucible run finished", { body: vm.headline });
    } catch {
      /* notifications blocked */
    }
  }, [vm.phase, vm.headline, on]);

  if (typeof window === "undefined" || !("Notification" in window)) return null;
  if (Notification.permission === "denied") return null;
  return (
    <button
      className={`lp-notify${on ? " on" : ""}`}
      aria-pressed={on}
      onClick={async () => {
        if (on) return setOn(false);
        const p = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
        setOn(p === "granted");
      }}
    >
      {on ? "🔔 I'll notify you" : "🔕 Notify me when done"}
    </button>
  );
}

function useTabTitle(vm: ProgressVM, repo: string) {
  useEffect(() => {
    const original = document.title;
    return () => {
      document.title = original;
    };
  }, []);
  useEffect(() => {
    const mark = { launching: "…", running: `${Math.round(vm.overall * 100)}%`, finished: "✓", failed: "✗", stopped: "■" }[vm.phase];
    document.title = `${mark} ${repo} · Crucible`;
  }, [vm.phase, vm.overall, repo]);
}

// ------------------------------------------------------------------ pipeline

function StatusOrb({ status, big }: { status: Status; big?: boolean }) {
  const glyph = { waiting: "", active: "", done: "✓", failed: "!", skipped: "–" }[status];
  return (
    <span className={`lp-orb lp-st-${status}${big ? " big" : ""}`} aria-hidden="true">
      {glyph}
    </span>
  );
}

const STATUS_WORD: Record<Status, string> = {
  waiting: "Up next", active: "Working", done: "Done", failed: "Error", skipped: "Not run",
};

function Pipeline({ agents }: { agents: AgentVM[] }) {
  return (
    <section className="lp-pipeline" aria-label="Agents">
      {agents.map((a, i) => (
        <AgentCard key={a.key} a={a} index={i + 1} />
      ))}
    </section>
  );
}

function AgentCard({ a, index }: { a: AgentVM; index: number }) {
  const eta = etaPhrase(a.etaSecs);
  return (
    <article className={`lp-card lp-st-${a.status}`} aria-label={`${a.title} agent — ${STATUS_WORD[a.status]}`}>
      <header>
        <span className="lp-card-idx">{index}</span>
        <div className="lp-card-title">
          <strong>{a.title}</strong>
          <span className="dim">{a.role}</span>
        </div>
        <span className={`lp-chip lp-st-${a.status}`}>{STATUS_WORD[a.status]}</span>
      </header>
      <div className="lp-card-headline">{a.headline}</div>
      <Bar value={a.status === "active" ? a.progress : fill(a)} status={a.status} />
      <ol className="lp-steps">
        {a.steps.map((s) => (
          <li key={s.label} className={`lp-st-${s.status}`}>
            <StatusOrb status={s.status} />
            <div>
              <div className="lp-step-label">{s.label}</div>
              {s.detail && <div className="lp-step-detail">{s.detail}</div>}
              {s.progress && s.status === "active" && <Bar value={s.progress.done / Math.max(1, s.progress.total)} status="active" thin />}
            </div>
          </li>
        ))}
      </ol>
      <footer className="dim">
        {a.elapsedSecs !== null && <span>{a.status === "active" ? "for" : "took"} {duration(a.elapsedSecs)}</span>}
        {eta && a.status === "active" && <span>⏱ {eta} left</span>}
        {a.badge && <span className="lp-badge">{a.badge}</span>}
      </footer>
    </article>
  );
}

function Bar({ value, status, thin }: { value: number | null; status: Status; thin?: boolean }) {
  const indeterminate = value === null;
  return (
    <div
      className={`lp-bar lp-st-${status}${thin ? " thin" : ""}${indeterminate ? " indeterminate" : ""}`}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : Math.round(value * 100)}
    >
      <div style={{ width: indeterminate ? undefined : `${Math.max(0, Math.min(1, value)) * 100}%` }} />
    </div>
  );
}

// ------------------------------------------------------------------ now hunting

function NowHunting({ tl, now }: { tl: Timeline; now: number }) {
  const act = activeTasks(tl);
  if (!act.length) return null;
  const done = tl.tasks.filter((t) => t.secs !== null);
  const typical = done.length ? done.reduce((a, t) => a + (t.secs ?? 0), 0) / done.length : null;
  return (
    <section className="panel lp-section">
      <h3>Hunting right now</h3>
      <ul className="lp-now">
        {act.map((t) => {
          const secs = (now - t.start) / 1000;
          const slow = typical !== null && secs > typical * 2 && secs > 60;
          return (
            <li key={t.taskId + t.start}>
              <span className="lp-radar" aria-hidden="true" />
              <div className="lp-now-text">
                <strong>{capitalize(attackName(t.attackClass))}</strong> <span className="dim">in {areaName(t.area)}</span>
              </div>
              <div className="lp-now-time">
                <Bar value={typical ? Math.min(1, secs / typical) : null} status="active" thin />
                <span className="dim">
                  {duration(secs)}
                  {slow ? " · taking longer than usual" : typical ? ` · usually ~${duration(typical)}` : ""}
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// ------------------------------------------------------------------ hunt map

type CellState = "queued" | "active" | "checked" | "found";
type MapCell = { area: string; cls: string; state: CellState; passes: number; findings: number };

function HuntMap({ tl, coverage }: { tl: Timeline; coverage: Coverage | undefined }) {
  const rows = useMemo(() => {
    const cells = new Map<string, MapCell>();
    const key = (area: string, cls: string) => `${area || "."}::${cls}`;
    for (const c of coverage?.cells ?? []) {
      cells.set(key(c.area, c.attack_class), {
        area: c.area || ".", cls: c.attack_class, passes: c.passes, findings: c.findings,
        state: c.findings > 0 ? "found" : c.passes > 0 ? "checked" : "queued",
      });
    }
    for (const t of tl.tasks) {
      const k = key(t.area, t.attackClass);
      const cur: MapCell = cells.get(k) ?? { area: t.area || ".", cls: t.attackClass, passes: 0, findings: 0, state: "queued" };
      if (t.end !== null) {
        cur.passes = Math.max(cur.passes, 1);
        cur.findings = Math.max(cur.findings, t.findings);
        cur.state = cur.findings > 0 ? "found" : "checked";
      }
      cells.set(k, cur);
    }
    for (const t of activeTasks(tl)) {
      const c = cells.get(key(t.area, t.attackClass));
      if (c) c.state = "active";
    }
    const byArea = new Map<string, MapCell[]>();
    for (const c of cells.values()) byArea.set(c.area, [...(byArea.get(c.area) ?? []), c]);
    return [...byArea.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [tl, coverage]);

  if (!rows.length) {
    return (
      <section className="panel lp-section">
        <h3>Hunt map</h3>
        <div className="dim">The map fills in once Recon has planned where to look.</div>
      </section>
    );
  }
  const all = rows.flatMap(([, cs]) => cs);
  const checked = all.filter((c) => c.state === "checked" || c.state === "found").length;

  return (
    <section className="panel lp-section">
      <div className="lp-section-head">
        <h3>Hunt map</h3>
        <span className="dim">{checked} of {plural(all.length, "area")} searched · each square is one kind of vulnerability in one part of the code</span>
      </div>
      <div className="lp-map">
        {rows.map(([area, cs]) => (
          <div key={area} className="lp-map-row">
            <div className="lp-map-area" title={areaName(area)}>{areaName(area)}</div>
            <div className="lp-map-cells">
              {cs.map((c) => (
                <span
                  key={c.cls}
                  className={`lp-cell ${c.state}`}
                  title={`${capitalize(attackName(c.cls))} in ${areaName(area)} — ${
                    c.state === "active" ? "being searched now"
                    : c.state === "found" ? `${plural(c.findings, "lead")} found`
                    : c.state === "checked" ? `searched${c.passes > 1 ? ` ${c.passes}×` : ""}, nothing found`
                    : "not searched yet"
                  }`}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="lp-legend dim">
        <span><i className="lp-cell queued" /> not yet</span>
        <span><i className="lp-cell active" /> searching now</span>
        <span><i className="lp-cell checked" /> nothing found</span>
        <span><i className="lp-cell found" /> lead found</span>
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ leads

const LEAD_STATUS: Record<string, [string, string]> = {
  raw: ["Spotted — waiting to be reproduced", "neutral"],
  mechanical_failed: ["Couldn't reproduce", "muted"],
  mechanical_passed: ["Reproduced — under review", "warn"],
  bug_refuted: ["Ruled out as a false alarm", "muted"],
  bug_upheld: ["Real bug — checking reachability", "warn"],
  reach_refuted: ["Real bug, not reachable by an attacker", "neutral"],
  reach_upheld: ["Confirmed and reachable", "bad"],
  duplicate: ["Same as another lead", "muted"],
};

function Leads({ run, findings }: { run: Run; findings: Finding[] }) {
  const c = run.counts;
  const reach = c.reach_upheld ?? 0;
  const real = (c.bug_upheld ?? 0) + reach + (c.reach_refuted ?? 0);
  const repro = (c.mechanical_passed ?? 0) + (c.bug_refuted ?? 0) + real;
  const spotted = (c.total ?? 0) - (c.duplicate ?? 0);
  const stages = [
    { k: "Spotted", v: spotted, hint: "possible issues the hunters flagged" },
    { k: "Reproduced", v: repro, hint: "a proof-of-concept actually triggered it" },
    { k: "Real bug", v: real, hint: "a reviewer agreed it's a genuine bug" },
    { k: "Reachable", v: reach, hint: "an attacker can actually get to it" },
  ];
  const max = Math.max(1, spotted);
  return (
    <section className="panel lp-section">
      <div className="lp-section-head">
        <h3>Leads so far</h3>
        {(c.duplicate ?? 0) > 0 && <span className="dim">{plural(c.duplicate ?? 0, "duplicate")} merged</span>}
      </div>
      <div className="lp-funnel">
        {stages.map((s) => (
          <div key={s.k} className="lp-funnel-row" title={s.hint}>
            <span className="lp-funnel-k">{s.k}</span>
            <div className="lp-funnel-bar"><div style={{ width: `${(s.v / max) * 100}%` }} /></div>
            <span className="lp-funnel-v">{s.v}</span>
          </div>
        ))}
      </div>
      {findings.length > 0 ? (
        <ul className="lp-leads">
          {findings.filter((f) => f.status !== "duplicate").slice(0, 8).map((f) => {
            const [label, tone] = LEAD_STATUS[f.status] ?? [f.status, "neutral"];
            return (
              <li key={f.finding_id}>
                <Sev v={f.severity} />
                <Link to={`/findings/${f.finding_id}`} className="lp-lead-title">{f.title || "Untitled lead"}</Link>
                <span className={`tag tag-${tone}`}>{label}</span>
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="dim" style={{ marginTop: 8 }}>No leads yet — they'll show up here as the hunters find them.</div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ feed

function Feed({ events, first, status }: { events: ReturnType<typeof useRunLog>["events"]; first: number | null; status: LogStatus }) {
  const [all, setAll] = useState(false);
  const items = useMemo(() => {
    // keyed by position in the event log, so earlier items keep their key
    // (and don't re-animate) as new ones arrive on top
    const out: (FeedItem & { key: number })[] = [];
    events.forEach((e, key) => {
      const it = feedItem(e);
      if (it && (all || it.highlight)) out.push({ ...it, key });
    });
    return out.reverse().slice(0, 150);
  }, [events, all]);

  return (
    <section className="panel lp-section lp-feed">
      <div className="lp-section-head">
        <h3>What's happening</h3>
        <div className="segmented lp-feed-toggle">
          <label><input type="radio" name="lp-feed" checked={!all} onChange={() => setAll(false)} />Highlights</label>
          <label><input type="radio" name="lp-feed" checked={all} onChange={() => setAll(true)} />Everything</label>
        </div>
      </div>
      {items.length ? (
        <ol className="lp-feed-list">
          {items.map((it) => (
            <li key={it.key} className={`lp-feed-item tone-${it.tone} agent-${it.agent}`}>
              <span className="lp-feed-dot" aria-hidden="true" />
              <div>
                <div>{it.text}</div>
                <div className="dim">
                  {AGENT_META[it.agent].title} · {first !== null ? `${duration((it.t - first) / 1000)} in` : ""}
                </div>
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <div className="dim">
          {status === "error" ? "Couldn't read this run's activity." : status === "loading" ? "Loading…" : "Nothing yet — updates appear here as the agents work."}
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ timeline

function RunTimeline({ tl, now }: { tl: Timeline; now: number }) {
  if (tl.first === null || !tl.spans.length) return null;
  const start = tl.first;
  const end = Math.max(now, tl.last ?? start, start + 1000);
  const span = end - start;
  const lanes: AgentKey[] = ["recon", "hunt", "validate", "report"];
  return (
    <section className="panel lp-section">
      <div className="lp-section-head">
        <h3>Timeline</h3>
        <span className="dim">when each agent was working</span>
      </div>
      <div className="lp-gantt">
        {lanes.map((k) => (
          <div key={k} className="lp-lane">
            <span className="lp-lane-k">{AGENT_META[k].title}</span>
            <div className="lp-lane-track">
              {tl.spans
                .filter((s) => (NODE_AGENT[s.node] ?? "hunt") === k)
                .map((s, i) => {
                  const e = s.end ?? now;
                  return (
                    <span
                      key={i}
                      className={`lp-lane-seg agent-${k}${s.end === null ? " running" : ""}${s.failed ? " failed" : ""}`}
                      style={{ left: `${((s.start - start) / span) * 100}%`, width: `max(3px, ${((e - s.start) / span) * 100}%)` }}
                      title={`${AGENT_META[k].title}: ${duration((e - s.start) / 1000)}`}
                    />
                  );
                })}
            </div>
          </div>
        ))}
        <div className="lp-lane lp-axis dim">
          <span className="lp-lane-k" />
          <div className="lp-lane-track">
            <span style={{ left: 0 }}>start</span>
            <span style={{ left: "50%" }}>{duration(span / 2000)}</span>
            <span style={{ right: 0 }}>{duration(span / 1000)}</span>
          </div>
        </div>
      </div>
    </section>
  );
}
