"""Stage 3 - Judge.

One Antigravity CLI call per candidate, run as a subprocess in non-interactive
print mode. Verified against the installed CLI on 2026-09-13:

    agy -p "<prompt>" --model claude-sonnet-4-6 --output-format json

  * `agy models` reports `claude-sonnet-4-6` == "Claude Sonnet 4.6 (Thinking)",
    so thinking is intrinsic to the model id and cannot silently downgrade.
  * `--output-format json` wraps the reply as
    {"status": "SUCCESS"|"ERROR", "response": "...", "error": "...", "usage": {}}
  * `--json-schema` is deliberately NOT used: it pushed the model towards a
    `read_url` tool call, which headless mode auto-denies, yielding an EMPTY
    response. Prompt-level JSON demands proved strictly more reliable.
  * CLI stdout is UTF-8; decoding must be explicit or em-dashes arrive mangled.

Two failure modes are kept strictly apart, because they call for opposite
responses:

  * a MALFORMED reply is worth one retry with a stricter instruction;
  * a QUOTA error (RESOURCE_EXHAUSTED / 429) must NOT be retried - retrying
    doubles the load on an already-exhausted quota and buys nothing. It aborts
    the batch so the eval can declare the run invalid rather than silently
    scoring a run where most candidates were never judged.

Verdicts are cached on disk by (model, prompt) so re-running an eval over
unchanged candidates costs no quota at all.

Per CLAUDE.md, the CLI's text is converted into a Pydantic `JudgeResult` at the
boundary, before it touches any other code.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import os
import re
import subprocess
import threading
import time

from .config import REPO_ROOT, SETTINGS
from .models import Candidate, DiscrepancyType, JudgeResult

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)
_QUOTA_SIGNS = ("RESOURCE_EXHAUSTED", "429", "quota reached", "Individual quota")

CACHE_DIR = REPO_ROOT / "runs" / "judge_cache"

# Subjects served from cache this process - reported so a run is never
# mistaken for a fully fresh one.
CACHE_HITS: list[str] = []

SHAPE = (
    '{"is_match": <true|false>, "discrepancy_type": "<string>", '
    '"confidence": <number between 0 and 1>, "reasoning": "<one short paragraph>"}'
)

_RULES = """
Answer ONLY from the records supplied below. Do not browse, do not call any
tool, and do not look anything up - you have everything you need here.

Respond with a single raw JSON object and nothing else. No prose before or
after it, no markdown code fences.

Required shape:
""" + SHAPE + """

Field meanings:
  is_match          true only if this is a GENUINE, actionable discrepancy of
                    the stated type that a careful revenue-operations analyst
                    would act on. false if the records are actually consistent.
  discrepancy_type  echo the candidate type, or "none" when is_match is false.
  confidence        your calibrated certainty in the is_match verdict.
  reasoning         why, citing the specific figures or fields that decided it.
"""

_PROMPTS: dict[DiscrepancyType, str] = {
    DiscrepancyType.DUPLICATE_COMPANY: """You are auditing a CRM for duplicate company records.

Two HubSpot company records look similar. Decide whether they are the SAME
real-world company recorded twice (a true duplicate that should be merged), or
TWO GENUINELY DIFFERENT companies that merely have similar names.

Think about this carefully before answering. Similar names are weak evidence on
their own. A shared email domain is strong evidence of the same company, because
domains are globally unique. Different domains are strong evidence of different
companies, even when the brand word matches - many unrelated firms share a
common word, and a parent brand and a separate spin-off are still different
records. Merging two distinct companies is a destructive mistake, so only answer
true when you genuinely believe they are one company.

Candidate: {subject}
{facts}
""",
    DiscrepancyType.PAID_BUT_OPEN: """You are reconciling Stripe billing against a HubSpot CRM pipeline.

The customer appears to have paid, yet their deal is still in an open stage.
Decide whether this is a real "paid but still open" discrepancy that warrants
moving the deal to Closed Won.

Be careful: money that was paid and then fully refunded is NOT a completed sale,
and a $0 trial invoice is NOT a payment. Check the net amount actually retained.

Candidate: {subject}
{facts}
""",
    DiscrepancyType.FAILED_PAYMENT: """You are reviewing Stripe invoices that failed to collect.

Decide whether this customer has a genuinely failed, still-outstanding payment
that warrants a dunning follow-up task and a DRAFT email to the customer.

Guidance on invoice state: an invoice that is still `open` with a non-zero
balance and one or more failed collection attempts is exactly the case that
needs dunning. A `$0` invoice is not a failed payment. An invoice marked
`uncollectible` has already been written off, so it is NOT a dunning candidate.

Candidate: {subject}
{facts}
""",
    DiscrepancyType.CANCELLED_BUT_ACTIVE: """You are reconciling Stripe subscription status against CRM lifecycle stage.

The Stripe subscription is cancelled, but the CRM still records the company as
an active customer. Decide whether this company has genuinely churned and the
CRM lifecycle stage is therefore stale.

Be careful: if the customer still holds any active or trialing subscription,
they have not churned.

Candidate: {subject}
{facts}
""",
    DiscrepancyType.AMOUNT_MISMATCH: """You are reconciling contract value between Stripe and a HubSpot deal.

Decide whether the CRM deal amount genuinely disagrees with what Stripe actually
bills, once both are expressed over the same period.

Be careful with billing frequency: a monthly Stripe subscription must be
multiplied by 12 before comparing against an annual CRM figure. If the two agree
once normalised, there is NO discrepancy and you must answer false.

Candidate: {subject}
{facts}
""",
    DiscrepancyType.NO_CRM_RECORD: """You are checking whether every paying Stripe customer exists in the CRM.

This Stripe customer had no matching HubSpot company. Decide whether a CRM
company record genuinely ought to be created for them.

Candidate: {subject}
{facts}
""",
}


def build_prompt(candidate: Candidate) -> str:
    template = _PROMPTS.get(candidate.kind, _PROMPTS[DiscrepancyType.NO_CRM_RECORD])
    body = template.format(subject=candidate.subject, facts=candidate.fact_lines())
    return f"{body}\n{_RULES}\nThe candidate type under test is: {candidate.kind.value}\n"


class JudgeFailure(Exception):
    """A candidate could not be judged. `quota` distinguishes an exhausted
    upstream from a model that simply replied badly."""

    def __init__(self, candidate: Candidate, detail: str, quota: bool = False):
        super().__init__(detail)
        self.candidate = candidate
        self.detail = detail
        self.quota = quota


class QuotaExhausted(RuntimeError):
    """Raised as soon as the CLI reports RESOURCE_EXHAUSTED / 429."""


# --------------------------------------------------------------------------
# Verdict cache - keyed by (model, prompt), so reruns cost no quota
# --------------------------------------------------------------------------
def _cache_enabled() -> bool:
    return os.getenv("JUDGE_CACHE", "1").strip().lower() in {"1", "true", "yes", "on"}


_VOLATILE = re.compile(
    r"\b(?:cus|in|sub|si|price|prod|re|pi|ch|ii)_[A-Za-z0-9]+\b|\b\d{6,}\b")


def semantic_key(kind: str, subject: str, facts: dict) -> str:
    """A cache key that survives reseeding.

    Candidate facts embed freshly generated Stripe/HubSpot object ids, so keying
    on the raw prompt would miss on every new seed even though the question is
    identical. Volatile identifiers are scrubbed, leaving the semantic content:
    which scenario, of which type, with which figures.
    """
    scrubbed = {k: _VOLATILE.sub("<id>", str(v)) for k, v in sorted(facts.items())}
    payload = json.dumps({"model": SETTINGS.agy_model, "kind": kind,
                          "subject": subject, "facts": scrubbed}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _cache_path(candidate: Candidate):
    return CACHE_DIR / f"{semantic_key(candidate.kind.value, candidate.subject, candidate.facts)}.json"


def _cache_get(candidate: Candidate) -> JudgeResult | None:
    if not _cache_enabled():
        return None
    path = _cache_path(candidate)
    if not path.exists():
        return None
    try:
        return JudgeResult.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cache_put(candidate: Candidate, verdict: JudgeResult) -> None:
    if not _cache_enabled():
        return
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(candidate).write_text(verdict.model_dump_json(), encoding="utf-8")
    except OSError:
        pass


def cache_warm(kind: str, subject: str, facts: dict, verdict: JudgeResult) -> None:
    """Insert a verdict recorded by an earlier run (replayed from a trace)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{semantic_key(kind, subject, facts)}.json").write_text(
        verdict.model_dump_json(), encoding="utf-8")


# --------------------------------------------------------------------------
# CLI invocation
# --------------------------------------------------------------------------
def _looks_like_quota(text: str) -> bool:
    return any(sign.lower() in text.lower() for sign in _QUOTA_SIGNS)


def _run_cli(prompt: str, timeout: int) -> str:
    """Invoke the CLI once and return the model's text."""
    proc = subprocess.run(
        [SETTINGS.agy_bin, "-p", prompt,
         "--model", SETTINGS.agy_model,
         "--output-format", "json",
         "--print-timeout", f"{timeout}s"],
        capture_output=True,
        timeout=timeout + 60,
        encoding="utf-8",       # CLI emits UTF-8; without this em-dashes mangle
        errors="replace",
    )
    raw = (proc.stdout or "").strip()
    if not raw:
        detail = (proc.stderr or "")[:300]
        if _looks_like_quota(detail):
            raise QuotaExhausted(detail)
        raise ValueError(f"empty stdout (exit {proc.returncode}): {detail}")

    # Unwrap the --output-format json envelope when present.
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return raw                      # plain-text output mode

    if isinstance(envelope, dict) and ("response" in envelope or "error" in envelope):
        err = (envelope.get("error") or "").strip()
        if envelope.get("status") not in (None, "SUCCESS"):
            # Surface the CLI's own message - a bare "status=ERROR" is useless.
            if _looks_like_quota(err):
                raise QuotaExhausted(err)
            raise ValueError(f"CLI status={envelope.get('status')}: {err[:240]}")
        text = (envelope.get("response") or "").strip()
        if not text:
            denied = envelope.get("denied_actions")
            if _looks_like_quota(err):
                raise QuotaExhausted(err)
            raise ValueError(f"empty response (denied_actions={denied}) {err[:160]}")
        return text
    return raw


def _parse(text: str) -> JudgeResult:
    cleaned = _FENCE.sub("", text).strip()
    try:
        return JudgeResult.model_validate_json(cleaned)
    except Exception:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        return JudgeResult.model_validate(json.loads(cleaned[start:end + 1]))
    raise ValueError(f"no JSON object found in: {cleaned[:200]}")


def judge_candidate(candidate: Candidate, timeout: int | None = None) -> JudgeResult:
    """One candidate -> one JudgeResult.

    Retries once on a malformed reply. Never retries a quota error.
    """
    timeout = timeout or SETTINGS.judge_timeout
    base_prompt = build_prompt(candidate)

    cached = _cache_get(candidate)
    if cached is not None:
        CACHE_HITS.append(candidate.subject)
        return cached

    prompt = base_prompt
    first_error = ""

    for attempt in (1, 2):
        if attempt == 2:
            prompt = ("Your last response wasn't valid JSON, return ONLY the JSON object, "
                      "no other text.\n\n" + base_prompt)
        try:
            verdict = _parse(_run_cli(prompt, timeout))
            _cache_put(candidate, verdict)
            return verdict
        except QuotaExhausted as exc:
            raise JudgeFailure(candidate, f"upstream quota exhausted: {exc}", quota=True) from exc
        except subprocess.TimeoutExpired:
            first_error = first_error or f"timeout after {timeout}s"
        except Exception as exc:
            first_error = first_error or f"{type(exc).__name__}: {exc}"

    raise JudgeFailure(candidate, first_error)


def judge_all(candidates: list[Candidate], concurrency: int | None = None, on_result=None):
    """Judge every candidate in parallel.

    Returns (results, failures, seconds). A malformed candidate is skipped and
    recorded. The first quota error stops further submissions: continuing would
    hammer an exhausted quota and produce a run whose metrics mean nothing.
    """
    workers = concurrency or SETTINGS.judge_concurrency
    results: list[tuple[Candidate, JudgeResult]] = []
    failures: list[JudgeFailure] = []
    started = time.time()
    quota_hit = threading.Event()

    def work(candidate: Candidate) -> JudgeResult:
        if quota_hit.is_set():
            raise JudgeFailure(candidate, "skipped: upstream quota already exhausted", quota=True)
        try:
            return judge_candidate(candidate)
        except JudgeFailure as failure:
            if failure.quota:
                quota_hit.set()
            raise

    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, c): c for c in candidates}
        for fut in cf.as_completed(futures):
            candidate = futures[fut]
            try:
                verdict = fut.result()
                results.append((candidate, verdict))
                if on_result:
                    on_result(candidate, verdict, None)
            except JudgeFailure as failure:
                failures.append(failure)
                if on_result:
                    on_result(candidate, None, failure)
            except Exception as exc:
                failure = JudgeFailure(candidate, f"{type(exc).__name__}: {exc}")
                failures.append(failure)
                if on_result:
                    on_result(candidate, None, failure)

    results.sort(key=lambda pair: (pair[0].kind.value, pair[0].subject))
    return results, failures, time.time() - started


def quota_failures(failures: list[JudgeFailure]) -> int:
    return sum(1 for f in failures if getattr(f, "quota", False))
