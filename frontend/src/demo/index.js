/* DeepScout static-demo runtime.
 *
 * The console is written against a live FastAPI backend (`../api.js` calls
 * `/api/...`). This module lets the SAME app run as a fully static bundle —
 * a landing page, docs and a chat interface with realistic scripted
 * conversations — with no server, by intercepting `window.fetch` at startup
 * and answering `/api/*` from the fixtures in ./conversations.js and
 * ./sessions.js.
 *
 * Why intercept fetch rather than fork the client:
 *   Every component, the stream parser and the event switch stay byte-for-byte
 *   identical to production. There is no second code path to keep in sync and
 *   no risk of the demo drifting from the real UI — the demo IS the real UI,
 *   pointed at scripted data. (AGENTS.md rule: two views of one thing must be
 *   driven by one signal; a forked demo client would be a second signal.)
 *
 * Activation:
 *   - `import.meta.env.VITE_DEMO === "1"` (build-time, set by the CF Pages
 *     build command), OR
 *   - `?demo=1` / `#demo` in the URL (handy for a real backend deploy that
 *     wants a public no-backend showcase route), OR
 *   - `localStorage.deepscout.demo === "1"`.
 *
 * Everything is wrapped in try/catch: if interception fails for any reason,
 * the app falls back to real fetch and behaves like the normal client. The
 * demo must never be able to brick the app.
 */

import { CONVERSATIONS, findConversation } from "./conversations";
import { DEMO_SESSIONS, demoSessionDetail, demoTrace } from "./sessions";

const DEMO_ACTIVE =
  (typeof import.meta !== "undefined" && import.meta.env && import.meta.env.VITE_DEMO === "1")
  || (typeof window !== "undefined" && (
    new URLSearchParams(window.location.search).get("demo") === "1"
    || window.location.hash.includes("demo")
    || (() => { try { return localStorage.getItem("deepscout.demo") === "1"; } catch { return false; } })()
  ));

export const IS_DEMO = DEMO_ACTIVE;

/* A JSON Response the client's `response.json()` can consume. */
function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/* An NDJSON streaming Response that emits the given frames one per line with
 * the authored delays between them, so the UI renders a live-feeling run.
 * Each frame is stamped with a fresh `ts` at emission (matching the backend's
 * event_line) and honours an AbortSignal so "Stop" works exactly as it does
 * against the real stream. */
/* A multiplier on the authored inter-frame delays. 1 = real demo pacing.
 * Tests set it lower so a full stream replays in milliseconds instead of
 * waiting out ~10s of scripted pauses. Exposed via setDemoSpeed. */
let speed = 1;
export function setDemoSpeed(factor) {
  speed = Number(factor) > 0 ? Number(factor) : 1;
}

function streamResponse(frames, signal) {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      try {
        for (const raw of frames) {
          if (signal?.aborted) break;
          const { delay = 500, ...rest } = raw;
          // ts LAST: the recorded frames carry no ts, but if one ever does it
          // must not win and paint a real run's clock onto this playback.
          const frame = { ...rest, ts: Date.now() };
          await sleep(Math.round(delay * speed), signal);
          if (signal?.aborted) break;
          controller.enqueue(encoder.encode(JSON.stringify(frame) + "\n"));
        }
      } catch (err) {
        /* An aborted read surfaces here; anything else is logged so a broken
         * fixture is visible rather than a silent hang. */
        if (!(err instanceof DOMException && err.name === "AbortError")) {
          console.warn("[demo] stream ended early", err);
        }
      } finally {
        try { controller.close(); } catch { /* already closed */ }
      }
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "application/x-ndjson" },
  });
}

/* Abortable sleep — resolves after `ms`, rejects with an AbortError if the
 * signal fires first, so a Stop during a long inter-frame delay is immediate
 * rather than waiting out the delay. */
function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException("Aborted", "AbortError"));
    const t = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(t);
      reject(new DOMException("Aborted", "AbortError"));
    }, { once: true });
  });
}

/* Pick a script for a query: an exact match, else a keyword-ish match, else
 * the first conversation so an unknown typed question still produces a
 * believable run instead of a blank. The demo is explicitly scripted, so this
 * substitution is disclosed in the run's first progress frame. */
function pickConversation(query) {
  const exact = findConversation(query);
  if (exact) return exact;
  const q = String(query || "").toLowerCase();
  const scored = CONVERSATIONS.map((c) => {
    const words = c.query.toLowerCase().split(/\W+/).filter((w) => w.length > 3);
    const hits = words.filter((w) => q.includes(w)).length;
    return { c, hits };
  }).sort((a, b) => b.hits - a.hits);
  return scored[0].c;
}

/* The API surface the client calls. Each handler returns a Response. */
async function handleApi(pathname, search, init) {
  const method = (init?.method || "GET").toUpperCase();
  const body = init?.body ? safeParse(init.body) : {};

  /* Start a research run — the streaming endpoint. */
  if (pathname === "/api/research/stream" && method === "POST") {
    const convo = pickConversation(body.query);
    const typed = String(body.query || "").trim().toLowerCase();
    const exact = convo.query.trim().toLowerCase() === typed;
    /* An exact question replays its own recording under its own run id, so the
     * streamed run and the sidebar row are one identity. */
    const requestId = exact ? convo.runId : `demo-recording-${Date.now()}`;
    const sessionId = body.session_id || convo.sessionId;
    const frames = convo.frames.map((f, i) => {
      const stamped = { ...f, request_id: requestId, session_id: sessionId };
      /* A typed question that matches no recording is answered by SOME real
       * run. That must be visible in the stream, not hidden: the first line
       * says which recording is playing, so no visitor reads it as a fresh
       * answer to their question. The rest of the frame is left exactly as
       * recorded — only this message is ours. */
      if (i === 0 && !exact && typeof stamped.message === "string") {
        stamped.message = `Demo: no recording for that question — replaying "${truncateForNotice(convo.query)}"`;
      }
      return stamped;
    });
    return streamResponse(frames, init?.signal);
  }

  if (pathname === "/api/sessions" && method === "GET") {
    return jsonResponse({ sessions: DEMO_SESSIONS });
  }

  const sessionMatch = /^\/api\/sessions\/([^/]+)$/.exec(pathname);
  if (sessionMatch && method === "GET") {
    const detail = demoSessionDetail(decodeURIComponent(sessionMatch[1]));
    return detail ? jsonResponse(detail) : jsonResponse({ detail: "Unknown session_id" }, 404);
  }

  const traceMatch = /^\/api\/research\/([^/]+)\/trace$/.exec(pathname);
  if (traceMatch && method === "GET") {
    const trace = demoTrace(decodeURIComponent(traceMatch[1]));
    return trace ? jsonResponse(trace) : jsonResponse({ detail: "Unknown run_id" }, 404);
  }

  /* Plan review (human-in-the-loop): accept and report ok; the demo run is
   * pre-scripted so approving simply lets the recorded stream proceed. */
  if (/^\/api\/research\/([^/]+)\/plan$/.test(pathname) && method === "POST") {
    return jsonResponse({ status: "ok", action: body.action || "approve" });
  }

  /* Resume is a no-op in the demo (nothing ever fails). */
  if (/^\/api\/research\/([^/]+)\/resume$/.test(pathname) && method === "POST") {
    const runId = pathname.split("/")[3];
    const convo = CONVERSATIONS.find((c) => demoTrace(runId)?.query === c.query) || CONVERSATIONS[0];
    return streamResponse(convo.frames, init?.signal);
  }

  /* Providers / chains: the Model Controls view needs *some* data. Return an
   * empty, valid set so the view renders its empty state rather than erroring. */
  if (pathname === "/api/providers" && method === "GET") {
    return jsonResponse({ providers: [], active_id: null, chains: [], active_chain_id: null });
  }
  if (pathname === "/api/provider-chains" && method === "GET") {
    return jsonResponse({ chains: [], active_chain_id: null });
  }

  /* Health — the app may probe it. Report a demo-safe ok. */
  if (pathname === "/api/health") {
    return jsonResponse({ status: "ok", demo: true });
  }

  /* Anything else: a 404 the client's error path already handles, rather than
   * a fabricated success that would mislead a component. */
  return jsonResponse({ detail: `No demo fixture for ${method} ${pathname}` }, 404);
}

/* Keep the disclosure to one readable line. */
function truncateForNotice(text, max = 72) {
  const t = String(text || "");
  return t.length > max ? `${t.slice(0, max - 1)}\u2026` : t;
}

function safeParse(raw) {
  try { return JSON.parse(raw); } catch { return {}; }
}

/* Install the fetch interceptor. Idempotent and guarded — a failure here
 * logs and leaves the real fetch in place. */
export function installDemoFetch() {
  if (!DEMO_ACTIVE || typeof window === "undefined" || window.__deepscoutDemoInstalled) return;
  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    let url;
    try {
      url = typeof input === "string" ? input : input?.url || String(input);
      const parsed = new URL(url, window.location.origin);
      if (parsed.pathname.startsWith("/api/")) {
        return await handleApi(parsed.pathname, parsed.search, init);
      }
    } catch (err) {
      console.warn("[demo] fetch interception failed, using real fetch", err);
    }
    return realFetch(input, init);
  };
  window.__deepscoutDemoInstalled = true;
}
