"""Stage 1 — Ingest.

Pull Stripe customers/invoices/subscriptions and HubSpot companies/deals from
the twins and return validated Pydantic models. No judgement happens here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .clients import HubSpotTwin, stripe_client
from .config import TwinSession
from .models import (
    HubSpotCompany,
    HubSpotDeal,
    StripeCustomer,
    StripeInvoice,
    StripeRefund,
    StripeSubscription,
)


@dataclass
class Snapshot:
    customers: list[StripeCustomer] = field(default_factory=list)
    invoices: list[StripeInvoice] = field(default_factory=list)
    subscriptions: list[StripeSubscription] = field(default_factory=list)
    refunds: list[StripeRefund] = field(default_factory=list)
    companies: list[HubSpotCompany] = field(default_factory=list)
    deals: list[HubSpotDeal] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "stripe.customers": len(self.customers),
            "stripe.invoices": len(self.invoices),
            "stripe.subscriptions": len(self.subscriptions),
            "stripe.refunds": len(self.refunds),
            "hubspot.companies": len(self.companies),
            "hubspot.deals": len(self.deals),
        }

    def customer_by_id(self, cid: str) -> StripeCustomer | None:
        return next((c for c in self.customers if c.id == cid), None)

    def refunded_cents(self, customer_id: str) -> int:
        return sum(r.amount for r in self.refunds
                   if r.customer == customer_id and r.status == "succeeded")

    def net_paid_cents(self, customer_id: str) -> int:
        """Paid minus refunded. Guards the 'paid then fully refunded' decoy."""
        paid = sum(i.amount_paid for i in self.invoices
                   if i.customer == customer_id and i.is_paid)
        return paid - self.refunded_cents(customer_id)


def _as_dict(value) -> dict:
    """stripe-python returns StripeObject, which is not a mapping."""
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    return dict(value)


def _amount_and_interval(sub) -> tuple[int, str]:
    """Pull per-interval amount out of a subscription's first item."""
    items = getattr(sub, "items", None)
    data = getattr(items, "data", None) or (items or {}).get("data", []) if items else []
    if not data:
        return 0, "month"
    price = getattr(data[0], "price", None) or {}
    unit = getattr(price, "unit_amount", None)
    if unit is None and isinstance(price, dict):
        unit = price.get("unit_amount")
    recurring = getattr(price, "recurring", None) or (price.get("recurring") if isinstance(price, dict) else None) or {}
    interval = getattr(recurring, "interval", None) or (recurring.get("interval") if isinstance(recurring, dict) else None)
    qty = getattr(data[0], "quantity", 1) or 1
    return int(unit or 0) * int(qty), (interval or "month")


def ingest_stripe(session: TwinSession):
    client = stripe_client(session)

    customers = [
        StripeCustomer(id=c.id, name=c.name, email=c.email, metadata=_as_dict(c.metadata))
        for c in client.v1.customers.list({"limit": 100}).auto_paging_iter()
    ]

    invoices = [
        StripeInvoice(
            id=i.id,
            customer=i.customer if isinstance(i.customer, str) else getattr(i.customer, "id", None),
            status=i.status,
            amount_due=int(i.amount_due or 0),
            amount_paid=int(i.amount_paid or 0),
            currency=i.currency or "usd",
            created=i.created,
            attempt_count=int(getattr(i, "attempt_count", 0) or 0),
            paid=bool(getattr(i, "paid", False)),
        )
        for i in client.v1.invoices.list({"limit": 100}).auto_paging_iter()
    ]

    subscriptions = []
    for s in client.v1.subscriptions.list({"limit": 100, "status": "all"}).auto_paging_iter():
        amount, interval = _amount_and_interval(s)
        subscriptions.append(
            StripeSubscription(
                id=s.id,
                customer=s.customer if isinstance(s.customer, str) else getattr(s.customer, "id", None),
                status=s.status,
                amount=amount,
                interval=interval,
                currency=getattr(s, "currency", "usd") or "usd",
            )
        )
    # Refunds -> resolve back to a customer via their payment intent.
    pi_customer: dict[str, str] = {}
    for pi in client.v1.payment_intents.list({"limit": 100}).auto_paging_iter():
        cust = pi.customer if isinstance(pi.customer, str) else getattr(pi.customer, "id", None)
        if cust:
            pi_customer[pi.id] = cust

    refunds = []
    for r in client.v1.refunds.list({"limit": 100}).auto_paging_iter():
        pi_id = r.payment_intent if isinstance(r.payment_intent, str) else getattr(r.payment_intent, "id", None)
        refunds.append(
            StripeRefund(
                id=r.id,
                payment_intent=pi_id,
                charge=r.charge if isinstance(r.charge, str) else getattr(r.charge, "id", None),
                amount=int(r.amount or 0),
                status=r.status,
                customer=pi_customer.get(pi_id or ""),
            )
        )
    return customers, invoices, subscriptions, refunds


def ingest_hubspot(session: TwinSession) -> tuple[list[HubSpotCompany], list[HubSpotDeal]]:
    hs = HubSpotTwin(session)

    companies = []
    for c in hs.list_companies():
        p = c.get("properties", {})
        companies.append(
            HubSpotCompany(
                id=c["id"],
                name=p.get("name"),
                domain=p.get("domain"),
                lifecyclestage=p.get("lifecyclestage"),
                stripe_customer_id=p.get("stripe_customer_id") or p.get("description"),
            )
        )

    deals = []
    for d in hs.list_deals():
        p = d.get("properties", {})
        assoc = ((d.get("associations") or {}).get("companies") or {}).get("results", [])
        try:
            amount = float(p.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        deals.append(
            HubSpotDeal(
                id=d["id"],
                dealname=p.get("dealname"),
                amount=amount,
                dealstage=p.get("dealstage"),
                pipeline=p.get("pipeline") or "default",
                company_ids=[a["id"] for a in assoc],
            )
        )
    return companies, deals


def ingest_all(session: TwinSession) -> Snapshot:
    customers, invoices, subscriptions, refunds = ingest_stripe(session)
    companies, deals = ingest_hubspot(session)
    return Snapshot(
        customers=customers,
        invoices=invoices,
        subscriptions=subscriptions,
        refunds=refunds,
        companies=companies,
        deals=deals,
    )
