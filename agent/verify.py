"""Stage 5a - Read-back verification.

CLAUDE.md hard rule: every write in executor.py is followed by a read-back here
before the action counts as successful. This is the reliability mechanism that
catches a silent failure - an API that returns 200 while changing nothing.

Every function re-fetches the record from the twin (never from a cached
response or the write's own return value) and reports what it actually observed.
"""
from __future__ import annotations

from .clients import gmail_client, HubSpotTwin
from .config import TwinSession
from .models import VerifyResult


def verify_deal_field(session: TwinSession, deal_id: str, field: str, expected) -> VerifyResult:
    """Re-fetch a HubSpot deal and confirm one property landed."""
    hs = HubSpotTwin(session)
    try:
        deal = hs.get_deal(deal_id, properties=[field, "dealname", "dealstage", "amount"])
    except Exception as exc:
        return VerifyResult(ok=False, field=field, expected=expected, observed=None,
                            detail=f"read-back failed: {type(exc).__name__}: {exc}")
    observed = (deal.get("properties") or {}).get(field)
    ok = str(observed) == str(expected)
    return VerifyResult(
        ok=ok, field=field, expected=expected, observed=observed,
        detail=("matches" if ok else f"expected {expected!r} but the record still reads {observed!r}"),
    )


def verify_company_field(session: TwinSession, company_id: str, field: str, expected) -> VerifyResult:
    hs = HubSpotTwin(session)
    try:
        company = hs.get_company(company_id, properties=[field, "name", "domain", "lifecyclestage"])
    except Exception as exc:
        return VerifyResult(ok=False, field=field, expected=expected, observed=None,
                            detail=f"read-back failed: {type(exc).__name__}: {exc}")
    observed = (company.get("properties") or {}).get(field)
    ok = str(observed) == str(expected)
    return VerifyResult(
        ok=ok, field=field, expected=expected, observed=observed,
        detail=("matches" if ok else f"expected {expected!r} but the record still reads {observed!r}"),
    )


def verify_company_exists(session: TwinSession, name: str) -> VerifyResult:
    """Confirm a company with this name is now present in the CRM."""
    hs = HubSpotTwin(session)
    try:
        names = [(c.get("properties") or {}).get("name") for c in hs.list_companies()]
    except Exception as exc:
        return VerifyResult(ok=False, field="name", expected=name, observed=None,
                            detail=f"read-back failed: {type(exc).__name__}: {exc}")
    ok = name in names
    return VerifyResult(ok=ok, field="name", expected=name,
                        observed=name if ok else None,
                        detail="company present" if ok else "company absent after create")


def verify_task_exists(session: TwinSession, subject: str) -> VerifyResult:
    hs = HubSpotTwin(session)
    try:
        tasks = hs._paged("/crm/v3/objects/tasks", "hs_task_subject,hs_task_status")
    except Exception as exc:
        return VerifyResult(ok=False, field="hs_task_subject", expected=subject, observed=None,
                            detail=f"read-back failed: {type(exc).__name__}: {exc}")
    subjects = [(t.get("properties") or {}).get("hs_task_subject") for t in tasks]
    ok = subject in subjects
    return VerifyResult(ok=ok, field="hs_task_subject", expected=subject,
                        observed=subject if ok else None,
                        detail="task present" if ok else "task absent after create")


def verify_draft_exists(session: TwinSession, before_count: int) -> VerifyResult:
    """Gmail is draft-only. Confirm the draft count actually grew."""
    try:
        after = len(gmail_client(session).list_drafts())
    except Exception as exc:
        return VerifyResult(ok=False, field="drafts", expected=before_count + 1, observed=None,
                            detail=f"read-back failed: {type(exc).__name__}: {exc}")
    ok = after > before_count
    return VerifyResult(ok=ok, field="drafts", expected=before_count + 1, observed=after,
                        detail="draft created" if ok else "draft count did not increase")
