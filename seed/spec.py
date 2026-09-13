"""Machine-readable ground truth for seed/scenarios.md.

One entry per numbered scenario. `seed.py` builds the twin state from these and
`eval/run_eval.py` scores the agent against the same table, so the seeder and
the scorer can never drift apart.

Money is in cents everywhere.
"""
from __future__ import annotations

AUTOMATIC = "automatic"
SLACK = "slack_approval"
NONE = "no_action"

# ---- Category 1: paid in Stripe, deal still open -> Automatic -------------
CATEGORY_1 = [
    {"n": 1, "name": "Brightline Analytics", "domain": "brightlineanalytics.com", "paid": 1_200_000},
    {"n": 2, "name": "Kestrel Foods",        "domain": "kestrelfoods.com",        "paid": 480_000},
    {"n": 3, "name": "Halcyon Dental",       "domain": "halcyondental.com",       "paid": 360_000},
    {"n": 4, "name": "Orbit Logistics",      "domain": "orbitlogistics.com",      "paid": 900_000},
]

# ---- Category 2: duplicate company, name variants -> Slack ----------------
CATEGORY_2 = [
    {"n": 5, "name": "Veridian Health", "variant": "Veridian Health Inc.", "domain": "veridianhealth.com"},
    {"n": 6, "name": "Northstar Media", "variant": "North Star Media Ltd", "domain": "northstarmedia.com"},
    {"n": 7, "name": "Pinecrest Solar", "variant": "PineCrest Solar Co",   "domain": "pinecrestsolar.com"},
    {"n": 8, "name": "Quill & Co",      "variant": "Quill and Company",    "domain": "quillandco.com"},
]

# ---- Category 3: failed payment, no follow-up -> Automatic (+Gmail draft) --
CATEGORY_3 = [
    {"n": 9,  "name": "Tidewater Labs", "domain": "tidewaterlabs.com", "due": 450_000},
    {"n": 10, "name": "Cobalt Fitness", "domain": "cobaltfitness.com", "due": 120_000},
    {"n": 11, "name": "Mesa Robotics",  "domain": "mesarobotics.com",  "due": 780_000},
]

# ---- Category 4: cancelled in Stripe, still "customer" in CRM -> Slack ----
CATEGORY_4 = [
    {"n": 12, "name": "Lumen Cafe",    "domain": "lumencafe.com",    "monthly": 29_900},
    {"n": 13, "name": "Arcadia Books", "domain": "arcadiabooks.com",  "monthly": 49_900},
    {"n": 14, "name": "Summit HR",     "domain": "summithr.com",      "monthly": 99_900},
]

# ---- Category 5: amount mismatch -> Slack --------------------------------
# crm_amount is what HubSpot claims; monthly*12 is what Stripe actually bills.
CATEGORY_5 = [
    {"n": 15, "name": "Ferro Steel",   "domain": "ferrosteel.com",   "crm": 2_400_000, "monthly": 150_000},
    {"n": 16, "name": "Harbor Legal",  "domain": "harborlegal.com",  "crm":   600_000, "monthly":  75_000},
    {"n": 17, "name": "Ionic Studios", "domain": "ionicstudios.com", "crm": 1_200_000, "monthly":  50_000},
]

# ---- Category 6: Stripe customer with no CRM record -> Slack -------------
CATEGORY_6 = [
    {"n": 18, "name": "Juniper Vet",    "domain": "junipervet.com",    "monthly": 65_000},
    {"n": 19, "name": "Onyx Security",  "domain": "onyxsecurity.com",  "monthly": 120_000},
    {"n": 20, "name": "Wren Architects","domain": "wrenarchitects.com","monthly": 85_000},
]

# ---- Decoys: must produce ZERO actions ------------------------------------
DECOYS = [
    {"n": 21, "name": "Acme Inc",          "domain": "acme.com",
     "variant": "Acme Labs", "variant_domain": "acmelabs.io",
     "why": "Different companies despite similar names; different domains."},
    {"n": 22, "name": "Northwind Traders", "domain": "northwindtraders.com",
     "why": "$0 trial invoice; deal correctly still open. Not paid-but-open."},
    {"n": 23, "name": "Redwood Partners",  "domain": "redwoodpartners.com",
     "crm": 1_200_000, "monthly": 100_000,
     "why": "$12k/yr CRM == $1,000/mo Stripe. Amounts agree."},
    {"n": 24, "name": "Sable Energy",      "domain": "sableenergy.com", "paid": 700_000,
     "why": "Paid then fully refunded; deal correctly still open."},
    {"n": 25, "name": "Delta Dynamics",    "domain": "deltadynamics.com",
     "why": "Two contacts at one company, not a duplicate company."},
]


def expectations() -> dict[str, dict]:
    """subject -> expected outcome. The eval's answer key."""
    exp: dict[str, dict] = {}
    for s in CATEGORY_1:
        exp[s["name"]] = {"n": s["n"], "tier": AUTOMATIC, "type": "paid_but_open", "decoy": False}
    for s in CATEGORY_2:
        exp[s["name"]] = {"n": s["n"], "tier": SLACK, "type": "duplicate_company", "decoy": False}
    for s in CATEGORY_3:
        exp[s["name"]] = {"n": s["n"], "tier": AUTOMATIC, "type": "failed_payment", "decoy": False}
    for s in CATEGORY_4:
        exp[s["name"]] = {"n": s["n"], "tier": SLACK, "type": "cancelled_but_active", "decoy": False}
    for s in CATEGORY_5:
        exp[s["name"]] = {"n": s["n"], "tier": SLACK, "type": "amount_mismatch", "decoy": False}
    for s in CATEGORY_6:
        exp[s["name"]] = {"n": s["n"], "tier": SLACK, "type": "no_crm_record", "decoy": False}
    for s in DECOYS:
        exp[s["name"]] = {"n": s["n"], "tier": NONE, "type": "none", "decoy": True, "why": s["why"]}
    return exp


REAL_DISCREPANCY_COUNT = 20
DECOY_COUNT = 5


def alias_map() -> dict[str, str]:
    """Every name a scenario can surface under -> its canonical scenario name.

    Duplicate-pair candidates may be reported under either the primary record
    or the variant (company ordering is not guaranteed), so the eval resolves
    both back to one subject before scoring.
    """
    aliases: dict[str, str] = {}
    groups = (CATEGORY_1, CATEGORY_2, CATEGORY_3, CATEGORY_4,
              CATEGORY_5, CATEGORY_6, DECOYS)
    for group in groups:
        for s in group:
            aliases[s["name"]] = s["name"]
            if s.get("variant"):
                aliases[s["variant"]] = s["name"]
    return aliases


def canonical(subject: str) -> str:
    """Resolve a reported subject to its scenario name (identity if unknown)."""
    return alias_map().get(subject, subject)
