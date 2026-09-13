# Live demo — URLs and how to keep it up

## The links

| What | URL |
|---|---|
| **Dashboard (public)** | https://reconcile-agent-3mxuhuttu-jackson-abetianbes-projects.vercel.app |
| API (via tunnel) | https://secretary-great-swimming-molecules.trycloudflare.com |
| Vercel project | https://vercel.com/jackson-abetianbes-projects/reconcile-agent |

Vercel Authentication is **disabled** on this project, so a judge can open the
dashboard without logging in. Verified: `HTTP 200` from an unauthenticated
request, and CORS confirmed (`Access-Control-Allow-Origin` echoes the Vercel
origin).

## What has to be running

The dashboard is on Vercel and always up. The **API runs on this laptop**, so
three things must be alive for the demo to work:

1. **FastAPI** — `python -m uvicorn api.main:app --port 8000`
2. **Cloudflare tunnel** — gives the API a public URL
3. **The laptop itself**, awake and online

If any of those stop, the dashboard loads but shows its "API unreachable" error
state (by design — it never invents data).

## Restarting after a reboot

```bash
python -m uvicorn api.main:app --port 8000
```

```bash
"/c/Program Files (x86)/cloudflared/cloudflared.exe" tunnel --url http://127.0.0.1:8000
```

**The tunnel URL changes every restart.** A quick `trycloudflare` tunnel gets a
new random hostname each time, so after restarting you must re-point Vercel:

```bash
cd web
npx vercel deploy --prod --yes \
  --build-env NEXT_PUBLIC_API_URL="<new tunnel url>" \
  --env NEXT_PUBLIC_API_URL="<new tunnel url>"
```

To avoid that, create a named tunnel on a Cloudflare account — the hostname is
then stable and this step disappears.

## Latest eval — fully fresh

| Metric | Result | Target |
|---|---|---|
| Detection recall | 100% (20/20) | 100% |
| Detection precision | 100% | 100% |
| F1 | 1.00 | 1.00 |
| **Decoy false-action rate** | **0/5** | 0/5 |
| Tier routing accuracy | 100% | 100% |
| Post-state correctness | 100% (19 read-back confirmed) | 100% |
| Injected fault caught | yes (Kestrel Foods) | yes |
| Judge failures | 0 | 0 |
| **Verdict provenance** | **21 fresh, 0 cached** | — |

Every verdict in that run was a live Claude Sonnet 4.6 call. Nothing reused.

## What is genuinely external

| System | Real? | Evidence |
|---|---|---|
| Claude Sonnet 4.6 (judge) | **yes** | live `agy` CLI calls; quota limits were hit repeatedly |
| Lemma | **yes** | real calls to `api.uselemma.ai`, HTTP 201 |
| Arga Labs | **yes** | real calls to `api.argalabs.com` (~2s latency in the health strip) |
| Stripe / HubSpot / Slack / Gmail | simulator | real SDK/REST code pointed at `sim/twin_sim.py` |

The four simulated ones use genuine integration code — official Stripe SDK,
official Slack SDK, real HubSpot REST — pointed at a local stand-in because the
Arga free-plan quota (10 runs/month) is exhausted. Switching back is one
environment variable once quota resets.

The latency difference in the health strip is the honest tell: ~30ms for local,
~2s for the genuinely external ones.
