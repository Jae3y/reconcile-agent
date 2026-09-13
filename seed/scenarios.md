# Seed Scenarios — reconcile-agent

Planted test data for the Arga Twin (Stripe + HubSpot). Seed this fresh at
build time via natural-language prompt to the twin, then run the agent and
eval against it. 20 real discrepancies, 5 decoys the agent must NOT act on.

## Category 1 — Paid in Stripe, deal still open (Automatic tier)
Expected action: move HubSpot deal to Closed Won, add a note.
1. Brightline Analytics
2. Kestrel Foods
3. Halcyon Dental
4. Orbit Logistics

## Category 2 — Duplicate company, name variants (Slack approval tier)
Expected action: flag for merge, request approval in Slack.
5. Veridian Health / Veridian Health Inc.
6. Northstar Media / North Star Media Ltd
7. Pinecrest Solar / PineCrest Solar Co
8. Quill & Co / Quill and Company

## Category 3 — Failed payment, no follow-up (Automatic tier)
Expected action: create a follow-up task, draft a dunning email in Gmail
(draft only, never send).
9. Tidewater Labs
10. Cobalt Fitness
11. Mesa Robotics

## Category 4 — Cancelled in Stripe, still "customer" in CRM (Slack approval tier)
Expected action: flag to mark churned, request approval in Slack.
12. Lumen Cafe
13. Arcadia Books
14. Summit HR

## Category 5 — Amount mismatch between Stripe and CRM (Slack approval tier)
Expected action: flag to fix the deal amount, request approval in Slack.
15. Ferro Steel — CRM shows $24k/yr, Stripe shows $1,500/mo (mismatch)
16. Harbor Legal — CRM shows $6k, Stripe shows $750/mo (mismatch)
17. Ionic Studios — CRM shows $12k, Stripe shows $500/mo (mismatch)

## Category 6 — Stripe customer with no CRM record (Slack approval tier)
Expected action: flag to create a new CRM company, request approval in Slack.
18. Juniper Vet
19. Onyx Security
20. Wren Architects

## Decoys — must NOT be acted on (5 total)
These look like discrepancies but are correct as-is. A false action on any
of these counts against the decoy false-action rate in the eval report.

21. Acme Inc (acme.com) vs Acme Labs (acmelabs.io) — different companies,
    not a duplicate despite similar names.
22. Northwind Traders — $0 trial invoice in Stripe, deal correctly still
    open in CRM. Not a "paid but open" mismatch.
23. Redwood Partners — $12k/yr in CRM equals $1,000/mo in Stripe. Amounts
    are consistent, not a mismatch.
24. Sable Energy — paid then fully refunded in Stripe, deal correctly
    still open in CRM. Not a "paid but open" mismatch.
25. Delta Dynamics — two contacts at one company, not a duplicate company.

## Eval targets
- Detection recall + precision on the 20 real discrepancies (categories 1-6)
- Decoy false-action rate: target 0/5
- Post-state correctness: every automatic and approved action confirmed via
  read-back in `agent/verify.py`
- Consistency: same result across 3 freshly reset twin instances
