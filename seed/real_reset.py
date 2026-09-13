"""Targeted cleanup for a REAL HubSpot portal.

Seeding a real CRM is not idempotent the way the simulator is: running the
seeder twice would leave two copies of all 25 scenarios. Before a real seed we
archive only the records this seeder created, identified by the exact company
domains in seed/spec.py. Nothing outside that allow-list is touched, so any
pre-existing records in the portal are left alone.

HubSpot's DELETE archives rather than hard-deletes (recoverable in the portal),
so this is reversible.
"""
from __future__ import annotations

from agent.clients import HubSpotTwin
from agent.config import TwinSession

from . import spec


def seeded_domains() -> set[str]:
    domains: set[str] = set()
    for group in (spec.CATEGORY_1, spec.CATEGORY_2, spec.CATEGORY_3,
                  spec.CATEGORY_4, spec.CATEGORY_5, spec.CATEGORY_6, spec.DECOYS):
        for s in group:
            if s.get("domain"):
                domains.add(s["domain"].lower())
            if s.get("variant_domain"):
                domains.add(s["variant_domain"].lower())
    return domains


def seeded_names() -> set[str]:
    names: set[str] = set()
    for group in (spec.CATEGORY_1, spec.CATEGORY_2, spec.CATEGORY_3,
                  spec.CATEGORY_4, spec.CATEGORY_5, spec.CATEGORY_6, spec.DECOYS):
        for s in group:
            names.add(s["name"])
            if s.get("variant"):
                names.add(s["variant"])
    return names


def reset(session: TwinSession, on_line=None) -> dict:
    """Archive previously-seeded companies and their deals. Returns counts."""
    hs = HubSpotTwin(session)
    domains, names = seeded_domains(), seeded_names()
    removed_companies = removed_deals = 0

    # Deals first: a deal whose name starts with a seeded company name.
    for deal in hs.list_deals():
        dealname = (deal.get("properties") or {}).get("dealname") or ""
        if any(dealname.startswith(f"{n} - ") for n in names):
            try:
                hs._req("DELETE", f"/crm/v3/objects/deals/{deal['id']}")
                removed_deals += 1
            except Exception:
                pass

    for company in hs.list_companies():
        props = company.get("properties") or {}
        domain = (props.get("domain") or "").lower()
        name = props.get("name") or ""
        if domain in domains or name in names:
            try:
                hs._req("DELETE", f"/crm/v3/objects/companies/{company['id']}")
                removed_companies += 1
            except Exception:
                pass

    if on_line:
        on_line(f"archived {removed_companies} companies, {removed_deals} deals")
    return {"companies": removed_companies, "deals": removed_deals}
