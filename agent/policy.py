"""Stage 4 - Policy router.

Pure logic. No LLM call, no I/O. Maps each judged finding onto one of the three
autonomy tiers defined in CLAUDE.md.

The "Never" tier is enforced as a CATEGORY EXCLUSION, not a confidence
threshold. Refunds, deletions and outbound customer email are unreachable no
matter how certain the judge is - there is deliberately no number that unlocks
them, so a mis-calibrated 0.99 cannot escalate into an irreversible action.
"""
from __future__ import annotations

from .models import DiscrepancyType, Finding, JudgeResult, Tier, Candidate

AUTOMATIC_CONFIDENCE = 0.9

# Actions this agent must never perform. Checked by identity, never by score.
NEVER_ACTIONS: frozenset[str] = frozenset({
    "refund",
    "issue_refund",
    "delete",
    "delete_record",
    "delete_company",
    "delete_deal",
    "archive_record",
    "send_email",
    "send_customer_email",
    "email_customer",
})

# Reversible, low-blast-radius actions - eligible for the Automatic tier.
AUTOMATIC_KINDS: frozenset[DiscrepancyType] = frozenset({
    DiscrepancyType.PAID_BUT_OPEN,      # move deal stage + attach a note
    DiscrepancyType.FAILED_PAYMENT,     # create a follow-up task + DRAFT an email
})

# Consequential or ambiguous - always a human in the loop.
APPROVAL_KINDS: frozenset[DiscrepancyType] = frozenset({
    DiscrepancyType.DUPLICATE_COMPANY,      # merging is destructive
    DiscrepancyType.CANCELLED_BUT_ACTIVE,   # declaring churn is a revenue call
    DiscrepancyType.AMOUNT_MISMATCH,        # changing contract value
    DiscrepancyType.NO_CRM_RECORD,          # creating a new CRM company
})


def is_never(action: str) -> bool:
    """True if `action` is permanently out of bounds, regardless of confidence."""
    return action.strip().lower() in NEVER_ACTIONS


def assert_reachable(action: str) -> None:
    """Guard called at the executor boundary before any write."""
    if is_never(action):
        raise PermissionError(
            f"Action '{action}' is in the Never tier and is unreachable by policy. "
            "This is a category exclusion, not a confidence threshold."
        )


def route(candidate: Candidate, judge: JudgeResult) -> Finding:
    """Assign one judged candidate to a tier."""
    # 1. The judge says this isn't a real discrepancy -> do nothing at all.
    if not judge.is_match:
        return Finding(
            candidate=candidate, judge=judge, tier=Tier.NO_ACTION,
            tier_reason=f"judge rejected as a genuine discrepancy (confidence {judge.confidence:.2f})",
        )

    # 2. Automatic: reversible AND confidently a real issue.
    if candidate.kind in AUTOMATIC_KINDS:
        if judge.confidence >= AUTOMATIC_CONFIDENCE:
            return Finding(
                candidate=candidate, judge=judge, tier=Tier.AUTOMATIC,
                tier_reason=(f"reversible action and confidence "
                             f"{judge.confidence:.2f} >= {AUTOMATIC_CONFIDENCE}"),
            )
        return Finding(
            candidate=candidate, judge=judge, tier=Tier.SLACK_APPROVAL,
            tier_reason=(f"reversible but confidence {judge.confidence:.2f} < "
                         f"{AUTOMATIC_CONFIDENCE}; escalated for approval"),
        )

    # 3. Everything else actionable needs a human.
    if candidate.kind in APPROVAL_KINDS:
        return Finding(
            candidate=candidate, judge=judge, tier=Tier.SLACK_APPROVAL,
            tier_reason="consequential change; requires Slack approval regardless of confidence",
        )

    return Finding(
        candidate=candidate, judge=judge, tier=Tier.NO_ACTION,
        tier_reason=f"no policy mapping for kind '{candidate.kind.value}'",
    )


def route_all(judged: list[tuple[Candidate, JudgeResult]]) -> list[Finding]:
    return [route(candidate, verdict) for candidate, verdict in judged]


def summarize(findings: list[Finding]) -> dict[str, int]:
    counts = {t.value: 0 for t in Tier}
    for f in findings:
        counts[f.tier.value] += 1
    return counts
