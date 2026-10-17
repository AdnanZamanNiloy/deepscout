# DeepScout — Static Demo Deployment (Cloudflare Pages)

This folder is **self-contained**: it lets DeepScout's landing page, docs site
and chat console deploy as a **static site with no backend**. The chat
interface replays **real recorded research runs** — no models are called, no
server runs.

Nothing here changes the production app. Demo mode is opt-in at build time.

---

## How demo mode works

The console's API client (`frontend/src/api.js`) calls `/api/...` on the
FastAPI backend. In demo builds, a fetch interceptor
(`frontend/src/demo/index.js`) answers those calls from fixtures:

```text
frontend/src/demo/
  runs.js          GENERATED — five REAL runs, pulled from
                   GET /api/research/{run_id}/trace by
                   bench/make_demo_fixtures.py. Queries, plans, progress
                   lines, critic reviews, confidence, findings and full
                   reports are what the pipeline actually produced. Do not
                   hand-edit; re-run the generator.
  conversations.js Maps those runs to what a typed question streams.
  sessions.js      Maps them to sidebar sessions + replay traces.
  index.js         The fetch interceptor: serves GET fixtures as JSON and
                   POST /api/research/stream as a timed, abortable NDJSON
                   stream.
```

Because the interceptor sits *below* every component, the demo **is** the real
UI — same stream parser, same event switch, same panels — pointed at recorded
data. There is no forked client to drift out of sync.

The fixtures used to be hand-authored literals, and they rotted: their own
header claimed the wire emitted no `findings` frame (it does), the sidebar
listed invented chats, and the citations pointed at `example.org`. Nothing
failed, because a fixture is data — being wrong looks exactly like being right.
They are generated from recordings now, so re-running the generator after a wire
change is all it takes to keep the demo current.

**Activation** (any one enables it):
- `VITE_DEMO=1` at build time (what `build.sh` sets), or
- `?demo=1` / `#demo` in the URL, or
- `localStorage.deepscout.demo = "1"`.

If interception fails for any reason, the app logs and falls back to real
`fetch`, so the demo can never brick the app.

---

## Deploy to Cloudflare Pages

### Option A — connect the repo (recommended)

In the Cloudflare dashboard, create a Pages project from this repository:

| Setting | Value |
| --- | --- |
| Framework preset | None |
| Root directory | `frontend` |
| Build command | `VITE_DEMO=1 npm run build && cp ../deploy/cloudflare-pages/_redirects dist/_redirects && cp ../deploy/cloudflare-pages/_headers dist/_headers` |
| Build output directory | `dist` |

Cloudflare serves the site at the project root. The copied `_redirects` /
`_headers` apply to every response.

### Option B — build locally, upload the output

```bash
bash deploy/cloudflare-pages/build.sh          # produces frontend/dist
npx wrangler pages deploy frontend/dist --project-name deepscout-demo
```

`build.sh` installs deps if needed, builds with `VITE_DEMO=1`, and copies
`_redirects` + `_headers` into `dist/`.

---

## What gets deployed

```text
/                 Landing page (real product copy; links into the console)
/#/workspace      Chat console — sidebar of 4 demo chats, live-streaming runs
/#/missions       Missions view (populated from the local chat history)
/#/agents         Agent team view
/#/model-controls Provider/chain controls (renders its empty state)
/#/docs           Documentation site (iframe → /docs.html)
/docs.html        The docs page itself (real file, not swallowed by fallback)
```

---

## Control files

- **`_redirects`** — SPA fallback. Every unknown path resolves to
  `index.html`, EXCEPT `/docs.html`, which is a real file and must be served
  directly (it has its own in-page hash router). Order matters: the explicit
  `docs.html` rule is listed before the catch-all.
- **`_headers`** — security headers and cache policy. Hashed assets under
  `/assets/*` are immutable; `index.html` and `docs.html` revalidate so a new
  deploy is picked up.

---

## Customizing the demo

**Do not hand-edit the fixtures.** They are generated from real recorded runs,
and hand-editing them is exactly how they went stale before. Re-record instead:

- **Re-record the demo data**: run the generator against a live backend.
  ```bash
  python backend/bench/make_demo_fixtures.py --base http://127.0.0.1:8000
  ```
  It re-reads each featured run's trace and rewrites `frontend/src/demo/runs.js`.
  Pin or reorder runs in `FEATURED_RUNS` at the top of that script
  (`backend/bench/make_demo_fixtures.py`).
- **Bound the payload**: `--claims`, `--sources` and `--replay-seconds` cap the
  claim rows, source rows and playback duration. The real totals stay beside the
  kept rows (`realClaimCount` / `realSourceCount`) and the run's own
  `source_count` is never lowered, so the UI shows the truth and the file stays
  a sane size. Report markdown is kept whole — a truncated report would
  misdescribe a finished run.
- **Record a fresh run to feature**: send a question to a live backend in the
  mode you want, then pin that run id in `FEATURED_RUNS`. At least one featured
  run should come from the current build, or the demo stops showing today's wire
  (older recordings predate fields like `confidence_notes`).
- **Change the starter question**: `DEFAULT_QUERY` / `DEMO_STARTERS` in
  `conversations.js` — these are derived from the recordings, so the "starters"
  are the real questions the demo can answer.
- **Unknown typed queries** are answered with the closest-matching recording
  rather than a blank, so a visitor poking at the composer always sees a
  believable run. The run's first progress line says which recording is playing,
  because that substitution must not pass for a fresh answer.

`frontend/test/demo-real-data.test.js` enforces the above: real UUIDs, no
placeholder hosts, real claim sentences, and a streamed run reporting the same
claim count as its replay.

---

## Notes / limits

- Demo mode is a **showcase**, not the product. Add your own LLM key and run
  the backend (`backend/`, see the repo SETUP.md) for real research.
- The Model Controls view renders its empty state in the demo (no providers
  are configured); it is a real view, not a stub.
- The bundle is ~1MB gzipped for the app plus lazily-loaded diagram chunks
  (Mermaid) — comfortably within Cloudflare Pages' free tier.
