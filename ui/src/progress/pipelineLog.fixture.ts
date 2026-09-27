// A real run.log (INFO lines) from a registry-less `crucible run` of a tiny
// two-route Flask repo: every stage runs, Hunt/validate skip for lack of a
// model. Regenerate by running the graph with NodeDeps(registry=None) if the
// backend log wording changes.
export const PIPELINE_LOG = `
2026-09-26 15:35:05,441 INFO    crucible.graph:158  → recon
2026-09-26 15:35:05,442 INFO    crucible.recon:71  recon start  repo=/tmp/lp-fixture/repo
2026-09-26 15:35:05,478 INFO    crucible.recon:78  R0 seed  0.0s  kind=unknown lang=python files=1 entry_points=2 reflection=0 call_edges=7
2026-09-26 15:35:05,478 INFO    crucible.recon:107  R1a partition  1 subsystem(s) via fallback  api(9)
2026-09-26 15:35:05,478 INFO    crucible.recon:120  R1b subsystem maps  skipped (no registry) — seed-only map
2026-09-26 15:35:05,479 INFO    crucible.recon:166  R1c synthesis  quality=seed_only  subsystems=1  attack_surface=2  top: api/app.py:4 (flask/fastapi)=6; api/app.py:7 (flask/fastapi)=6
2026-09-26 15:35:05,480 INFO    crucible.recon:202  R3 decompose  areas=['api']  cross-subsystem taint=0
2026-09-26 15:35:05,480 INFO    crucible.recon:204  R3 decompose  14/24 chunks queued  {'surface': 2, 'catch_all': 12}
2026-09-26 15:35:05,492 INFO    crucible.recon:209  recon done  pending_hunts=14
2026-09-26 15:35:05,492 INFO    crucible.graph:167  ✓ recon  0.1s
2026-09-26 15:35:05,493 INFO    crucible.graph:158  → hunt
2026-09-26 15:35:05,494 INFO    crucible.hunt:120  hunt start  pending=14  continuation=1/3
2026-09-26 15:35:05,494 INFO    crucible.hunt:125  hunt  skipped (no registry) — clearing the queue
2026-09-26 15:35:05,505 INFO    crucible.graph:167  ✓ hunt  0.0s
2026-09-26 15:35:05,506 INFO    crucible.graph:158  → dedup
2026-09-26 15:35:05,506 INFO    crucible.dedup:83  dedup  skipped (findings=0, store=False)
2026-09-26 15:35:05,507 INFO    crucible.graph:167  ✓ dedup  0.0s
2026-09-26 15:35:05,508 INFO    crucible.graph:158  → validate_mechanical
2026-09-26 15:35:05,508 INFO    crucible.validate_mechanical:50  validate_mechanical  0 finding(s): 0 passed, 0 failed
2026-09-26 15:35:05,508 INFO    crucible.graph:167  ✓ validate_mechanical  0.0s
2026-09-26 15:35:05,509 INFO    crucible.graph:158  → gapfill
2026-09-26 15:35:05,522 INFO    crucible.gapfill:93  gapfill done  matrix=7 covered=0 failed=0 missing=7 barren=0 requeued=7  queue=7
2026-09-26 15:35:05,523 INFO    crucible.graph:167  ✓ gapfill  0.0s
2026-09-26 15:35:05,524 INFO    crucible.graph:158  → feedback
2026-09-26 15:35:05,524 INFO    crucible.feedback:90  feedback done  rewrote 0/7 queued prompt(s)  {'validation_failure': 0, 'shallow': 0, 'repeated_miss': 0}
2026-09-26 15:35:05,525 INFO    crucible.graph:167  ✓ feedback  0.0s
2026-09-26 15:35:05,525 INFO    crucible.graph:158  → loop_control
2026-09-26 15:35:05,526 INFO    crucible.loop:37  loop  cycle 1/2 complete — re-hunting 7 re-queued cell(s); findings so far=0
2026-09-26 15:35:05,542 INFO    crucible.graph:167  ✓ loop_control  0.0s
2026-09-26 15:35:05,544 INFO    crucible.graph:158  → hunt
2026-09-26 15:35:05,544 INFO    crucible.hunt:120  hunt start  pending=7  continuation=1/3
2026-09-26 15:35:05,544 INFO    crucible.hunt:125  hunt  skipped (no registry) — clearing the queue
2026-09-26 15:35:05,559 INFO    crucible.graph:167  ✓ hunt  0.0s
2026-09-26 15:35:05,560 INFO    crucible.graph:158  → dedup
2026-09-26 15:35:05,561 INFO    crucible.dedup:83  dedup  skipped (findings=0, store=False)
2026-09-26 15:35:05,561 INFO    crucible.graph:167  ✓ dedup  0.0s
2026-09-26 15:35:05,562 INFO    crucible.graph:158  → validate_mechanical
2026-09-26 15:35:05,562 INFO    crucible.validate_mechanical:50  validate_mechanical  0 finding(s): 0 passed, 0 failed
2026-09-26 15:35:05,562 INFO    crucible.graph:167  ✓ validate_mechanical  0.0s
2026-09-26 15:35:05,563 INFO    crucible.graph:158  → gapfill
2026-09-26 15:35:05,580 INFO    crucible.gapfill:93  gapfill done  matrix=7 covered=0 failed=0 missing=7 barren=0 requeued=7  queue=7
2026-09-26 15:35:05,580 INFO    crucible.graph:167  ✓ gapfill  0.0s
2026-09-26 15:35:05,581 INFO    crucible.graph:158  → feedback
2026-09-26 15:35:05,581 INFO    crucible.feedback:90  feedback done  rewrote 0/7 queued prompt(s)  {'validation_failure': 0, 'shallow': 0, 'repeated_miss': 0}
2026-09-26 15:35:05,582 INFO    crucible.graph:167  ✓ feedback  0.0s
2026-09-26 15:35:05,583 INFO    crucible.graph:158  → loop_control
2026-09-26 15:35:05,583 INFO    crucible.loop:43  loop  producer-consumer loop done after 2 cycle(s) (cycle cap reached) — 0 finding(s) to the validate/report tail
2026-09-26 15:35:05,601 INFO    crucible.graph:167  ✓ loop_control  0.0s
2026-09-26 15:35:05,602 INFO    crucible.graph:158  → validate_bug
2026-09-26 15:35:05,602 INFO    crucible.validate_bug:41  validate_bug  no store — skipping
2026-09-26 15:35:05,602 INFO    crucible.graph:167  ✓ validate_bug  0.0s
2026-09-26 15:35:05,603 INFO    crucible.graph:158  → validate_reachability
2026-09-26 15:35:05,603 INFO    crucible.validate_reachability:42  validate_reachability  no store — skipping
2026-09-26 15:35:05,603 INFO    crucible.graph:167  ✓ validate_reachability  0.0s
2026-09-26 15:35:05,605 INFO    crucible.graph:158  → report
2026-09-26 15:35:05,626 INFO    crucible.report:58  report  0 upheld / 0 findings  (empty)  -> report.json
2026-09-26 15:35:05,627 INFO    crucible.graph:167  ✓ report  0.0s
`;
