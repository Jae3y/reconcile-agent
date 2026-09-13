"""Pydantic models for every object that crosses a stage boundary.

CLAUDE.md hard rule: the judge ALWAYS returns a Pydantic model, never raw text
parsed with loose string matching. `JudgeResult` is that boundary type.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------
# Stripe
# --------------------------------------------------------------------------
class StripeCustomer(BaseModel):
    id: str
    name: str | None = None
    email: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def domain(self) -> str:
        return self.email.split("@")[-1].lower() if self.email and "@" in self.email else ""


class StripeInvoice(BaseModel):
    id: str
    customer: str | None = None
    status: str | None = None          # draft | open | paid | uncollectible | void
    amount_due: int = 0                # cents
    amount_paid: int = 0               # cents
    currency: str = "usd"
    created: int | None = None
    attempt_count: int = 0
    paid: bool = False

    @property
    def is_paid(self) -> bool:
        return self.status == "paid" or self.paid

    @property
    def is_failed(self) -> bool:
        """Dunning-worthy. Note: the Arga twin leaves attempt_count at 0, so
        'uncollectible' is the authoritative failed-payment signal; a retried
        open invoice also counts when the real API does populate attempts."""
        if self.amount_due <= 0:
            return False
        return self.status == "uncollectible" or (self.status == "open" and self.attempt_count > 0)


class StripeRefund(BaseModel):
    id: str
    payment_intent: str | None = None
    charge: str | None = None
    amount: int = 0                    # cents
    status: str | None = None
    customer: str | None = None        # resolved during ingest


class StripeSubscription(BaseModel):
    id: str
    customer: str | None = None
    status: str | None = None          # active | canceled | past_due | trialing | unpaid
    amount: int = 0                    # cents per interval
    interval: str = "month"            # month | year
    currency: str = "usd"

    @property
    def annualized_cents(self) -> int:
        if self.interval == "year":
            return self.amount
        if self.interval == "week":
            return self.amount * 52
        return self.amount * 12


# --------------------------------------------------------------------------
# HubSpot
# --------------------------------------------------------------------------
class HubSpotCompany(BaseModel):
    id: str
    name: str | None = None
    domain: str | None = None
    lifecyclestage: str | None = None
    stripe_customer_id: str | None = None

    @property
    def domain_norm(self) -> str:
        return (self.domain or "").lower().strip()


class HubSpotDeal(BaseModel):
    id: str
    dealname: str | None = None
    amount: float = 0.0                # dollars, as HubSpot stores it
    dealstage: str | None = None
    pipeline: str = "default"
    company_ids: list[str] = Field(default_factory=list)

    @property
    def is_closed(self) -> bool:
        return (self.dealstage or "") in {"closedwon", "closedlost"}

    @property
    def is_won(self) -> bool:
        return self.dealstage == "closedwon"

    @property
    def amount_cents(self) -> int:
        return int(round(self.amount * 100))


# --------------------------------------------------------------------------
# Pipeline types
# --------------------------------------------------------------------------
class DiscrepancyType(str, Enum):
    PAID_BUT_OPEN = "paid_but_open"
    DUPLICATE_COMPANY = "duplicate_company"
    FAILED_PAYMENT = "failed_payment"
    CANCELLED_BUT_ACTIVE = "cancelled_but_active"
    AMOUNT_MISMATCH = "amount_mismatch"
    NO_CRM_RECORD = "no_crm_record"
    NONE = "none"


class Tier(str, Enum):
    AUTOMATIC = "automatic"
    SLACK_APPROVAL = "slack_approval"
    NEVER = "never"
    NO_ACTION = "no_action"


class Candidate(BaseModel):
    """A pair/record worth judging. Produced deterministically, never by an LLM."""

    key: str
    kind: DiscrepancyType
    subject: str                       # human label, e.g. "Ferro Steel"
    stripe_ref: str | None = None
    hubspot_ref: str | None = None
    facts: dict[str, Any] = Field(default_factory=dict)

    def fact_lines(self) -> str:
        return "\n".join(f"  - {k}: {v}" for k, v in self.facts.items())


class JudgeResult(BaseModel):
    """The exact shape mandated by CLAUDE.md."""

    is_match: bool
    discrepancy_type: str
    confidence: float
    reasoning: str

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v: Any) -> float:
        """The CLI sometimes emits '0.95', 95, or '95%'. Normalise to 0..1."""
        if isinstance(v, str):
            v = v.strip().rstrip("%")
            v = float(v) / 100 if float(v) > 1 else float(v)
        v = float(v)
        if v > 1.0:
            v = v / 100.0
        return max(0.0, min(1.0, v))

    @field_validator("reasoning", mode="before")
    @classmethod
    def _stringify_reasoning(cls, v: Any) -> str:
        return v if isinstance(v, str) else str(v)


class Finding(BaseModel):
    candidate: Candidate
    judge: JudgeResult
    tier: Tier = Tier.NO_ACTION
    tier_reason: str = ""


class VerifyResult(BaseModel):
    ok: bool
    field: str
    expected: Any = None
    observed: Any = None
    detail: str = ""


class ActionResult(BaseModel):
    subject: str
    kind: DiscrepancyType
    tier: Tier
    status: Literal[
        "applied", "verify_failed", "skipped", "denied", "timeout", "error", "no_action", "drafted"
    ]
    description: str = ""
    verify: VerifyResult | None = None
    attempts: int = 0
    detail: str = ""
