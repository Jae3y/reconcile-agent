"""Phase 10 - background run manager.

Runs execute on a worker thread so the HTTP request never blocks. Every
interesting moment is appended to the run's event log with a monotonic `seq`,
which is what makes the SSE stream replayable: a judge who opens the dashboard
halfway through a run still sees the whole story from the beginning, then tails
live from the same cursor.

The pipeline itself is untouched - this module only observes it through the
hooks `agent.pipeline.run_pipeline` already exposes.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent.config import REPO_ROOT
from agent.pipeline import run_pipeline
from agent.twins import simulated_session
from seed import spec
from seed.seed import seed_all

from .models import (
    ActionItem,
    JudgedItem,
    Metrics,
    RunDetail,
    RunEvent,
    RunSummary,
    StageState,
)

HISTORY_DIR = REPO_ROOT / "runs" / "history"
STAGE_ORDER: list[str] = ["ingest", "candidates", "judge", "policy", "execute"]

STAGE_LABEL = {
    "ingest": "Ingest",
    "candidates": "Candidates",
    "judge": "Judge",
    "policy": "Policy router",
    "execute": "Executor + verify",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunRecord:
    """Live state for one run. Converted to RunDetail for the API."""

    def __init__(self, kind: str, inject_fault: str | None):
        self.run_id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.status = "queued"
        self.started_at = _now()
        self.finished_at: str | None = None
        self.duration_seconds: float | None = None
        self.trace_id = ""
        self.error = ""
        self.inject_fault = inject_fault
        self.counts: dict[str, int] = {}
        self.stages = [StageState(name=n) for n in STAGE_ORDER]  # type: ignore[arg-type]
        self.judged: list[JudgedItem] = []
        self.actions: list[ActionItem] = []
        self.events: list[RunEvent] = []
        self.metrics: Metrics | None = None
        self._seq = 0
        self._lock = threading.Lock()
        self.done = threading.Event()

    # -- events --------------------------------------------------------
    def emit(self, type_: str, title: str, *, stage=None, level="info",
             message: str = "", data: dict | None = None) -> None:
        with self._lock:
            self._seq += 1
            self.events.append(RunEvent(
                seq=self._seq, run_id=self.run_id, at=_now(), type=type_,
                stage=stage, level=level, title=title, message=message,
                data=data or {}))

    def events_since(self, seq: int) -> list[RunEvent]:
        with self._lock:
            return [e for e in self.events if e.seq > seq]

    def set_stage(self, name: str, status: str, detail: str = "",
                  count: int | None = None, seconds: float | None = None) -> None:
        for st in self.stages:
            if st.name == name:
                st.status = status  # type: ignore[assignment]
                if detail:
                    st.detail = detail
                if count is not None:
                    st.count = count
                if seconds is not None:
                    st.seconds = seconds
        self.emit("stage", STAGE_LABEL.get(name, name), stage=name,
                  level="success" if status == "done" else "info",
                  message=detail, data={"status": status, "count": count})

    # -- serialisation -------------------------------------------------
    def summary(self) -> RunSummary:
        return RunSummary(
            run_id=self.run_id, kind=self.kind, status=self.status,  # type: ignore[arg-type]
            started_at=self.started_at, finished_at=self.finished_at,
            duration_seconds=self.duration_seconds, trace_id=self.trace_id,
            error=self.error,
            candidates=len(self.judged) or (self.counts.get("candidates") or 0),
            applied=sum(1 for a in self.actions if a.status == "applied"),
            verify_failed=sum(1 for a in self.actions if a.status == "verify_failed"),
            skipped=sum(1 for a in self.actions if a.status == "skipped"),
            no_action=sum(1 for a in self.actions if a.status == "no_action"),
            metrics=self.metrics)

    def detail(self) -> RunDetail:
        base = self.summary().model_dump()
        return RunDetail(**base, stages=self.stages, counts=self.counts,
                         judged=self.judged, actions=self.actions, events=self.events)

    def persist(self) -> None:
        try:
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            (HISTORY_DIR / f"{self.run_id}.json").write_text(
                self.detail().model_dump_json(indent=2), encoding="utf-8")
        except OSError:
            pass


class RunManager:
    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._busy = threading.Lock()

    # -- history -------------------------------------------------------
    def history(self, limit: int = 50) -> list[RunSummary]:
        live = []
        with self._lock:
            for rid in reversed(self._order):
                live.append(self._runs[rid].summary())
        seen = {s.run_id for s in live}
        stored: list[RunSummary] = []
        if HISTORY_DIR.exists():
            for path in sorted(HISTORY_DIR.glob("*.json"),
                               key=lambda p: p.stat().st_mtime, reverse=True):
                if path.stem in seen:
                    continue
                try:
                    stored.append(RunSummary(**json.loads(path.read_text(encoding="utf-8"))))
                except Exception:
                    continue
        return (live + stored)[:limit]

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._runs.get(run_id)

    def load_detail(self, run_id: str) -> RunDetail | None:
        record = self.get(run_id)
        if record:
            return record.detail()
        path = HISTORY_DIR / f"{run_id}.json"
        if path.exists():
            try:
                return RunDetail(**json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                return None
        return None

    # -- execution -----------------------------------------------------
    def start(self, kind: str, inject_fault: str | None = None) -> RunRecord:
        record = RunRecord(kind, inject_fault)
        with self._lock:
            self._runs[record.run_id] = record
            self._order.append(record.run_id)
        threading.Thread(target=self._execute, args=(record,), daemon=True).start()
        return record

    def _execute(self, record: RunRecord) -> None:
        started = time.time()
        # One run at a time: concurrent runs would fight over simulator state
        # and multiply load on the judge's quota.
        with self._busy:
            try:
                record.status = "running"
                record.emit("run.start", f"{record.kind} started",
                            message=f"kind={record.kind}", data={"kind": record.kind})

                session = simulated_session()

                if record.kind == "eval":
                    import sim.twin_sim as twin_sim

                    twin_sim.reset()
                    record.emit("seed", "Reseeding a fresh twin",
                                message="25 planted scenarios: 20 real, 5 decoys")
                    seeded = seed_all(session)
                    twin_sim.save_state()
                    record.emit("seed", "Twin seeded", level="success",
                                message=f"{len(seeded['lines'])} scenarios",
                                data={"errors": seeded["errors"][:3]})

                outcome = run_pipeline(
                    session, approval_mode="auto",
                    inject_fault=record.inject_fault,
                    hooks=self._hooks(record))

                record.trace_id = outcome.run_id
                record.counts["candidates"] = len(outcome.candidates)

                # Close the final stage: the executor hook fires per action and
                # has no way to know which one was last, so the stage would
                # otherwise stay "active" forever on a finished run.
                applied = sum(1 for a in record.actions if a.status == "applied")
                caught = sum(1 for a in record.actions if a.status == "verify_failed")
                record.set_stage(
                    "execute", "done",
                    detail=(f"{applied} applied + verified"
                            + (f", {caught} silent failure(s) caught" if caught else "")),
                    count=len(record.actions),
                    seconds=round(outcome.timings.get("execute", 0.0), 1))

                if record.kind == "eval":
                    from eval.run_eval import score

                    m = score(outcome, injected_subject=record.inject_fault)
                    record.metrics = Metrics(
                        valid=m.valid, invalid_reason=m.invalid_reason,
                        recall=m.recall, precision=m.precision, f1=m.f1,
                        decoy_false_action_rate=m.decoy_false_action_rate,
                        decoys_acted=len(m.acted_decoys), decoy_total=spec.DECOY_COUNT,
                        tier_accuracy=m.tier_accuracy,
                        post_state_correctness=m.post_state_correctness,
                        detected_real=len(m.detected_real),
                        real_total=spec.REAL_DISCREPANCY_COUNT,
                        missed=m.missed_real, judge_failures=m.judge_failures,
                        quota_failures=m.quota_failures,
                        cached_verdicts=m.cached_verdicts,
                        fresh_verdicts=m.fresh_verdicts,
                        injected_subject=m.injected_subject,
                        injected_caught=m.injected_caught,
                        verified_ok=m.verified_ok)
                    level = "success" if m.valid else "error"
                    record.emit("metrics", "Eval scored", level=level,
                                message=(m.invalid_reason or
                                         f"recall {m.recall:.0%}, decoys {m.decoy_false_action_rate}"),
                                data=record.metrics.model_dump())

                record.status = "succeeded"
                record.emit("run.end", f"{record.kind} complete", level="success",
                            message=f"{len(record.actions)} actions")
            except Exception as exc:
                record.status = "failed"
                record.error = f"{type(exc).__name__}: {exc}"
                record.emit("run.error", "Run failed", level="error",
                            message=record.error,
                            data={"traceback": traceback.format_exc()[-1500:]})
            finally:
                record.finished_at = _now()
                record.duration_seconds = round(time.time() - started, 2)
                record.persist()
                record.done.set()

    def _hooks(self, record: RunRecord) -> dict:
        timings: dict[str, float] = {}

        def on_ingest(snapshot):
            record.counts.update(snapshot.counts())
            total = sum(snapshot.counts().values())
            record.set_stage("ingest", "done",
                             detail=f"{total} records from Stripe + HubSpot", count=total)
            record.emit("ingest", "Records ingested", stage="ingest", level="success",
                        message=", ".join(f"{k.split('.')[-1]} {v}"
                                          for k, v in snapshot.counts().items()),
                        data=snapshot.counts())
            record.set_stage("candidates", "active", detail="deterministic pass")

        def on_candidates(candidates):
            by_kind: dict[str, int] = {}
            for c in candidates:
                by_kind[c.kind.value] = by_kind.get(c.kind.value, 0) + 1
            record.counts["candidates"] = len(candidates)
            record.set_stage("candidates", "done",
                             detail=f"{len(candidates)} candidates, 0 LLM calls",
                             count=len(candidates))
            record.emit("candidates", "Candidates generated", stage="candidates",
                        level="success",
                        message=", ".join(f"{k} x{v}" for k, v in sorted(by_kind.items())),
                        data=by_kind)
            record.set_stage("judge", "active",
                             detail=f"judging {len(candidates)} candidates")
            timings["judge"] = time.time()

        def on_judge(candidate, verdict, failure):
            if verdict is None:
                record.emit("judge", f"{candidate.subject}: judge failed",
                            stage="judge", level="error",
                            message=getattr(failure, "detail", "")[:200],
                            data={"subject": candidate.subject,
                                  "quota": bool(getattr(failure, "quota", False))})
                return
            record.judged.append(JudgedItem(
                subject=candidate.subject, discrepancy_type=candidate.kind.value,
                is_match=verdict.is_match, confidence=verdict.confidence,
                reasoning=verdict.reasoning))
            record.emit("judge",
                        f"{candidate.subject}: {'real issue' if verdict.is_match else 'not an issue'}",
                        stage="judge",
                        level="success" if verdict.is_match else "info",
                        message=verdict.reasoning[:220],
                        data={"subject": candidate.subject,
                              "type": candidate.kind.value,
                              "is_match": verdict.is_match,
                              "confidence": verdict.confidence,
                              "judged": len(record.judged)})

        def on_findings(findings):
            tiers: dict[str, int] = {}
            for f in findings:
                tiers[f.tier.value] = tiers.get(f.tier.value, 0) + 1
            record.set_stage("judge", "done", detail=f"{len(record.judged)} verdicts",
                             count=len(record.judged),
                             seconds=round(time.time() - timings.get("judge", time.time()), 1))
            record.set_stage("policy", "done",
                             detail=", ".join(f"{k} x{v}" for k, v in sorted(tiers.items())),
                             count=len(findings))
            record.emit("policy", "Findings routed", stage="policy", level="success",
                        message=", ".join(f"{k} x{v}" for k, v in sorted(tiers.items())),
                        data=tiers)
            record.set_stage("execute", "active", detail="writing + reading back")

        def on_action(result):
            verify = result.verify
            record.actions.append(ActionItem(
                subject=result.subject, discrepancy_type=result.kind.value,
                tier=result.tier.value, status=result.status,
                description=result.description, detail=result.detail,
                attempts=result.attempts,
                verified=(verify.ok if verify else None),
                verify_field=(verify.field if verify else ""),
                verify_expected=(verify.expected if verify else None),
                verify_observed=(verify.observed if verify else None)))

            level = {"applied": "success", "verify_failed": "error",
                     "skipped": "warning", "denied": "error"}.get(result.status, "info")
            title = {
                "applied": f"Write verified — {result.subject}",
                "verify_failed": f"Silent failure caught and retried — {result.subject}",
                "skipped": f"Skipped — {result.subject}",
                "no_action": f"No action — {result.subject}",
            }.get(result.status, f"{result.status} — {result.subject}")

            if result.tier.value == "slack_approval" and result.status != "no_action":
                record.emit("slack", f"Slack approval requested — {result.subject}",
                            stage="execute", level="warning",
                            message=result.description,
                            data={"subject": result.subject, "channel": "#approvals"})

            record.emit("action", title, stage="execute", level=level,
                        message=result.description,
                        data={"subject": result.subject, "status": result.status,
                              "tier": result.tier.value,
                              "verified": bool(verify and verify.ok),
                              "observed": verify.observed if verify else None,
                              "expected": verify.expected if verify else None})

        return {"on_ingest": on_ingest, "on_candidates": on_candidates,
                "on_judge": on_judge, "on_findings": on_findings,
                "on_action": on_action}


MANAGER = RunManager()
