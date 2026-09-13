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

## Resource interlocks (learned the hard way)

Two upstream quotas were exhausted during the build. Both now have guards, and
any future work in this repo must respect them.

- **Arga Labs** — free plan allows 10 validation runs/month, one twin per run,
  fixed 10-minute TTL. `agent/twins.py` raises `ArgaQuotaLock` on any
  provisioning attempt unless `ARGA_ALLOW_PROVISION=true` is set explicitly.
  The pipeline therefore runs against `sim/twin_sim.py`, a local simulator that
  re-serves the exact request/response shapes captured from the live twins.
  Swapping back is one environment variable.
- **Antigravity CLI** — per-account daily quota. A `RESOURCE_EXHAUSTED` / 429
  is NEVER retried (retrying doubles load on an exhausted quota). Verdicts are
  cached in `runs/judge_cache/` under a *semantic* key that scrubs volatile
  Stripe/HubSpot object ids, so re-running an eval costs quota only for
  candidates whose underlying facts changed.
- **A partial run is reported as INVALID, never scored.** If any candidate
  fails to get a verdict, the eval refuses to present detection metrics, because
  a number computed over a partially-failed run measures upstream availability
  rather than agent quality.

## Phase 9 — Web dashboard (Next.js) + Phase 10 — FastAPI service

The dashboard is not decoration on top of the agent. It is the tangible proof,
to a hands-on judge who will click through it, that every integration genuinely
works live. Build it to survive someone actively poking at it.

### ABSOLUTE CONSTRAINT — no mock data

Every value rendered must come from a real API response reflecting an actual
run against the real sandbox. Zero mock data anywhere in the shipped build. No
fixture JSON, no hardcoded sample rows, no placeholder numbers. Before any phase
is considered done, trigger a real run and confirm every visible number matches
the API response byte for byte.

Where the sandbox is the local twin simulator rather than live Arga twins, the
UI must say so plainly rather than implying live Arga. Honest labelling is part
of the constraint, not an exception to it.

### Phase 10 — `api/` FastAPI service (built first; the dashboard depends on it)

The dashboard consumes this; it did not exist before Phase 9 and must be built
first.

- `GET  /api/health` — process liveness.
- `GET  /api/integrations` — **live** per-integration reachability for Stripe,
  HubSpot, Slack, Gmail, Lemma and Arga. Each entry performs a genuine
  request/ping and reports latency plus the endpoint actually probed. Green
  means verified reachable right now, never merely "configured".
- `GET  /api/scenarios` — the 25 planted scenarios from `seed/spec.py`.
- `POST /api/seed` — reseed a fresh twin.
- `POST /api/run` — start a pipeline run; returns a `run_id` immediately.
- `POST /api/eval` — start an eval run; returns a `run_id` immediately.
- `GET  /api/runs` — run history, newest first, with metrics per run.
- `GET  /api/runs/{run_id}` — full detail: stages, candidates, verdicts,
  findings, actions, read-back results.
- `GET  /api/stream/{run_id}` — **Server-Sent Events**, phase-by-phase, pushed
  as the run executes. Not polling. The system map is driven by this stream.

Rules: Pydantic response models for every endpoint (the TS client mirrors this
schema exactly). Runs execute in a background worker so the HTTP request never
blocks. CORS allowlist must include the deployed Vercel domain. The API reuses
`agent/` directly — it never reimplements pipeline logic.

### Phase 9 — `web/` Next.js dashboard

**Stack.** Next.js (App Router), TypeScript, Tailwind, shadcn/ui as the
component foundation. **GSAP is the single animation engine** — timelines,
scroll-triggers, sequenced reveals; no competing animation library. Real-time
transport is SSE from FastAPI, never polling — the live transport is itself a
technical-execution point to demonstrate.

**3D decision.** The spec allows Spline-authored 3D *or* a well-built 2D node
graph, whichever is the sounder call under time pressure. No Spline MCP server
is present in this environment, so build-time Spline authoring is unavailable;
the system map is therefore a bespoke 2D node graph driven by GSAP. If a Spline
scene is added later, load it via `@splinetool/react-spline`, code-split, with
the dashboard fully usable before the asset resolves.

**Views.**

1. **System map (centerpiece).** The five stages — Ingest → Candidates → Judge
   → Policy → Executor — as connected nodes. Each node lights up, shows its
   state, and displays real data flowing through it as a run executes, driven
   by the SSE stream. This is the direct answer to "do AI agents work across
   different software, and does it feel seamless" — make it undeniable at a
   glance.
2. **Integration health panel.** Persistent, always-visible strip: Stripe,
   HubSpot, Slack, Lemma, Arga. Backed by `/api/integrations`, which performs a
   genuine reachability probe per integration. The first thing a skeptical
   judge looks at.
3. **Live results table.** Every scenario — entity, discrepancy type, action
   taken, confidence — with GSAP-animated row expansion revealing the judge's
   real reasoning text. Sortable and filterable by action type and confidence.
   A real data tool, not a static list.
4. **Reliability metrics panel.** Recall, precision, decoy false-action rate,
   each with a GSAP count-up from `/api/eval`. The **0/5 decoy result is the
   single most visually unmissable number on the page** — it is the core
   reliability claim and must carry that weight.
5. **Command palette (Cmd+K).** Trigger a run, trigger an eval, jump to any
   scenario, toggle dark/light. Full keyboard navigation.
6. **Live activity feed / toasts.** Real-time toasts from the same SSE stream —
   "Slack approval requested", "Write verified", "Silent failure caught and
   retried". Sourced from real events, never simulated.
7. **Run history.** Past runs and evals, timestamped, so a judge can compare
   multiple real runs. Direct evidence of the consistency the eval claims.

**Engineering bar.** Full TypeScript coverage, a typed API client mirroring the
FastAPI schema, no `any`. Genuine loading, empty and error states for every
view, designed as carefully as the success states — an enterprise tool is
defined as much by how it fails as by how it succeeds. Responsive to mobile
width. Keyboard navigation and visible focus states throughout; sufficient
contrast in dark mode. Fast initial load: code-split anything heavy and keep the
dashboard usable while it resolves.

**Verification before done.** Drive a full click-through with browser
automation — trigger a real run, watch it complete, and assert the displayed
values against the actual API response. This is a self-administered
judge-simulation: catch what a judge would catch, first.

**Deployment.** Frontend to Vercel; `NEXT_PUBLIC_API_URL` points at the deployed
FastAPI service. Confirm the CORS allowlist includes the live Vercel domain, and
re-run the click-through against production URLs, not just localhost, before
calling it done.

## Repo layout (target)

```
agent/        the five stages, plus config/clients/tracing/ui
seed/         scenarios.md (source of truth), spec.py (answer key), seed.py
sim/          local twin simulator standing in for Arga twins
eval/         run_eval.py, report.py, warm_cache.py
api/          FastAPI service (Phase 10)
web/          Next.js dashboard (Phase 9)
main.py       CLI: seed | run | eval
```
