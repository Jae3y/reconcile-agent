# Deploying reconcile-agent

Two pieces ship separately: the **FastAPI service** (Phase 10) and the
**Next.js dashboard** (Phase 9). The dashboard is useless without the API, so
deploy the API first and point the frontend at it.

Both are verified working locally. What remains needs *your* credentials — I
can't run an OAuth login or enter account details on your behalf.

---

## 1. Deploy the API

The `Dockerfile` at the repo root builds the service. Any container host works
(Render, Railway, Fly.io, Cloud Run). Example with Render:

1. New → Web Service → connect this repo.
2. Runtime: **Docker**. Health check path: `/api/health`.
3. Set environment variables (copy the values from your local `.env`):

```
LEMMA_API_KEY=...
LEMMA_PROJECT_ID=...
SLACK_BOT_TOKEN=...
HUBSPOT_ACCESS_TOKEN=...
ARGA_LABS_API_KEY=...
ARGA_ALLOW_PROVISION=false
CORS_ORIGINS=https://<your-app>.vercel.app
```

`CORS_ORIGINS` is a comma-separated allowlist. The app additionally allows any
`https://*.vercel.app` origin by regex, so preview deployments work without
re-configuration — but set the explicit production origin anyway.

4. Confirm it is live:

```bash
curl https://<your-api-host>/api/health
curl https://<your-api-host>/api/integrations
```

### Known limitation of a hosted API

The judge is the **Antigravity CLI (`agy`)**, a local binary authenticated
through the Google AI Pro account on your machine. It does not exist inside a
container. A hosted API therefore:

- serves any verdict already in `runs/judge_cache/` (bake the cache into the
  image, or mount it), and
- reports a **judge failure** for anything uncached, which the dashboard
  surfaces as `RUN INVALID` rather than inventing a number.

For a judge clicking through a live demo, the honest options are to run the API
locally (full judging) or to ship it hosted with the cache warmed so the demo
path is fully covered. Either way the UI never fabricates a value.

---

## 2. Deploy the dashboard

```bash
cd web
npx vercel login          # you must do this - interactive
npx vercel link
npx vercel env add NEXT_PUBLIC_API_URL production   # https://<your-api-host>
npx vercel --prod
```

Then confirm CORS end to end from the deployed origin:

```bash
curl -i -H "Origin: https://<your-app>.vercel.app" \
     https://<your-api-host>/api/integrations | grep -i access-control-allow-origin
```

---

## 3. Re-run the click-through against production

The local verification is in this repo's history; repeat it against the live
URLs before calling it done:

1. Open the deployed dashboard.
2. Confirm the integration strip shows real latencies, not zeros.
3. Trigger a run from the header (or ⌘K → "Start pipeline run") and watch the
   system map advance stage by stage over SSE.
4. Expand a results row and check the reasoning text against
   `GET /api/runs/{run_id}` for the same run — they must match verbatim.
5. Check run history lists more than one run.

---

## Local development

```bash
# terminal 1 - API
python -m uvicorn api.main:app --reload --port 8000

# terminal 2 - dashboard
cd web && npm run dev        # reads NEXT_PUBLIC_API_URL from web/.env.local
```
