"""Lemma tracing (uselemma-tracing).

Every pipeline run becomes one Lemma trace; each stage becomes a span. Judge
calls are recorded as generations so the model, prompt and verdict are visible,
and executor writes are recorded as tools so a verify failure is legible in the
trace view rather than buried in stdout.

Traces are always mirrored to runs/trace-<id>.jsonl. That keeps the run
inspectable when LEMMA_API_KEY is absent or the network is down - tracing must
never be the reason a reconciliation run fails.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .config import REPO_ROOT, SETTINGS

RUNS_DIR = REPO_ROOT / "runs"


class RunTrace:
    """Thin façade over a Lemma trace handle with a local JSONL mirror."""

    def __init__(self, handle: Any, path: Path, run_id: str):
        self._handle = handle
        self._path = path
        self.run_id = run_id
        self.spans = 0

    # -- internal ------------------------------------------------------
    def _mirror(self, kind: str, payload: dict) -> None:
        record = {"ts": time.time(), "run_id": self.run_id, "kind": kind, **payload}
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")

    def _safe(self, method: str, **kwargs) -> None:
        if self._handle is None:
            return
        fn = getattr(self._handle, method, None)
        if fn is None:
            return
        try:
            fn(**kwargs)
        except Exception:
            # Observability must never break the pipeline.
            pass

    # -- public --------------------------------------------------------
    def span(self, name: str, *, input: Any = None, output: Any = None,
             metadata: dict | None = None) -> None:
        self.spans += 1
        self._mirror("span", {"name": name, "input": input, "output": output,
                              "metadata": metadata or {}})
        self._safe("span", name=name, input=input, output=output, metadata=metadata or {})

    def generation(self, name: str, *, input: Any = None, output: Any = None,
                   model: str | None = None, metadata: dict | None = None) -> None:
        self.spans += 1
        self._mirror("generation", {"name": name, "input": input, "output": output,
                                    "model": model, "metadata": metadata or {}})
        self._safe("generation", name=name, input=input, output=output,
                   model=model or SETTINGS.agy_model, metadata=metadata or {})

    def tool(self, name: str, *, input: Any = None, output: Any = None,
             metadata: dict | None = None) -> None:
        self.spans += 1
        self._mirror("tool", {"name": name, "input": input, "output": output,
                              "metadata": metadata or {}})
        self._safe("tool", name=name, input=input, output=output, metadata=metadata or {})

    def event(self, message: str, **fields) -> None:
        self._mirror("event", {"message": message, **fields})


class Tracer:
    def __init__(self) -> None:
        self.enabled = False
        self._lemma = None
        RUNS_DIR.mkdir(exist_ok=True)
        if SETTINGS.lemma_api_key and SETTINGS.lemma_project_id:
            try:
                from uselemma_tracing import Lemma

                self._lemma = Lemma(api_key=SETTINGS.lemma_api_key,
                                    project_id=SETTINGS.lemma_project_id)
                self.enabled = True
            except Exception:
                self._lemma = None

    @property
    def status(self) -> str:
        return ("Lemma project " + SETTINGS.lemma_project_id[:8] + "…"
                if self.enabled else "local JSONL only (LEMMA_API_KEY unset)")

    def run(self, name: str, fn, *, metadata: dict | None = None):
        """Execute fn(trace) inside one Lemma trace. Returns fn's result."""
        run_id = uuid.uuid4().hex[:12]
        path = RUNS_DIR / f"trace-{run_id}.jsonl"

        if self._lemma is None:
            trace = RunTrace(None, path, run_id)
            trace.event("trace-start", name=name, backend="local")
            return fn(trace), trace

        holder: dict[str, Any] = {}

        def body(ctx):
            handle = ctx if hasattr(ctx, "span") else getattr(ctx, "trace", None)
            trace = RunTrace(handle, path, run_id)
            trace.event("trace-start", name=name, backend="lemma")
            holder["trace"] = trace
            return fn(trace)

        try:
            result = self._lemma.trace(name, body, metadata=metadata or {})
            return result, holder.get("trace")
        except Exception:
            # Lemma unavailable mid-run - finish the work on the local mirror.
            trace = holder.get("trace") or RunTrace(None, path, run_id)
            trace.event("lemma-unavailable", name=name)
            if "trace" not in holder:
                return fn(trace), trace
            raise


TRACER = Tracer()
