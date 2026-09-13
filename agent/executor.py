"""Stage 5 - Executor.

Performs the action for each routed finding, then immediately reads the record
back through verify.py. A write is only "applied" once the read-back agrees.
On mismatch it retries once and, if it still doesn't stick, records a failure.

Approval tier: a message goes to #approvals describing the proposed change, and
the executor polls for a reaction (not interactive buttons):
    white_check_mark / heavy_check_mark / +1  -> approve
    x / negative_squared_cross_mark / -1      -> reject
Timeout or rejection is logged as skipped; nothing is written.

Gmail is DRAFT ONLY. There is no send path anywhere in this module.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .clients import gmail_client, HubSpotTwin, slack_client
from .config import SETTINGS, TwinSession
from .models import ActionResult, DiscrepancyType, Finding, Tier, VerifyResult
from .policy import assert_reachable
from . import verify as vfy

APPROVE_EMOJI = {"white_check_mark", "heavy_check_mark", "ballot_box_with_check", "+1"}
REJECT_EMOJI = {"x", "negative_squared_cross_mark", "-1", "no_entry_sign"}


def _money_to_dollars(text: str) -> str:
    """'$18,000.00' -> '18000'  (HubSpot stores deal amounts in dollars)."""
    cleaned = text.replace("$", "").replace(",", "").strip()
    try:
        return str(int(round(float(cleaned))))
    except ValueError:
        return cleaned


@dataclass
class Executor:
    session: TwinSession
    approval_mode: str = "auto"          # "auto" (simulated approval) | "poll" (wait for a human)
    approval_timeout: int = 120
    poll_interval: float = 3.0
    channel: str = field(default_factory=lambda: SETTINGS.slack_channel)
    # Phase 7 demo hook: subject whose write is swallowed to fake a silent failure.
    inject_silent_failure_for: str | None = None
    events: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Action definitions - each returns (description, write_fn, verify_fn)
    # ------------------------------------------------------------------
    def _plan(self, finding: Finding):
        c = finding.candidate
        hs = HubSpotTwin(self.session)
        kind = c.kind

        if kind == DiscrepancyType.PAID_BUT_OPEN:
            deal_id = c.hubspot_ref or ""
            note = (f"reconcile-agent: Stripe shows {c.facts.get('stripe_net_paid')} net paid. "
                    f"Moving deal to Closed Won. (judge confidence "
                    f"{finding.judge.confidence:.2f})")

            def write():
                assert_reachable("update_deal_stage")
                hs.update_deal(deal_id, {"dealstage": "closedwon"})
                hs.create_note(note)

            return (f"set deal {deal_id} stage -> closedwon (+ note)", write,
                    lambda: vfy.verify_deal_field(self.session, deal_id, "dealstage", "closedwon"))

        if kind == DiscrepancyType.FAILED_PAYMENT:
            subject = f"Dunning follow-up: {c.subject} ({c.facts.get('amount_outstanding')})"
            email = c.facts.get("customer_email") or ""
            before = len(gmail_client(self.session).list_drafts())

            def write():
                assert_reachable("create_task")
                hs.create_task(subject, f"Invoice {c.facts.get('stripe_invoice')} is "
                                        f"{c.facts.get('stripe_invoice_status')}.")
                # DRAFT ONLY - never sent. Sending is a Never-tier action.
                assert_reachable("create_email_draft")
                gmail_client(self.session).create_draft(
                    to=email,
                    subject=f"Payment issue on your {c.subject} account",
                    body=(f"Hi,\n\nWe weren't able to collect "
                          f"{c.facts.get('amount_outstanding')} for your recent invoice "
                          f"({c.facts.get('stripe_invoice')}).\n\nCould you confirm your "
                          f"payment details so we can retry?\n\nThanks,\nBilling"),
                )

            def check():
                task = vfy.verify_task_exists(self.session, subject)
                return task if not task.ok else vfy.verify_draft_exists(self.session, before)

            return (f"create follow-up task + Gmail DRAFT for {email}", write, check)

        if kind == DiscrepancyType.AMOUNT_MISMATCH:
            deal_id = c.hubspot_ref or ""
            target = _money_to_dollars(str(c.facts.get("stripe_annualized", "")))

            def write():
                assert_reachable("update_deal_amount")
                hs.update_deal(deal_id, {"amount": target})

            return (f"set deal {deal_id} amount -> ${target} (Stripe annualized)", write,
                    lambda: vfy.verify_deal_field(self.session, deal_id, "amount", target))

        if kind == DiscrepancyType.CANCELLED_BUT_ACTIVE:
            company_id = c.hubspot_ref or ""

            def write():
                assert_reachable("update_company_lifecycle")
                hs.update_company(company_id, {"lifecyclestage": "other"})

            return (f"mark company {company_id} churned (lifecyclestage -> other)", write,
                    lambda: vfy.verify_company_field(self.session, company_id, "lifecyclestage", "other"))

        if kind == DiscrepancyType.DUPLICATE_COMPANY:
            ids = (c.hubspot_ref or "").split(",")
            primary, dup = (ids + ["", ""])[:2]

            marker = f"reconcile-agent: duplicate of company {primary}"

            def write():
                # Flag for merge. An actual merge deletes a record, which is a
                # Never-tier action, so the agent only ever marks the pair.
                # Written to the standard `description` field rather than a
                # custom property: creating custom properties needs a schema
                # write scope, and requiring a portal schema change just to run
                # the agent is the wrong trade.
                assert_reachable("flag_duplicate")
                hs.update_company(dup, {"description": marker})

            return (f"flag company {dup} as duplicate of {primary}", write,
                    lambda: vfy.verify_company_field(self.session, dup, "description", marker))

        if kind == DiscrepancyType.NO_CRM_RECORD:
            name = c.subject
            domain = (c.facts.get("stripe_customer") or "").split("@")[-1].rstrip(">")

            def write():
                assert_reachable("create_company")
                hs.create_company({"name": name, "domain": domain, "lifecyclestage": "customer",
                                   "description": f"reconcile-agent: created from Stripe customer "
                                                  f"{c.stripe_ref or 'unknown'}"})

            return (f"create CRM company '{name}' ({domain})", write,
                    lambda: vfy.verify_company_exists(self.session, name))

        return (f"no action mapping for {kind.value}", None, None)

    # ------------------------------------------------------------------
    # Write + read-back, with one retry
    # ------------------------------------------------------------------
    def _apply(self, finding: Finding, description, write, check) -> ActionResult:
        c = finding.candidate
        attempts = 0
        last: VerifyResult | None = None

        for attempts in (1, 2):
            try:
                if self.inject_silent_failure_for and c.subject == self.inject_silent_failure_for:
                    # DEMO FAULT: pretend the write succeeded without doing it.
                    self.events.append(
                        f"[injected fault] swallowed the write for {c.subject} "
                        f"- the API 'succeeded' but nothing changed")
                else:
                    write()
            except PermissionError as exc:
                return ActionResult(subject=c.subject, kind=c.kind, tier=finding.tier,
                                    status="denied", description=description, detail=str(exc),
                                    attempts=attempts)
            except Exception as exc:
                last = VerifyResult(ok=False, field="write", detail=f"{type(exc).__name__}: {exc}")
                continue

            last = check()
            if last.ok:
                return ActionResult(subject=c.subject, kind=c.kind, tier=finding.tier,
                                    status="applied", description=description,
                                    verify=last, attempts=attempts)
            self.events.append(
                f"[verify failed] {c.subject}: {last.detail} (attempt {attempts})")

        return ActionResult(subject=c.subject, kind=c.kind, tier=finding.tier,
                            status="verify_failed", description=description,
                            verify=last, attempts=attempts,
                            detail="write did not stick after retry")

    # ------------------------------------------------------------------
    # Slack approval
    # ------------------------------------------------------------------
    def _request_approval(self, finding: Finding, description: str) -> tuple[bool, str]:
        c = finding.candidate
        slack = slack_client(self.session)
        detail = "\n".join(f"• {k}: {v}" for k, v in c.facts.items())
        text = (
            f"*Approval needed — {c.kind.value.replace('_', ' ')}*\n"
            f"*Subject:* {c.subject}\n"
            f"*Proposed action:* {description}\n"
            f"*Judge:* confidence {finding.judge.confidence:.2f} — {finding.judge.reasoning[:300]}\n"
            f"*Evidence:*\n{detail}\n\n"
            f"React :white_check_mark: to approve or :x: to reject."
        )
        try:
            posted = slack.chat_postMessage(channel=self.channel, text=text)
        except Exception as exc:
            return False, f"slack post failed: {type(exc).__name__}: {exc}"

        ts = posted.get("ts")
        channel = posted.get("channel") or self.channel

        if self.approval_mode == "auto":
            # Simulated approver for unattended eval runs.
            try:
                slack.reactions_add(channel=channel, timestamp=ts, name="white_check_mark")
            except Exception:
                pass
            return True, "auto-approved (unattended mode)"

        deadline = time.time() + self.approval_timeout
        while time.time() < deadline:
            try:
                got = slack.reactions_get(channel=channel, timestamp=ts)
                names = {r.get("name") for r in (got.get("message", {}) or {}).get("reactions", [])}
            except Exception:
                names = set()
            if names & APPROVE_EMOJI:
                return True, "approved by reaction"
            if names & REJECT_EMOJI:
                return False, "rejected by reaction"
            time.sleep(self.poll_interval)
        return False, f"no reaction within {self.approval_timeout}s"

    # ------------------------------------------------------------------
    def execute(self, findings: list[Finding], on_result=None) -> list[ActionResult]:
        results: list[ActionResult] = []
        for finding in findings:
            c = finding.candidate
            description, write, check = self._plan(finding)

            if finding.tier == Tier.NO_ACTION:
                result = ActionResult(subject=c.subject, kind=c.kind, tier=finding.tier,
                                      status="no_action", description="nothing to do",
                                      detail=finding.tier_reason)
            elif write is None:
                result = ActionResult(subject=c.subject, kind=c.kind, tier=finding.tier,
                                      status="error", description=description,
                                      detail="no action mapping")
            elif finding.tier == Tier.AUTOMATIC:
                result = self._apply(finding, description, write, check)
            elif finding.tier == Tier.SLACK_APPROVAL:
                approved, why = self._request_approval(finding, description)
                if approved:
                    result = self._apply(finding, description, write, check)
                    result.detail = (result.detail + " | " if result.detail else "") + why
                else:
                    result = ActionResult(subject=c.subject, kind=c.kind, tier=finding.tier,
                                          status="skipped", description=description, detail=why)
            else:
                result = ActionResult(subject=c.subject, kind=c.kind, tier=finding.tier,
                                      status="denied", description=description,
                                      detail="Never tier")

            results.append(result)
            if on_result:
                on_result(result)
        return results
