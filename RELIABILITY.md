# reconcile-agent — reliability brief

_Generated 2026-09-13 21:52 UTC from a freshly seeded twin (25 planted scenarios: 20 real discrepancies, 5 decoys)._


| Metric | Result | Target |
| --- | --- | --- |
| Detection recall | 100% (20/20) | 100% |
| Detection precision | 100% | 100% |
| F1 | 1.00 | 1.00 |
| Decoy false-action rate | 0/5 | 0/5 |
| Tier routing accuracy | 100% | 100% |
| Post-state correctness | 100% (19 confirmed by read-back) | 100% |
| Judge failures | 0 | 0 |
| Injected silent failure caught | yes | yes |

## How the numbers are produced

Every write is followed by an independent re-read of the record through `agent/verify.py`; an action is only counted as applied once the twin confirms the new value. Post-state correctness is that read-back, not the write's own return value.

A silent failure was deliberately injected for **Kestrel Foods** — the write returns success without changing anything. The read-back caught it, the executor retried once, and it was then recorded as a failed action rather than reported as applied.

## Known limitations

- **Judgement is probabilistic.** The duplicate-company decoy (Acme Inc vs Acme Labs) is indistinguishable from a true duplicate by fuzzy matching alone — both score 100% after normalising legal suffixes — so correctness there rests entirely on the LLM judge weighing domain evidence. It is right consistently in testing, but it is not a guarantee, which is why every destructive category stays behind Slack approval.
- **Approvals are simulated in eval.** Unattended runs auto-approve Slack-tier actions so the harness can score post-state correctness. A human reacting with ✅/❌ is the real path (`--approval poll`); the eval therefore measures detection and execution quality, not human agreement rates.
- **Merges and refunds are never executed.** Duplicates are *flagged* rather than merged, because merging deletes a record; refunds, deletions and outbound customer email are excluded by category in `agent/policy.py` and unreachable at any confidence.
- **Judge latency dominates.** The Antigravity CLI takes ~45–50s per candidate; this run spent 500s in the judge stage across 21 candidates at 6-way concurrency.
