"""Stage 2 - Candidate generation.

Pure, deterministic, and fast. This stage NEVER calls an LLM (CLAUDE.md hard
rule); it only proposes pairs worth reasoning about so the judge sees a short,
high-signal list.

Two distinct families of candidate live here:

  * Identity candidates (duplicate_company) use rapidfuzz. Note that after
    normalising legal suffixes, "Acme Inc" vs "Acme Labs" scores 100 - exactly
    the same as the genuine duplicates. That is deliberate: deterministic code
    cannot tell those apart, which is precisely what the judge is for.

  * State candidates (paid_but_open, failed_payment, cancelled_but_active,
    amount_mismatch, no_crm_record) are direct field comparisons. Where the
    facts are unambiguous the filter is tight, so decoys whose numbers simply
    agree (Redwood) or net to zero (Northwind, Sable) never become candidates.
"""
from __future__ import annotations

import re
from itertools import combinations

from rapidfuzz import fuzz

from .ingest import Snapshot
from .models import Candidate, DiscrepancyType, HubSpotCompany, StripeCustomer

NAME_THRESHOLD = 90          # see module docstring; nearest non-pair scores 61.5
AMOUNT_TOLERANCE = 0.01      # 1% - guards float/rounding noise, not real gaps

_SUFFIXES = r"\b(inc|ltd|llc|co|corp|company|limited|and|the)\b"


def normalize_name(name: str | None) -> str:
    text = (name or "").lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(_SUFFIXES, " ", text)
    return " ".join(text.split())


def name_similarity(a: str | None, b: str | None) -> float:
    return fuzz.token_set_ratio(normalize_name(a), normalize_name(b))


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


class Matcher:
    """Resolves a Stripe customer to a HubSpot company."""

    def __init__(self, companies: list[HubSpotCompany]):
        self.companies = companies
        self.by_domain: dict[str, list[HubSpotCompany]] = {}
        for c in companies:
            if c.domain_norm:
                self.by_domain.setdefault(c.domain_norm, []).append(c)

    def match(self, customer: StripeCustomer) -> HubSpotCompany | None:
        if customer.domain and customer.domain in self.by_domain:
            return self.by_domain[customer.domain][0]
        best, best_score = None, 0.0
        for c in self.companies:
            score = name_similarity(customer.name, c.name)
            if score > best_score:
                best, best_score = c, score
        return best if best_score >= NAME_THRESHOLD else None


def build_candidates(snapshot: Snapshot) -> list[Candidate]:
    matcher = Matcher(snapshot.companies)
    out: list[Candidate] = []

    deals_by_company: dict[str, list] = {}
    for deal in snapshot.deals:
        for cid in deal.company_ids:
            deals_by_company.setdefault(cid, []).append(deal)

    subs_by_customer: dict[str, list] = {}
    for sub in snapshot.subscriptions:
        subs_by_customer.setdefault(sub.customer or "", []).append(sub)

    # ---------------------------------------------------------------- 1..6
    for cust in snapshot.customers:
        company = matcher.match(cust)
        subject = cust.name or cust.id
        deals = deals_by_company.get(company.id, []) if company else []
        open_deals = [d for d in deals if not d.is_closed]
        subs = subs_by_customer.get(cust.id, [])

        # --- Category 6: Stripe customer with no CRM record ---
        if company is None:
            out.append(Candidate(
                key=f"no_crm:{cust.id}", kind=DiscrepancyType.NO_CRM_RECORD, subject=subject,
                stripe_ref=cust.id,
                facts={
                    "stripe_customer": f"{cust.name} <{cust.email}>",
                    "stripe_subscriptions": ", ".join(
                        f"{s.status} {_money(s.amount)}/{s.interval}" for s in subs) or "none",
                    "hubspot_company_search": (
                        f"no company matched domain '{cust.domain}' or name '{cust.name}' "
                        f"at >= {NAME_THRESHOLD}% similarity"),
                }))
            continue

        # --- Category 1: paid in Stripe, deal still open ---
        net_paid = snapshot.net_paid_cents(cust.id)
        if net_paid > 0 and open_deals:
            deal = open_deals[0]
            refunded = snapshot.refunded_cents(cust.id)
            out.append(Candidate(
                key=f"paid_open:{deal.id}", kind=DiscrepancyType.PAID_BUT_OPEN, subject=subject,
                stripe_ref=cust.id, hubspot_ref=deal.id,
                facts={
                    "stripe_net_paid": _money(net_paid),
                    "stripe_refunded": _money(refunded),
                    "hubspot_deal": deal.dealname,
                    "hubspot_deal_stage": deal.dealstage,
                    "hubspot_deal_amount": _money(deal.amount_cents),
                }))

        # --- Category 3: failed payment with no follow-up ---
        for inv in snapshot.invoices:
            if inv.customer == cust.id and inv.is_failed:
                out.append(Candidate(
                    key=f"failed_pay:{inv.id}", kind=DiscrepancyType.FAILED_PAYMENT, subject=subject,
                    stripe_ref=inv.id, hubspot_ref=company.id,
                    facts={
                        "stripe_invoice": inv.id,
                        "stripe_invoice_status": inv.status,
                        "failed_collection_attempts": inv.attempt_count,
                        "written_off": inv.status == "uncollectible",
                        "amount_outstanding": _money(inv.amount_due),
                        "customer_email": cust.email,
                        "hubspot_company": company.name,
                        "hubspot_open_deals": len(open_deals),
                    }))

        # --- Category 4: cancelled in Stripe, still a customer in CRM ---
        cancelled = [s for s in subs if s.status == "canceled"]
        active = [s for s in subs if s.status in {"active", "trialing", "past_due"}]
        if cancelled and not active and (company.lifecyclestage or "").lower() == "customer":
            out.append(Candidate(
                key=f"churn:{company.id}", kind=DiscrepancyType.CANCELLED_BUT_ACTIVE, subject=subject,
                stripe_ref=cancelled[0].id, hubspot_ref=company.id,
                facts={
                    "stripe_subscription_status": "canceled",
                    "stripe_cancelled_value": f"{_money(cancelled[0].amount)}/{cancelled[0].interval}",
                    "stripe_active_subscriptions": 0,
                    "hubspot_company": company.name,
                    "hubspot_lifecyclestage": company.lifecyclestage,
                }))

        # --- Category 5: amount mismatch (normalised to an annual figure) ---
        for sub in active:
            annual = sub.annualized_cents
            for deal in deals:
                crm = deal.amount_cents
                if crm <= 0 or annual <= 0:
                    continue
                if abs(crm - annual) / max(crm, annual) > AMOUNT_TOLERANCE:
                    out.append(Candidate(
                        key=f"amount:{deal.id}", kind=DiscrepancyType.AMOUNT_MISMATCH, subject=subject,
                        stripe_ref=sub.id, hubspot_ref=deal.id,
                        facts={
                            "stripe_subscription": f"{_money(sub.amount)}/{sub.interval}",
                            "stripe_annualized": _money(annual),
                            "hubspot_deal": deal.dealname,
                            "hubspot_deal_amount": _money(crm),
                            "difference": _money(abs(crm - annual)),
                        }))

    # ---------------------------------------------------------------- 2
    for a, b in combinations(snapshot.companies, 2):
        same_domain = bool(a.domain_norm) and a.domain_norm == b.domain_norm
        score = name_similarity(a.name, b.name)
        if not (same_domain or score >= NAME_THRESHOLD):
            continue
        out.append(Candidate(
            key=f"dup:{a.id}:{b.id}", kind=DiscrepancyType.DUPLICATE_COMPANY,
            subject=a.name or a.id,
            hubspot_ref=f"{a.id},{b.id}",
            facts={
                "company_a": f"{a.name} (domain: {a.domain or 'none'}, id {a.id})",
                "company_b": f"{b.name} (domain: {b.domain or 'none'}, id {b.id})",
                "same_domain": same_domain,
                "name_similarity": f"{score:.1f}% after stripping legal suffixes",
            }))

    return out


def summarize(candidates: list[Candidate]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in candidates:
        counts[c.kind.value] = counts.get(c.kind.value, 0) + 1
    return counts
