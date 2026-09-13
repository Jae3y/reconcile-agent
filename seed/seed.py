"""Stage 0 - Seed a freshly provisioned twin with the 25 scenarios.

Seeding goes through direct SDK/API calls rather than Arga's natural-language
scenario prompt. Both are supported by the platform, but the eval needs exact
amounts and statuses (Ferro Steel must be $1,500/mo against a $24k deal, not
"about that"), and scenarios.md asks for identical results across repeated
fresh twins. Deterministic writes are the only way to guarantee that.

Re-runnable: every call targets a freshly provisioned, empty twin.
"""
from __future__ import annotations

import concurrent.futures as cf

from agent.clients import HubSpotTwin, stripe_client
from agent.config import TwinSession

from . import spec

OPEN_STAGE = "presentationscheduled"
OPEN_EARLY = "appointmentscheduled"
OPEN_LATE = "decisionmakerboughtin"
WON = "closedwon"


# --------------------------------------------------------------------------
# Stripe primitives
# --------------------------------------------------------------------------
def _customer(sc, name: str, domain: str):
    return sc.v1.customers.create({"name": name, "email": f"billing@{domain}"})


def _paid_invoice(sc, cust_id: str, amount: int):
    sc.v1.invoice_items.create({"customer": cust_id, "amount": amount, "currency": "usd",
                                "description": "Annual subscription"})
    inv = sc.v1.invoices.create({"customer": cust_id, "collection_method": "send_invoice",
                                 "days_until_due": 30})
    sc.v1.invoices.finalize_invoice(inv.id)
    return sc.v1.invoices.pay(inv.id, {"paid_out_of_band": True})


def _failed_payment_invoice(sc, cust_id: str, amount: int):
    """An OPEN, past-due invoice whose collection attempt was declined.

    Deliberately not `mark_uncollectible`: in Stripe that means the debt has been
    written off, which is the opposite of "failed payment needing follow-up".
    Seeding it that way made the judge (correctly) refuse to raise a dunning
    task, so the fixture models the real shape - open, with a failed attempt.
    """
    sc.v1.invoice_items.create({"customer": cust_id, "amount": amount, "currency": "usd",
                                "description": "Monthly subscription"})
    inv = sc.v1.invoices.create({"customer": cust_id, "collection_method": "charge_automatically"})
    sc.v1.invoices.finalize_invoice(inv.id)
    try:
        sc.v1.invoices.pay(inv.id, {"payment_method": "pm_card_chargeDeclined"})
    except Exception:
        pass          # the decline is the point
    return sc.v1.invoices.retrieve(inv.id)


def _zero_trial_invoice(sc, cust_id: str):
    sc.v1.invoice_items.create({"customer": cust_id, "amount": 0, "currency": "usd",
                                "description": "Free trial"})
    inv = sc.v1.invoices.create({"customer": cust_id, "collection_method": "send_invoice",
                                 "days_until_due": 30})
    return sc.v1.invoices.finalize_invoice(inv.id)


def _subscription(sc, cust_id: str, monthly: int, label: str, cancel: bool = False):
    prod = sc.v1.products.create({"name": f"{label} Plan"})
    price = sc.v1.prices.create({"product": prod.id, "unit_amount": monthly, "currency": "usd",
                                 "recurring": {"interval": "month"}})
    sub = sc.v1.subscriptions.create({"customer": cust_id, "items": [{"price": price.id}]})
    if cancel:
        sub = sc.v1.subscriptions.cancel(sub.id)
    return sub


def _refunded_charge(sc, cust_id: str, amount: int):
    pi = sc.v1.payment_intents.create({
        "amount": amount, "currency": "usd", "customer": cust_id,
        "payment_method": "pm_card_visa", "confirm": True,
        "automatic_payment_methods": {"enabled": True, "allow_redirects": "never"},
    })
    return sc.v1.refunds.create({"payment_intent": pi.id})


# --------------------------------------------------------------------------
# HubSpot primitives
# --------------------------------------------------------------------------
def _company(hs: HubSpotTwin, name: str, domain: str, lifecycle: str = "customer"):
    return hs.create_company({"name": name, "domain": domain, "lifecyclestage": lifecycle})


def _deal(hs: HubSpotTwin, company_id: str, name: str, amount_cents: int, stage: str):
    deal = hs.create_deal({
        "dealname": f"{name} - Subscription",
        "amount": str(amount_cents // 100),          # HubSpot stores dollars
        "dealstage": stage,
        "pipeline": "default",
    })
    hs.associate_deal_company(deal["id"], company_id)
    return deal


# --------------------------------------------------------------------------
# Per-category builders (each returns a short log line)
# --------------------------------------------------------------------------
def _build_cat1(sc, hs, s) -> str:
    cust = _customer(sc, s["name"], s["domain"])
    _paid_invoice(sc, cust.id, s["paid"])
    co = _company(hs, s["name"], s["domain"])
    _deal(hs, co["id"], s["name"], s["paid"], OPEN_STAGE)
    return f"{s['n']:>2}. {s['name']}: paid ${s['paid'] // 100:,} in Stripe, deal still open"


def _build_cat2(sc, hs, s) -> str:
    _customer(sc, s["name"], s["domain"])
    primary = _company(hs, s["name"], s["domain"])
    _company(hs, s["variant"], s["domain"])          # the duplicate
    _deal(hs, primary["id"], s["name"], 500_000, "qualifiedtobuy")
    return f"{s['n']:>2}. {s['name']}: duplicate CRM company '{s['variant']}' on {s['domain']}"


def _build_cat3(sc, hs, s) -> str:
    cust = _customer(sc, s["name"], s["domain"])
    _failed_payment_invoice(sc, cust.id, s["due"])
    co = _company(hs, s["name"], s["domain"])
    _deal(hs, co["id"], s["name"], s["due"], OPEN_LATE)
    return f"{s['n']:>2}. {s['name']}: ${s['due'] // 100:,} invoice open after a declined payment, no follow-up"


def _build_cat4(sc, hs, s) -> str:
    cust = _customer(sc, s["name"], s["domain"])
    _subscription(sc, cust.id, s["monthly"], s["name"], cancel=True)
    co = _company(hs, s["name"], s["domain"], lifecycle="customer")
    _deal(hs, co["id"], s["name"], s["monthly"] * 12, WON)
    return f"{s['n']:>2}. {s['name']}: subscription cancelled, CRM still lifecyclestage=customer"


def _build_cat5(sc, hs, s) -> str:
    cust = _customer(sc, s["name"], s["domain"])
    _subscription(sc, cust.id, s["monthly"], s["name"])
    co = _company(hs, s["name"], s["domain"])
    _deal(hs, co["id"], s["name"], s["crm"], WON)
    return (f"{s['n']:>2}. {s['name']}: CRM ${s['crm'] // 100:,} vs Stripe "
            f"${s['monthly'] // 100:,}/mo (${s['monthly'] * 12 // 100:,}/yr)")


def _build_cat6(sc, hs, s) -> str:
    cust = _customer(sc, s["name"], s["domain"])
    _subscription(sc, cust.id, s["monthly"], s["name"])
    return f"{s['n']:>2}. {s['name']}: Stripe customer with no CRM company at all"


def _build_decoy(sc, hs, s) -> str:
    n = s["n"]
    if n == 21:   # Acme Inc vs Acme Labs - similar names, genuinely different firms
        _customer(sc, s["name"], s["domain"])
        a = _company(hs, s["name"], s["domain"])
        b = _company(hs, s["variant"], s["variant_domain"])
        _deal(hs, a["id"], s["name"], 800_000, WON)
        _deal(hs, b["id"], s["variant"], 300_000, WON)
    elif n == 22:  # $0 trial invoice, deal correctly open
        cust = _customer(sc, s["name"], s["domain"])
        _zero_trial_invoice(sc, cust.id)
        co = _company(hs, s["name"], s["domain"], lifecycle="opportunity")
        _deal(hs, co["id"], s["name"], 400_000, OPEN_EARLY)
    elif n == 23:  # amounts genuinely agree
        cust = _customer(sc, s["name"], s["domain"])
        _subscription(sc, cust.id, s["monthly"], s["name"])
        co = _company(hs, s["name"], s["domain"])
        _deal(hs, co["id"], s["name"], s["crm"], WON)
    elif n == 24:  # paid then fully refunded
        cust = _customer(sc, s["name"], s["domain"])
        _paid_invoice(sc, cust.id, s["paid"])
        _refunded_charge(sc, cust.id, s["paid"])
        co = _company(hs, s["name"], s["domain"], lifecycle="opportunity")
        _deal(hs, co["id"], s["name"], s["paid"], OPEN_STAGE)
    else:          # 25 - two contacts at one company
        _customer(sc, s["name"], s["domain"])
        co = _company(hs, s["name"], s["domain"])
        for first, last in (("Dana", "Okoro"), ("Priya", "Raman")):
            hs.create_contact({"email": f"{first.lower()}@{s['domain']}",
                               "firstname": first, "lastname": last, "company": s["name"]})
        _deal(hs, co["id"], s["name"], 600_000, WON)
    return f"{n:>2}. {s['name']} [DECOY]: {s['why']}"


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
_JOBS = (
    [(_build_cat1, s) for s in spec.CATEGORY_1]
    + [(_build_cat2, s) for s in spec.CATEGORY_2]
    + [(_build_cat3, s) for s in spec.CATEGORY_3]
    + [(_build_cat4, s) for s in spec.CATEGORY_4]
    + [(_build_cat5, s) for s in spec.CATEGORY_5]
    + [(_build_cat6, s) for s in spec.CATEGORY_6]
    + [(_build_decoy, s) for s in spec.DECOYS]
)


def seed_all(session: TwinSession, workers: int = 8, on_line=None) -> dict:
    """Populate the twin. Returns {'lines': [...], 'errors': [...]}."""
    from agent.config import is_real

    # A real CRM is not wiped by resetting the simulator, so clear previously
    # seeded records first or every run leaves another copy of all 25 scenarios.
    if is_real("hubspot"):
        from .real_reset import reset as reset_real_hubspot

        reset_real_hubspot(session, on_line=on_line)

    sc = stripe_client(session)
    hs = HubSpotTwin(session)
    lines: list[str] = []
    errors: list[str] = []

    def run(job):
        fn, s = job
        try:
            return fn(sc, hs, s), None
        except Exception as exc:
            return None, f"{s['n']}. {s['name']}: {type(exc).__name__}: {exc}"

    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for line, err in pool.map(run, _JOBS):
            if err:
                errors.append(err)
            else:
                lines.append(line)
                if on_line:
                    on_line(line)

    lines.sort(key=lambda line: int(line.split(".")[0]))
    return {"lines": lines, "errors": errors}
