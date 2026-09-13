# reconcile-agent

An agent that finds discrepancies between billing (Stripe, via Arga Twins) and
CRM (HubSpot, via Arga Twins) data, fixes safe issues automatically, routes
ambiguous ones to Slack for human approval, and proves its accuracy against a
planted eval suite.

## Architecture — five stages, in order

1. **Ingest** (`agent/ingest.py`) — pull Stripe customers/invoices/subscriptions
   and HubSpot companies/deals via their SDKs, pointed at Arga Twins endpoints.
2. **Candidates** (`agent/candidates.py`) — deterministic pass using rapidfuzz:
   same email domain, normalized name similarity above a threshold. This never
   calls an LLM. It only proposes pairs for the judge to evaluate.
3. **Judge** (`agent/judge.py`) — one call per candidate pair via Antigravity
   CLI, invoked as a subprocess in non-interactive mode, model: Claude Sonnet
   4.6 (Thinking). Authenticated through the logged-in Google AI Pro account
   already on this machine — no separate API key, no billing, no balance to
   run out. Parse the CLI's output into a structured object: `{is_match: bool,
   discrepancy_type: str, confidence: float, reasoning: str}`. If the CLI
   doesn't return clean JSON, wrap the prompt to explicitly demand JSON-only
   output and parse defensively (strip markdown fences, retry once on parse
   failure).
4. **Policy router** (`agent/policy.py`) — assigns each judged finding to one
   of three tiers (below). Pure logic, no LLM call.
5. **Executor** (`agent/executor.py`) — performs the action via the relevant
   SDK, then calls `agent/verify.py` to read the record back and confirm the
   write actually took effect. On mismatch, retry once, then log as a failure.
   Sends Slack approval requests for the appropriate tier. Writes a run report.

## Autonomy tiers

- **Automatic** — confidence >= 0.9 AND reversible. Examples: update deal
  stage with a note, create a follow-up task, link a Stripe ID to a CRM
  record.
- **Slack approval required** — merge duplicate companies, change a deal
  amount, mark a customer churned, create a new CRM company.
- **Never** — refunds, deletions, direct customer emails. Gmail is draft-only,
  never send.

## Hard rules for any code generated in this repo

- The judge step ALWAYS returns a Pydantic model, never raw text parsed with
  loose string matching. The Antigravity CLI subprocess call returns text —
  parse it into the Pydantic model immediately, at the boundary, before it
  touches any other code.
- Every write in `executor.py` is followed by a read-back in `verify.py`
  before the action is considered successful. This is the core reliability
  mechanism — do not skip it to save time.
- Candidate generation (`candidates.py`) never calls an LLM. Keep it fast and
  deterministic so the judge only sees pairs worth reasoning about.
- All external calls (Stripe, HubSpot, Slack) point at Arga Twins endpoints
  by default, configured via environment variables in `.env` (see
  `.env.example`). Never hardcode a real production API base URL.
- Instrument agent runs with Lemma tracing (see `LEMMA_API_KEY`,
  `LEMMA_PROJECT_ID` in `.env.example`).
- The judge step uses Antigravity CLI (Claude Sonnet 4.6, Thinking) via
  subprocess, authenticated through the logged-in Google AI Pro account.
  No Anthropic API key, no Gemini API key, no per-token billing anywhere in
  this project.
- No code in this repo was written before the hackathon build window began.
  Only scaffolding (`.gitignore`, this file, `.env.example`, `seed/scenarios.md`)
  predates it.

## Repo layout (target)
