"""Replay judge verdicts from an earlier run's trace into the verdict cache.

Why this exists: the Antigravity CLI enforces an individual daily quota. When a
run is interrupted part-way, the verdicts it *did* produce are still recorded in
runs/trace-*.jsonl. Replaying them means a re-run only spends quota on the
candidates that actually changed.

Keys are semantic (see agent.judge.semantic_key), so a verdict recorded against
one seeded twin still applies after reseeding, as long as the underlying facts
are the same. Candidates whose facts changed simply miss and get re-judged.

    python -m eval.warm_cache [trace.jsonl ...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from agent.config import REPO_ROOT
from agent.judge import cache_warm
from agent.models import JudgeResult


def warm_from(path: Path) -> tuple[int, int]:
    warmed = skipped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("kind") != "generation":
            continue
        name = record.get("name") or ""
        if not name.startswith("judge:"):
            continue
        kind = name.split(":", 1)[1]
        payload = record.get("input") or {}
        output = record.get("output") or {}
        try:
            verdict = JudgeResult.model_validate(output)
        except Exception:
            skipped += 1
            continue
        cache_warm(kind, payload.get("subject", ""), payload.get("facts") or {}, verdict)
        warmed += 1
    return warmed, skipped


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] or sorted(
        (REPO_ROOT / "runs").glob("trace-*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not paths:
        print("no trace files found")
        return 1
    total = 0
    for path in paths:
        warmed, skipped = warm_from(path)
        total += warmed
        print(f"  {path.name}: warmed {warmed}, skipped {skipped}")
    print(f"cached {total} verdict(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
