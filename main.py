"""reconcile-agent CLI.

    python main.py seed     seed a fresh twin with the 25 planted scenarios
    python main.py run      run the five-stage pipeline once, live
    python main.py eval     reseed, run, and score against seed/scenarios.md
"""
from __future__ import annotations

import argparse
import sys

from agent import ui
from agent.config import SETTINGS
from agent.pipeline import run_pipeline
from agent.tracing import TRACER
from agent.twins import simulated_session


def _session(real_twins: bool):
    """Simulator by default. Real Arga twins only behind the explicit lock."""
    if real_twins:
        from agent.twins import load_or_provision

        ui.warn("--real-twins requested: this SPENDS a monthly Arga validation run.")
        return load_or_provision(), "Arga twins (live)"
    return simulated_session(), "local twin simulator"


def cmd_seed(args) -> int:
    import sim.twin_sim as twin_sim
    from seed.seed import seed_all

    session, backend = _session(args.real_twins)
    ui.banner("seed", backend, TRACER.status, SETTINGS.agy_model)
    ui.stage(0, "Seed", "planting 25 scenarios: 20 real discrepancies + 5 decoys")

    if not args.real_twins:
        twin_sim.clear_state()
    result = seed_all(session)
    if not args.real_twins:
        twin_sim.save_state()

    for line in result["lines"]:
        style = ui.MUTED if "[DECOY]" in line else "white"
        ui.console.print(f"  [{style}]{line}[/]")
    if result["errors"]:
        for err in result["errors"]:
            ui.error(err)
        return 1

    from agent.ingest import ingest_all

    ui.console.print()
    ui.counts_table(ingest_all(session).counts())
    ui.console.print(f"\n  [bright_green]✓ seeded {len(result['lines'])} scenarios[/]\n")
    return 0


def cmd_run(args) -> int:
    session, backend = _session(args.real_twins)
    ui.banner("run", backend, TRACER.status, SETTINGS.agy_model)

    from agent.candidates import summarize as summarize_candidates
    from agent.ingest import ingest_all

    if not ingest_all(session).customers:
        ui.warn("twin looks empty — run `python main.py seed` first.")
        return 1

    hooks = {}

    def on_ingest(snapshot):
        ui.stage(1, "Ingest", "Stripe + HubSpot via twin base URLs")
        ui.counts_table(snapshot.counts())

    def on_candidates(candidates):
        ui.stage(2, "Candidates", "deterministic — rapidfuzz + field comparison, no LLM")
        ui.note(f"{len(candidates)} candidates: " +
                ", ".join(f"{k}×{v}" for k, v in sorted(summarize_candidates(candidates).items())))
        ui.candidates_table(candidates)
        ui.stage(3, "Judge", f"{SETTINGS.agy_model} via Antigravity CLI "
                             f"(~45s each, {SETTINGS.judge_concurrency}-way concurrent)")
        ui.note(f"judging {len(candidates)} candidates…")

    def on_judge(candidate, verdict, failure):
        if verdict is None:
            ui.error(f"{candidate.subject}: judge failed — {getattr(failure, 'detail', '')[:80]}")
        else:
            mark = "[bright_green]real[/]" if verdict.is_match else "[grey62]not an issue[/]"
            ui.console.print(f"  · {candidate.subject:24s} {mark} "
                             f"[grey62]conf {verdict.confidence:.2f}[/]")

    def on_findings(findings):
        ui.stage(4, "Policy router", "pure logic — automatic / slack approval / never")
        ui.findings_table(findings)
        ui.stage(5, "Execute + verify", "every write is read back before it counts")

    hooks.update(on_ingest=on_ingest, on_candidates=on_candidates,
                 on_judge=on_judge, on_findings=on_findings)

    outcome = run_pipeline(session, approval_mode=args.approval,
                           inject_fault=args.inject_fault, stub_judge=args.stub_judge,
                           hooks=hooks)

    ui.console.print()
    ui.actions_table(outcome.results)
    ui.console.print()
    for event in outcome.events:
        ui.warn(event)
    ui.console.print()
    ui.summary_panel(outcome)
    return 0


def cmd_eval(args) -> int:
    from eval.report import render_console, to_markdown
    from eval.run_eval import run_eval

    session_label = "local twin simulator"
    ui.banner("eval", session_label, TRACER.status, SETTINGS.agy_model)
    ui.stage(0, "Eval", "fresh twin → seed → full pipeline → score vs seed/scenarios.md")

    metrics = run_eval(stub_judge=args.stub_judge, inject_fault=args.inject_fault)

    ui.console.print()
    render_console(metrics)

    brief = to_markdown(metrics)
    from agent.config import REPO_ROOT

    path = REPO_ROOT / "RELIABILITY.md"
    path.write_text(brief, encoding="utf-8")
    ui.console.print(f"\n  [grey62]reliability brief written to {path.name}[/]\n")

    ok = (metrics.valid and metrics.recall == 1.0 and not metrics.acted_decoys)
    return 0 if ok else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--real-twins", action="store_true",
                       help="use live Arga twins instead of the simulator (spends quota)")
        p.add_argument("--inject-fault", metavar="SUBJECT", default=None,
                       help="swallow one write to demo the read-back catching a silent failure")
        p.add_argument("--stub-judge", action="store_true",
                       help="skip the LLM judge (fast smoke run)")
        return p

    common(sub.add_parser("seed", help="seed a fresh twin with the 25 scenarios"))
    run_p = common(sub.add_parser("run", help="run the pipeline once"))
    run_p.add_argument("--approval", choices=["auto", "poll"], default="auto",
                       help="auto-approve Slack tier, or poll for a real ✅/❌ reaction")
    common(sub.add_parser("eval", help="reseed, run, and score"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return {"seed": cmd_seed, "run": cmd_run, "eval": cmd_eval}[args.command](args)
    except KeyboardInterrupt:
        ui.warn("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
