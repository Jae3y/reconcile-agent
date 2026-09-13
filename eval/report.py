"""Phase 8b - Reporting.

`render_console` prints the eval table for the demo recording.
`to_markdown` emits a short reliability brief for the submission write-up.
"""
from __future__ import annotations

from datetime import datetime, timezone

from rich.box import HEAVY_HEAD, ROUNDED
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.ui import ACCENT, BAD, MUTED, OK, WARN, console
from seed import spec

from .run_eval import EvalMetrics


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def render_console(metrics: EvalMetrics) -> None:
    if not metrics.valid:
        console.print(Panel(
            Text(metrics.invalid_reason
                 + "  Re-run once the upstream quota resets. Verdicts are cached, "
                   "so candidates that already succeeded will not be re-charged.",
                 style="white"),
            title="[bold]RUN INVALID - metrics are not a quality measurement[/bold]",
            border_style=BAD, box=ROUNDED, padding=(1, 2)))

    table = Table(box=HEAVY_HEAD, border_style=MUTED, header_style=f"bold {ACCENT}",
                  title="[bold]reconcile-agent — eval against seed/scenarios.md[/bold]",
                  title_style="bold white")
    table.add_column("metric", style="white")
    table.add_column("result", justify="right")
    table.add_column("target", justify="right", style=MUTED)
    table.add_column("detail", style=MUTED, overflow="fold")

    real_total = spec.REAL_DISCREPANCY_COUNT
    detected = len(metrics.detected_real)

    table.add_row(
        "detection recall",
        Text(_pct(metrics.recall), style=OK if metrics.recall == 1 else WARN),
        "100%", f"{detected}/{real_total} real discrepancies detected")
    table.add_row(
        "detection precision",
        Text(_pct(metrics.precision), style=OK if metrics.precision == 1 else WARN),
        "100%",
        f"{detected} true positives, {len(metrics.flagged_decoys)} decoys flagged")
    table.add_row(
        "F1",
        Text(f"{metrics.f1:.2f}", style=OK if metrics.f1 == 1 else WARN),
        "1.00", "harmonic mean of the two above")

    far_ok = not metrics.acted_decoys
    table.add_row(
        "decoy false-action rate",
        Text(metrics.decoy_false_action_rate, style=OK if far_ok else BAD),
        "0/5",
        "no writes against any decoy" if far_ok
        else f"[bright_red]ACTED ON DECOYS: {', '.join(metrics.acted_decoys)}[/]")

    table.add_row(
        "tier routing accuracy",
        Text(_pct(metrics.tier_accuracy), style=OK if metrics.tier_accuracy == 1 else WARN),
        "100%",
        f"{len(metrics.tier_correct)} correct"
        + (f", wrong: {metrics.tier_wrong}" if metrics.tier_wrong else ""))

    table.add_row(
        "post-state correctness",
        Text(_pct(metrics.post_state_correctness),
             style=OK if metrics.post_state_correctness == 1 else WARN),
        "100%",
        f"{metrics.verified_ok} writes confirmed by read-back, "
        f"{metrics.verified_bad} unexplained mismatches")

    if metrics.injected_subject:
        table.add_row(
            "injected fault caught",
            Text("yes" if metrics.injected_caught else "NO", style=OK if metrics.injected_caught else BAD),
            "yes",
            f"deliberate silent failure on {metrics.injected_subject}; excluded from post-state")

    table.add_row(
        "verdict provenance",
        Text(f"{metrics.fresh_verdicts} fresh", style=MUTED),
        "-",
        f"{metrics.cached_verdicts} reused from cache (identical candidate facts, same model)")

    table.add_row(
        "judge failures",
        Text(str(metrics.judge_failures), style=OK if not metrics.judge_failures else BAD),
        "0",
        (f"{metrics.quota_failures} unjudged: upstream quota (429)"
         if metrics.quota_failures else "candidates skipped after two malformed CLI replies"))

    console.print(table)

    if metrics.missed_real:
        console.print(Panel(", ".join(metrics.missed_real),
                            title="[bold]missed real discrepancies[/bold]",
                            border_style=BAD, box=ROUNDED))
    if metrics.verified_bad:
        subjects = [r.subject for r in (metrics.outcome.results if metrics.outcome else [])
                    if r.status == "verify_failed"]
        console.print(Panel(
            Text(f"{', '.join(subjects)}\n\n"
                 "The write reported success but the read-back disagreed, so the action was "
                 "recorded as failed rather than silently trusted. This is the intended "
                 "behaviour of the verify stage.", style="white"),
            title="[bold]silent failures caught by read-back[/bold]",
            border_style=WARN, box=ROUNDED))


def to_markdown(metrics: EvalMetrics) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    outcome = metrics.outcome
    judge_seconds = (outcome.timings.get("judge", 0) if outcome else 0)
    caught = metrics.injected_subject if metrics.injected_caught else None

    lines = [
        "# reconcile-agent — reliability brief",
        "",
        f"_Generated {stamp} from a freshly seeded twin (25 planted scenarios: "
        f"{spec.REAL_DISCREPANCY_COUNT} real discrepancies, {spec.DECOY_COUNT} decoys)._",
        "",
        ("" if metrics.valid else
         f"> **RUN INVALID.** {metrics.invalid_reason}\n"),
        "| Metric | Result | Target |",
        "| --- | --- | --- |",
        f"| Detection recall | {_pct(metrics.recall)} "
        f"({len(metrics.detected_real)}/{spec.REAL_DISCREPANCY_COUNT}) | 100% |",
        f"| Detection precision | {_pct(metrics.precision)} | 100% |",
        f"| F1 | {metrics.f1:.2f} | 1.00 |",
        f"| Decoy false-action rate | {metrics.decoy_false_action_rate} | 0/5 |",
        f"| Tier routing accuracy | {_pct(metrics.tier_accuracy)} | 100% |",
        f"| Post-state correctness | {_pct(metrics.post_state_correctness)} "
        f"({metrics.verified_ok} confirmed by read-back) | 100% |",
        f"| Judge failures | {metrics.judge_failures} | 0 |",
    ] + ([f"| Injected silent failure caught | {'yes' if metrics.injected_caught else 'NO'} | yes |"]
         if metrics.injected_subject else []) + [
        "",
        "## How the numbers are produced",
        "",
        "Every write is followed by an independent re-read of the record through "
        "`agent/verify.py`; an action is only counted as applied once the twin confirms "
        "the new value. Post-state correctness is that read-back, not the write's own "
        "return value.",
        "",
    ]

    if caught:
        lines += [
            f"A silent failure was deliberately injected for **{caught}** — the write "
            "returns success without changing anything. The read-back caught it, the executor "
            "retried once, and it was then recorded as a failed action rather than reported as "
            "applied.",
            "",
        ]

    lines += [
        "## Known limitations",
        "",
        "- **Judgement is probabilistic.** The duplicate-company decoy (Acme Inc vs Acme Labs) "
        "is indistinguishable from a true duplicate by fuzzy matching alone — both score 100% "
        "after normalising legal suffixes — so correctness there rests entirely on the LLM "
        "judge weighing domain evidence. It is right consistently in testing, but it is not a "
        "guarantee, which is why every destructive category stays behind Slack approval.",
        "- **Approvals are simulated in eval.** Unattended runs auto-approve Slack-tier actions "
        "so the harness can score post-state correctness. A human reacting with ✅/❌ is the "
        "real path (`--approval poll`); the eval therefore measures detection and execution "
        "quality, not human agreement rates.",
        "- **Merges and refunds are never executed.** Duplicates are *flagged* rather than "
        "merged, because merging deletes a record; refunds, deletions and outbound customer "
        "email are excluded by category in `agent/policy.py` and unreachable at any confidence.",
        f"- **Judge latency dominates.** The Antigravity CLI takes ~45–50s per candidate; this "
        f"run spent {judge_seconds:,.0f}s in the judge stage across "
        f"{len(outcome.judged) if outcome else 0} candidates at 6-way concurrency.",
        "",
    ]
    return "\n".join(lines)
