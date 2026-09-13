# reconcile-agent

**Live dashboard:** https://reconcile-agent.vercel.app

An agent that reconciles **Stripe billing** against a **HubSpot CRM**, fixes the
safe discrepancies automatically, routes the consequential ones to **Slack** for
human approval, drafts dunning email in **Gmail** (never sends), and proves its
accuracy against a planted eval suite.

---

## 01 · Project overview

Money arrives in Stripe. Sales tracks deals in HubSpot. The two drift apart
constantly and nobody notices:

- a customer paid, but the deal is still "open" → revenue reported wrong
- the same company exists twice under slightly different names → double-counted
- a card failed and nobody followed up → money silently lost
- someone cancelled in Stripe but CRM still says "active customer" → churn invisible
- Stripe bills $1,500/mo but the CRM deal says $24k/yr (it's really $18k) → forecast wrong
- someone is paying with no CRM record at all

A revenue-ops person does this by exporting both systems and eyeballing them for
hours, monthly. This agent does it end to end in five stages.

```
  ingest ──▶ candidates ──▶ judge ──▶ policy router ──▶ executor
   (SDKs)    (rapidfuzz)    (LLM)     (pure logic)      (write + READ BACK)
                              │
                       the only LLM call
                        in the system
```

| Stage | File | What it does | LLM? |
| --- | --- | --- | --- |
| 1. Ingest | `agent/ingest.py` | Pulls Stripe customers/invoices/subscriptions/refunds and HubSpot companies/deals into Pydantic models | no |
| 2. Candidates | `agent/candidates.py` | Deterministic: fuzzy name/domain matching + direct field comparison | **no** |
| 3. Judge | `agent/judge.py` | One Antigravity CLI subprocess per candidate → parsed into a Pydantic `JudgeResult` at the boundary | yes |
| 4. Policy router | `agent/policy.py` | Assigns Automatic / Slack-approval / Never | no |
| 5. Executor | `agent/executor.py` | Performs the write, then `agent/verify.py` **re-reads the record** to confirm it stuck | no |

No agent framework. The control flow is a straight line you can read top to
bottom in `agent/pipeline.py`.

### Autonomy tiers

- **Automatic** — confidence ≥ 0.9 *and* reversible (move a deal to Closed Won
  with a note; create a dunning task; draft an email).
- **Slack approval** — merging duplicates, changing a deal amount, marking
  churn, creating a CRM company. These stay behind a human **even at confidence 1.00**.
- **Never** — refunds, deletions, outbound customer email. Enforced in
  `agent/policy.py` as a *category exclusion*, not a confidence threshold, so
  there is no number that unlocks them.

### Why an LLM is needed at all

The decoy pair **Acme Inc** / **Acme Labs** scores **100% name similarity** after
normalising legal suffixes — identical to the four genuine duplicates. No
threshold separates them. The distinguishing evidence is that their domains
(`acme.com` vs `acmelabs.io`) differ, and weighing that is a judgement call.

Proof it matters: with the judge stubbed out, the decoy false-action rate is
**1/5**. With the real judge, it is **0/5**.

---

## 02 · External apps used

Seven, of which **five are genuinely live** (HubSpot, Slack, Claude, Lemma, Arga) — comfortably above the "at least three" requirement:

| App | Live? | What the agent does with it |
| --- | --- | --- |
| **HubSpot** | ✅ real | Reads companies/deals; moves deal stages, corrects amounts, flags duplicates, marks churn, creates companies and dunning tasks |
| **Slack** | ✅ real | Posts approval requests to `#approvals` and polls for a ✅ / ❌ **reaction** |
| **Claude Sonnet 4.6 (Thinking)** | ✅ real | The judge — via Antigravity CLI subprocess, no API key, no per-token billing |
| **Lemma** | ✅ real | Tracing — one trace per run, span per stage, judge calls as generations |
| **Arga Labs** | ✅ real | Digital twins of the vendor APIs; catalog queried live |
| **Gmail** | ⚠️ twin | Draft-only dunning email. **There is no send path anywhere in the codebase.** Real-Gmail path is implemented (`scripts/gmail_auth.py`) but the demo account is locked pending a Google appeal, so it runs on the twin |
| **Stripe** | ⚠️ twin | Real Stripe SDK pointed at the Arga twin / local simulator (see below) |

**Honest note on Stripe and Gmail:** both use the official SDK and real API
shapes, but are pointed at a twin rather than a live account. Each is switched
independently by one environment variable (`STRIPE_MODE=real`, `GMAIL_MODE=real`).
The dashboard's integration strip labels every service `real`, `twin` or
`external` — it never claims a simulated service is live, and the header shows
the live count (`4/6 live vendor APIs · simulated: Stripe, Gmail`).

The same is true of the twin backend generally: Arga's free plan allows 10
validation runs/month, which this build exhausted, so `sim/twin_sim.py` re-serves
the exact request/response shapes captured from the live twins.

---

## 03 · Setup instructions

```bash
git clone https://github.com/Jae3y/reconcile-agent
cd reconcile-agent
pip install -r requirements.txt -r requirements-api.txt
cp .env.example .env          # fill in the values
```

### Run the CLI

```bash
python main.py seed     # plant 25 scenarios: 20 real discrepancies + 5 decoys
python main.py run      # run the five-stage pipeline once
python main.py eval     # reseed, run, and score against seed/scenarios.md
```

### Run the dashboard

```bash
python -m uvicorn api.main:app --port 8000     # terminal 1
cd web && npm install && npm run dev           # terminal 2
```

Open http://localhost:3000.

### Per-service real vs twin

Each integration is switched independently in `.env`:

```
HUBSPOT_MODE=real      SLACK_MODE=real
GMAIL_MODE=real        STRIPE_MODE=twin
```

Gmail needs a one-time consent: `python scripts/gmail_auth.py`.

### Inspecting what the agent did

```bash
python scripts/show_slack.py    # read the real #approvals messages back out of Slack
```

This reads messages back through a *different* Slack API call than the one that
wrote them, so it demonstrates the round trip rather than replaying a local log.

### Safety interlocks

- `ARGA_ALLOW_PROVISION=false` — `agent/twins.py` raises `ArgaQuotaLock` rather
  than silently spending one of a limited monthly quota.
- Judge quota errors (`RESOURCE_EXHAUSTED` / 429) are **never retried** —
  retrying doubles load on an exhausted quota.
- Verdicts cache under a *semantic* key that scrubs volatile object ids, so a
  re-run only spends quota on candidates whose facts actually changed.

---

## 04 · Reliability testing

`python main.py eval` reseeds a fresh twin, runs the full pipeline, and scores it
against `seed/spec.py` — the machine-readable form of `seed/scenarios.md`.

**Latest run — every verdict freshly judged, nothing reused from cache:**

| Metric | Result | Target |
| --- | --- | --- |
| Detection recall | **100%** (20/20) | 100% |
| Detection precision | **100%** | 100% |
| F1 | **1.00** | 1.00 |
| **Decoy false-action rate** | **0/5** | 0/5 |
| Tier routing accuracy | **100%** | 100% |
| Post-state correctness | **100%** (19 confirmed by read-back) | 100% |
| Injected fault caught | **yes** | yes |
| Judge failures | **0** | 0 |
| Verdict provenance | **21 fresh, 0 cached** | — |

### The core mechanism: read-back verification

Every write is followed by an **independent re-read** of the record. An action is
only reported as applied once the system of record confirms the new value; on
mismatch the executor retries once, then records a failure.

To prove it, `--inject-fault` wraps one write so it returns success without
changing anything:

```bash
python main.py run --inject-fault "Kestrel Foods"
```

```
Kestrel Foods   ✗ verify failed   expected 'closedwon' but the record still reads 'presentationscheduled'
```

The fake success is caught, retried, and logged honestly instead of counted as a
win. It surfaces in the Lemma trace as `SILENT-FAILURE-CAUGHT`.

### A partial run is reported INVALID, never scored

If any candidate fails to get a verdict, the eval refuses to present detection
metrics and prints **RUN INVALID** instead. An earlier build scored a
quota-starved run as "30% recall" — which read like a quality measurement but was
really measuring upstream availability, since 15 of 21 candidates had never been
judged. A metric computed over a partially-failed run is worse than no metric.

### The judge caught a bug in our own test fixture

The first full eval scored 95%, missing Mesa Robotics. The judge's reasoning:

> "once an invoice is marked `uncollectible` in Stripe, it signals that standard
> collection efforts have been exhausted and the balance has been written off —
> sending a dunning email at this stage would be inappropriate"

That is correct domain reasoning. Category 3 had been seeded with
`mark_uncollectible`, which means *written off* — roughly the opposite of "failed
payment needing follow-up". **The fix went into the fixture, not the prompt.**
Tuning the prompt until the judge agreed with bad data would have hidden the
defect instead of fixing it.

---

## 05 · Demo video

**▶ [Demo video](DEMO_VIDEO_URL_HERE)** — under 2 minutes.

The recording lives in [`demo/`](demo/) along with notes on what each segment
shows and how to reproduce it. Everything in it is a live API response — the
dashboard renders no mock data, so the run in the video can be re-run and the
numbers checked against `GET /api/runs/{run_id}`.

---

## Repo layout

```
agent/        the five stages, plus config/clients/tracing/ui
seed/         scenarios.md (source of truth), spec.py (answer key), seed.py
sim/          local twin simulator standing in for the Arga twins
eval/         run_eval.py, report.py, warm_cache.py
api/          FastAPI service: SSE stream, live integration probes, run history
web/          Next.js dashboard (App Router, TypeScript, Tailwind, GSAP)
scripts/      gmail_auth.py (one-time OAuth consent)
main.py       CLI: seed | run | eval
```

## Notable engineering details

- **SSE, not polling.** `GET /api/stream/{run_id}` pushes each phase as it
  happens, and replays from seq 0 — a judge opening the dashboard mid-run sees
  the whole story, then tails live.
- **HubSpot needs an explicit adapter.** `hubspot-api-client` rebuilds its
  sub-clients lazily, so a `configuration.host` override is *gone* on the next
  attribute access and the call silently goes to production. `agent/clients.py`
  passes the base URL on every request so that fallback is structurally impossible.
- **Windows `SO_REUSEADDR`.** `HTTPServer` defaults `allow_reuse_address = True`,
  and on Windows that lets *two processes bind the same port* — both believed
  they owned the simulator and requests raced. Forced `False`.
- **Zero mock data in the dashboard.** Every rendered value comes from a real API
  response. When the API is unreachable the UI renders an error state rather than
  inventing something plausible.
