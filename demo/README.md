# Demo

Drop the demo video and screenshots here.

## Video

**Put the file at `demo/reconcile-agent-demo.mp4`** (or `.mov` / `.webm`).

Then add the link to the top-level [README](../README.md) — replace
`DEMO_VIDEO_URL_HERE` in section *05 · Demo video*.

### ⚠️ GitHub file-size limit

GitHub rejects any single file over **100 MB**, and warns above 50 MB. A
2-minute screen recording at 1080p is usually 20–60 MB, so it normally fits —
but check first:

```bash
ls -lh demo/
```

**If it is over ~50 MB**, don't commit the file. Upload it to YouTube (unlisted),
Loom or Google Drive and put that link in the README instead. The hackathon asks
for a *link* to a video, not a file in the repo, so hosting it is the safer
option either way.

## What the video covers

Recorded in three windows, roughly 100 seconds total:

| Segment | Window | Point being made |
| --- | --- | --- |
| ~60s | Dashboard (https://reconcile-agent.vercel.app) | Live integration probes; a real run streaming stage by stage over SSE; the judge's actual reasoning; the 0/5 decoy result |
| ~25s | Terminal — `python main.py run --inject-fault "Kestrel Foods"` | Read-back verification catching a write that *reported* success but changed nothing |
| ~15s | Slack `#approvals` | Destructive actions wait for a human reaction; each request carries the judge's confidence and the real HubSpot record IDs |

## Screenshots

`demo/screenshots/` — optional stills for the submission write-up. Useful ones:

- the integration health strip showing live latencies
- the system map mid-run with a stage lit up
- the **0/5** decoy false-action panel
- an expanded results row showing judge reasoning + the read-back line
- the terminal output with `✗ verify failed`

## Reproducing what the video shows

Nothing in the video is staged — every number comes from a live API response.
To reproduce it:

```bash
python main.py seed                                  # plant the 25 scenarios
python main.py eval                                  # score: 100% recall, 0/5 decoys
python main.py run --inject-fault "Kestrel Foods"    # watch the read-back catch a fake success
python scripts/show_slack.py                         # read the approvals back out of Slack
```
