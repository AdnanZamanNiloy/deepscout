# DeepScout — Static Demo Deployment (Cloudflare Pages)

This folder is **self-contained**: it lets DeepScout's landing page, docs site
and chat console deploy as a **static site with no backend**. The chat
interface replays four realistic, fully-streamed research conversations from
scripted fixtures — no models are called, no server runs.

Nothing here changes the production app. Demo mode is opt-in at build time.

---

## How demo mode works

The console's API client (`frontend/src/api.js`) calls `/api/...` on the
FastAPI backend. In demo builds, a fetch interceptor
(`frontend/src/demo/index.js`) answers those calls from fixtures:

```text
frontend/src/demo/
  conversations.js   Four scripted runs — ordered NDJSON frames (progress,
                     stage, plan, critic, final_report) with authored delays.
  sessions.js        Sidebar sessions + per-run traces (claims, sources,
                     critic reviews, contradictions) for replay cards.
  index.js           The fetch interceptor: serves GET fixtures as JSON and
                     POST /api/research/stream as a timed, abortable NDJSON
                     stream.
```

Because the interceptor sits *below* every component, the demo **is** the real
UI — same stream parser, same event switch, same panels — pointed at scripted
data. There is no forked client to drift out of sync.

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

- **Edit or add a conversation**: append to `CONVERSATIONS` in
  `frontend/src/demo/conversations.js`. Each entry needs a `query`, a
  `frames` array, and a matching trace/claims entry in
  `frontend/src/demo/sessions.js` (the fixtures are validated to agree on
  `source_count`, and reports should cite claims that appear in that run's
  `claims` — the product verifies claims against sources, so the demo should
  not assert numbers that aren't backed by its own evidence).
- **Change the starter question**: `DEFAULT_QUERY` / `DEMO_STARTERS` in
  `conversations.js`.
- **Unknown typed queries** are answered with the closest-matching script
  rather than a blank, so a visitor poking at the composer always sees a
  believable run.

---

## Notes / limits

- Demo mode is a **showcase**, not the product. Add your own LLM key and run
  the backend (`backend/`, see the repo SETUP.md) for real research.
- The Model Controls view renders its empty state in the demo (no providers
  are configured); it is a real view, not a stub.
- The bundle is ~1MB gzipped for the app plus lazily-loaded diagram chunks
  (Mermaid) — comfortably within Cloudflare Pages' free tier.
