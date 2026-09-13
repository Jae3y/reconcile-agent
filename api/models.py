"""Phase 10 - API response models.

Every endpoint returns one of these. The dashboard's TypeScript client mirrors
this file exactly, so a change here is a change to the frontend contract.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

RunKind = Literal["run", "eval"]
RunStatus = Literal["queued", "running", "succeeded", "failed"]
StageName = Literal["ingest", "candidates", "judge", "policy", "execute"]
StageStatus = Literal["pending", "active", "done", "error"]


class Health(BaseModel):
    ok: bool = True
    service: str = "reconcile-agent-api"
    version: str = "1.0.0"


class Integration(BaseModel):
    """One live reachability probe. `ok` means verified reachable right now."""

    name: str
    label: str
    ok: bool
    mode: Literal["real", "twin", "external"] = "twin"
    status: Literal["online", "degraded", "offline", "unconfigured"]
    detail: str = ""
    endpoint: str = ""
    latency_ms: float | None = None
    checked_at: str


class IntegrationsResponse(BaseModel):
    backend: str = Field(description="Which data backend the probes ran against")
    simulated: bool = Field(description="True when ANY integration is still simulated")
    real_count: int = 0
    total_count: int = 0
    integrations: list[Integration]
    checked_at: str


class Scenario(BaseModel):
    n: int
    name: str
    category: str
    expected_tier: str
    expected_type: str
    decoy: bool
    why: str = ""


class StageState(BaseModel):
    name: StageName
    status: StageStatus = "pending"
    detail: str = ""
    count: int | None = None
    seconds: float | None = None


class RunEvent(BaseModel):
    """One SSE frame. `seq` lets a late subscriber replay from the start."""

    seq: int
    run_id: str
    at: str
    type: str
    stage: StageName | None = None
    level: Literal["info", "success", "warning", "error"] = "info"
    title: str
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class JudgedItem(BaseModel):
    subject: str
    discrepancy_type: str
    is_match: bool
    confidence: float
    reasoning: str
    cached: bool = False


class ActionItem(BaseModel):
    subject: str
    discrepancy_type: str
    tier: str
    status: str
    description: str = ""
    detail: str = ""
    attempts: int = 0
    verified: bool | None = None
    verify_field: str = ""
    verify_expected: Any = None
    verify_observed: Any = None


class Metrics(BaseModel):
    valid: bool
    invalid_reason: str = ""
    recall: float = 0.0
    precision: float = 0.0
    f1: float = 0.0
    decoy_false_action_rate: str = "0/5"
    decoys_acted: int = 0
    decoy_total: int = 5
    tier_accuracy: float = 0.0
    post_state_correctness: float = 0.0
    detected_real: int = 0
    real_total: int = 20
    missed: list[str] = Field(default_factory=list)
    judge_failures: int = 0
    quota_failures: int = 0
    cached_verdicts: int = 0
    fresh_verdicts: int = 0
    injected_subject: str | None = None
    injected_caught: bool = False
    verified_ok: int = 0


class RunSummary(BaseModel):
    run_id: str
    kind: RunKind
    status: RunStatus
    started_at: str
    finished_at: str | None = None
    duration_seconds: float | None = None
    trace_id: str = ""
    error: str = ""
    candidates: int = 0
    applied: int = 0
    verify_failed: int = 0
    skipped: int = 0
    no_action: int = 0
    metrics: Metrics | None = None


class RunDetail(RunSummary):
    stages: list[StageState] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    judged: list[JudgedItem] = Field(default_factory=list)
    actions: list[ActionItem] = Field(default_factory=list)
    events: list[RunEvent] = Field(default_factory=list)


class StartRunResponse(BaseModel):
    run_id: str
    kind: RunKind
    status: RunStatus
    stream: str = Field(description="SSE URL for live phase-by-phase updates")


class SeedResponse(BaseModel):
    ok: bool
    seeded: int
    errors: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    lines: list[str] = Field(default_factory=list)
