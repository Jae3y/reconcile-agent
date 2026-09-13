"""The five-stage pipeline, wired end to end and traced.

    ingest -> candidates -> judge -> policy -> executor(+verify)

Deliberately plain Python: no agent framework, no graph engine. The only LLM
call in the whole run is stage 3.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .candidates import build_candidates, summarize as summarize_candidates
from .executor import Executor
from .ingest import Snapshot, ingest_all
from .judge import CACHE_HITS, judge_all
from .models import ActionResult, Candidate, Finding, JudgeResult
from .policy import route_all, summarize as summarize_tiers
from .tracing import TRACER


@dataclass
class RunOutcome:
    snapshot: Snapshot | None = None
    candidates: list[Candidate] = field(default_factory=list)
    judged: list[tuple[Candidate, JudgeResult]] = field(default_factory=list)
    judge_failures: list[Any] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    results: list[ActionResult] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    cached_verdicts: int = 0
    fresh_verdicts: int = 0
    events: list[str] = field(default_factory=list)
    run_id: str = ""

    def applied(self) -> list[ActionResult]:
        return [r for r in self.results if r.status == "applied"]

    def acted_subjects(self) -> set[str]:
        """Subjects where the agent actually changed something."""
        return {r.subject for r in self.results if r.status in {"applied", "verify_failed"}}


def run_pipeline(
    session,
    *,
    approval_mode: str = "auto",
    inject_fault: str | None = None,
    stub_judge: bool = False,
    hooks: dict | None = None,
) -> RunOutcome:
    """Execute one full reconciliation run.

    `stub_judge` bypasses the CLI for fast smoke runs; the real pipeline and the
    eval always use the live judge.
    """
    hooks = hooks or {}
    outcome = RunOutcome()

    def body(trace):
        outcome.run_id = trace.run_id

        # -- Stage 1: ingest -------------------------------------------
        t0 = time.time()
        snapshot = ingest_all(session)
        outcome.snapshot = snapshot
        outcome.timings["ingest"] = time.time() - t0
        trace.span("ingest", output=snapshot.counts(),
                   metadata={"seconds": round(outcome.timings["ingest"], 2)})
        if hooks.get("on_ingest"):
            hooks["on_ingest"](snapshot)

        # -- Stage 2: candidates (deterministic, no LLM) ---------------
        t0 = time.time()
        candidates = build_candidates(snapshot)
        outcome.candidates = candidates
        outcome.timings["candidates"] = time.time() - t0
        trace.span("candidates", output=summarize_candidates(candidates),
                   metadata={"count": len(candidates), "llm_calls": 0})
        if hooks.get("on_candidates"):
            hooks["on_candidates"](candidates)

        # -- Stage 3: judge (the one LLM step) -------------------------
        t0 = time.time()
        if stub_judge:
            judged = [(c, JudgeResult(is_match=True, discrepancy_type=c.kind.value,
                                      confidence=0.95, reasoning="stubbed judge"))
                      for c in candidates]
            failures: list[Any] = []
        else:
            def on_result(cand, verdict, failure):
                if verdict is not None:
                    trace.generation(
                        f"judge:{cand.kind.value}",
                        input={"subject": cand.subject, "facts": cand.facts},
                        output=verdict.model_dump(),
                        metadata={"candidate": cand.key})
                else:
                    trace.event("judge-failure", subject=cand.subject,
                                detail=getattr(failure, "detail", ""))
                if hooks.get("on_judge"):
                    hooks["on_judge"](cand, verdict, failure)

            CACHE_HITS.clear()
            judged, failures, _ = judge_all(candidates, on_result=on_result)
            outcome.cached_verdicts = len(CACHE_HITS)
            outcome.fresh_verdicts = len(judged) - outcome.cached_verdicts
        outcome.judged = judged
        outcome.judge_failures = failures
        outcome.timings["judge"] = time.time() - t0
        trace.span("judge", output={"judged": len(judged), "failed": len(failures),
                                    "cached": outcome.cached_verdicts,
                                    "fresh": outcome.fresh_verdicts},
                   metadata={"seconds": round(outcome.timings["judge"], 2)})

        # -- Stage 4: policy router (pure logic) -----------------------
        t0 = time.time()
        findings = route_all(judged)
        outcome.findings = findings
        outcome.timings["policy"] = time.time() - t0
        trace.span("policy", output=summarize_tiers(findings), metadata={"llm_calls": 0})
        if hooks.get("on_findings"):
            hooks["on_findings"](findings)

        # -- Stage 5: execute + verify ---------------------------------
        t0 = time.time()
        executor = Executor(session=session, approval_mode=approval_mode,
                            inject_silent_failure_for=inject_fault)

        def on_action(result: ActionResult):
            trace.tool(
                f"execute:{result.kind.value}",
                input={"subject": result.subject, "action": result.description,
                       "tier": result.tier.value},
                output={"status": result.status,
                        "verified": bool(result.verify and result.verify.ok),
                        "observed": result.verify.observed if result.verify else None},
                metadata={"attempts": result.attempts, "detail": result.detail})
            if result.status == "verify_failed":
                trace.event("SILENT-FAILURE-CAUGHT", subject=result.subject,
                            expected=result.verify.expected if result.verify else None,
                            observed=result.verify.observed if result.verify else None)
            if hooks.get("on_action"):
                hooks["on_action"](result)

        outcome.results = executor.execute(findings, on_result=on_action)
        outcome.events = executor.events
        outcome.timings["execute"] = time.time() - t0
        trace.span("execute",
                   output={"applied": len(outcome.applied()),
                           "verify_failed": sum(1 for r in outcome.results
                                                if r.status == "verify_failed"),
                           "skipped": sum(1 for r in outcome.results if r.status == "skipped")},
                   metadata={"seconds": round(outcome.timings["execute"], 2)})
        return outcome

    result, _trace = TRACER.run("reconcile-agent-run", body,
                                metadata={"approval_mode": approval_mode,
                                          "injected_fault": inject_fault or "none"})
    return result
