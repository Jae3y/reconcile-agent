"""Phase 8 - Eval harness.

Reseeds a fresh twin, runs the full pipeline against it, and scores the result
against seed/spec.py (the machine-readable form of seed/scenarios.md).

Scored quantities
-----------------
detection  a real discrepancy counts as DETECTED when the judge marked it a
           genuine issue and the router gave it an actionable tier.
precision  detected-real / (detected-real + decoys-flagged).
decoy FAR  decoys the agent actually wrote against, over 5. Target 0/5.
           Note this counts WRITES, not opinions: flagging without writing is
           already visible in precision.
post-state every applied action re-read through verify.py; this is the share
           that the twin confirms actually changed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agent.pipeline import RunOutcome, run_pipeline
from agent.twins import simulated_session
from agent.ui import console
from seed import spec
from seed.seed import seed_all

ACTED = {"applied", "verify_failed"}


@dataclass
class EvalMetrics:
    detected_real: list[str] = field(default_factory=list)
    missed_real: list[str] = field(default_factory=list)
    flagged_decoys: list[str] = field(default_factory=list)
    acted_decoys: list[str] = field(default_factory=list)
    tier_correct: list[str] = field(default_factory=list)
    tier_wrong: list[tuple[str, str, str]] = field(default_factory=list)
    verified_ok: int = 0
    verified_bad: int = 0
    judge_failures: int = 0
    outcome: RunOutcome | None = None
    injected_subject: str | None = None
    injected_caught: bool = False
    quota_failures: int = 0
    candidates_total: int = 0
    cached_verdicts: int = 0
    fresh_verdicts: int = 0

    @property
    def valid(self) -> bool:
        """A run is only measurable if every candidate actually got a verdict.

        Scoring a run where the judge never replied produces an authoritative
        looking number that measures upstream availability, not agent quality.
        """
        return self.judge_failures == 0

    @property
    def invalid_reason(self) -> str:
        if self.valid:
            return ""
        if self.quota_failures:
            return (f"{self.quota_failures} of {self.candidates_total} candidates were never "
                    f"judged: the Antigravity CLI reported RESOURCE_EXHAUSTED (429). "
                    f"Detection metrics below are NOT a measure of agent quality.")
        return (f"{self.judge_failures} of {self.candidates_total} candidates could not be "
                f"judged (malformed CLI replies). Metrics below are incomplete.")

    @property
    def recall(self) -> float:
        total = len(self.detected_real) + len(self.missed_real)
        return len(self.detected_real) / total if total else 0.0

    @property
    def precision(self) -> float:
        denom = len(self.detected_real) + len(self.flagged_decoys)
        return len(self.detected_real) / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def decoy_false_action_rate(self) -> str:
        return f"{len(self.acted_decoys)}/{spec.DECOY_COUNT}"

    @property
    def post_state_correctness(self) -> float:
        """Share of genuine writes the twin confirmed on read-back.

        A deliberately injected fault is excluded from this denominator: it is a
        demonstration that the read-back works, not a defect in the agent. It is
        reported separately as `injected_caught`.
        """
        total = self.verified_ok + self.verified_bad
        return self.verified_ok / total if total else 0.0

    @property
    def tier_accuracy(self) -> float:
        total = len(self.tier_correct) + len(self.tier_wrong)
        return len(self.tier_correct) / total if total else 0.0


def score(outcome: RunOutcome, injected_subject: str | None = None) -> EvalMetrics:
    expectations = spec.expectations()
    metrics = EvalMetrics(outcome=outcome, injected_subject=injected_subject)
    metrics.judge_failures = len(outcome.judge_failures)
    metrics.quota_failures = sum(1 for f in outcome.judge_failures
                                 if getattr(f, 'quota', False))
    metrics.candidates_total = len(outcome.candidates)
    metrics.cached_verdicts = outcome.cached_verdicts
    metrics.fresh_verdicts = outcome.fresh_verdicts

    # What did the agent conclude per subject?
    verdicts: dict[str, bool] = {}
    tiers: dict[str, str] = {}
    for finding in outcome.findings:
        subject = spec.canonical(finding.candidate.subject)
        verdicts[subject] = verdicts.get(subject, False) or finding.judge.is_match
        if finding.tier.value != "no_action":
            tiers[subject] = finding.tier.value

    acted: dict[str, str] = {}
    for result in outcome.results:
        subject = spec.canonical(result.subject)
        if result.status in ACTED:
            acted[subject] = result.status

    for subject, expected in expectations.items():
        flagged = verdicts.get(subject, False)
        if expected["decoy"]:
            if flagged:
                metrics.flagged_decoys.append(subject)
            if subject in acted:
                metrics.acted_decoys.append(subject)
            continue

        if flagged and subject in tiers:
            metrics.detected_real.append(subject)
            actual_tier = tiers[subject]
            if actual_tier == expected["tier"]:
                metrics.tier_correct.append(subject)
            else:
                metrics.tier_wrong.append((subject, expected["tier"], actual_tier))
        else:
            metrics.missed_real.append(subject)

    for result in outcome.results:
        if injected_subject and result.subject == injected_subject:
            # The fault was planted on purpose; scoring it as a defect would make
            # a correct run permanently unable to reach 100%.
            metrics.injected_caught = result.status == "verify_failed"
            continue
        if result.status == "applied" and result.verify and result.verify.ok:
            metrics.verified_ok += 1
        elif result.status == "verify_failed":
            metrics.verified_bad += 1

    for bucket in (metrics.detected_real, metrics.missed_real,
                   metrics.flagged_decoys, metrics.acted_decoys):
        bucket.sort()
    return metrics


def run_eval(*, stub_judge: bool = False, inject_fault: str | None = None,
             quiet: bool = False) -> EvalMetrics:
    """Fresh twin -> seed -> full pipeline -> score."""
    import sim.twin_sim as twin_sim

    session = simulated_session()
    twin_sim.reset()
    if not quiet:
        console.print("  [grey62]reseeding a fresh twin…[/]")
    seeded = seed_all(session)
    if seeded["errors"]:
        console.print(f"  [bright_red]seed errors: {seeded['errors'][:3]}[/]")

    outcome = run_pipeline(session, approval_mode="auto",
                           inject_fault=inject_fault, stub_judge=stub_judge)
    return score(outcome, injected_subject=inject_fault)
