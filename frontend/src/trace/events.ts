/**
 * Typed pipeline-trace event model.
 *
 * Every field mirrors a REAL event the backend emits on
 * `POST /api/research/stream` (NDJSON). Nothing is invented: if the wire does
 * not carry it, the trace does not show it. A trace that fabricates a
 * plausible-looking search story is worse than no trace.
 *
 * Wire events (measured against the running backend):
 *   progress | intent | route | direct_answer | plan | search_progress
 *   search_query | findings | critic | final_report | error
 */

/** One retrieved document, as it appears inside `search_query.results`. */
export type SearchHit = {
  title: string;
  url: string;
  /**
   * Source label: the publisher's site (e.g. "weforum.org"), NOT the index that
   * found it. Historically this carried the legacy composite provider string
   * "searxng:google cse", which is why retrieval engines appeared as source
   * names; `sourceLabelOf` refuses that shape.
   */
  source?: string;
  /** Canonical hostname of the page itself. Preferred over `source`. */
  domain?: string;
  /** Publication/organisation name, when the provider stated one. */
  publisher?: string;
  /** Retrieval provenance: the upstream index, e.g. "google cse". Never a source. */
  via?: string;
  /** The service queried: searxng, wikipedia, arxiv, crossref. */
  retrievalProvider?: string;
  reliability?: number | null;
  /** True when the pipeline actually opened this page, not just listed it. */
  fetched?: boolean;
};

/** One extracted claim, as it appears inside `findings.items`. */
export type Finding = {
  claim: string;
  source: string;
  verified?: boolean | null;
  verification_score?: number | null;
  verification_reason?: string | null;
  agent?: string;
  confidence?: number | null;
};

/** Any frame carries the server's emission stamp (`ts`, epoch ms). The client
 *  additionally stamps its own arrival time as `__ts`, which is more accurate
 *  for a live run; `__ts` is absent from persisted frames because it is added
 *  in the browser, so `ts` is what makes a restored session measurable. */
type Base = { __ts?: number; ts?: number };

export type ProgressEvent = Base & {
  type: "progress";
  message?: string;
  request_id?: string;
  session_id?: string;
};

export type IntentEvent = Base & {
  type: "intent";
  query_type?: string;
  domain?: string;
  explanation_level?: string;
  ambiguity?: boolean;
  senses?: unknown[];
  interpretations?: unknown[];
  underspecified?: boolean;
  recommended_action?: string;
  origin?: string;
};

export type RouteEvent = Base & {
  type: "route";
  path?: string;
  reason?: string;
  confidence?: number;
  origin?: string;
  signals?: Record<string, unknown>;
};

export type DirectAnswerEvent = Base & {
  type: "direct_answer";
  answer?: string;
  confidence?: number;
  self_confidence?: number;
  reason?: string;
  kind?: string;
};

export type PlanEvent = Base & {
  type: "plan";
  items?: string[];
  orchestration?: Record<string, unknown>;
  waves?: unknown[];
};

export type SearchProgressEvent = Base & {
  type: "search_progress";
  snippets?: number;
};

export type SearchQueryEvent = Base & {
  type: "search_query";
  query?: string;
  results?: SearchHit[];
  total_snippets?: number;
};

export type FindingsEvent = Base & {
  type: "findings";
  items?: Finding[];
  verified_update?: boolean;
};

export type CriticEvent = Base & {
  type: "critic";
  iteration?: number;
  reason?: string;
  breakdown?: Record<string, unknown>;
  redteam?: Record<string, unknown>;
};

export type FinalReportEvent = Base & {
  type: "final_report";
  report?: string;
  confidence?: number;
  degraded?: string[];
  audit_markdown?: string;
  answer_support?: number | null;
  citation_health?: Record<string, unknown>;
  capabilities?: Record<string, unknown>;
};

export type ErrorEvent = Base & {
  type: "error";
  message?: string;
};

/** Every event type this build understands. Discriminated on `type`. */
export type WireEvent =
  | ProgressEvent
  | IntentEvent
  | RouteEvent
  | DirectAnswerEvent
  | PlanEvent
  | SearchProgressEvent
  | SearchQueryEvent
  | FindingsEvent
  | CriticEvent
  | FinalReportEvent
  | ErrorEvent;

/**
 * A frame as it arrives: a known event, or something newer than this build.
 *
 * Kept OUT of the union on purpose — a `{ type: string }` member would defeat
 * discriminated-union narrowing and collapse every field access to `unknown`,
 * which is exactly the bug this shape avoids.
 */
export type WireFrame = WireEvent | (Base & { type: string; [k: string]: unknown });

const KNOWN: ReadonlySet<string> = new Set<WireEvent["type"]>([
  "progress",
  "intent",
  "route",
  "direct_answer",
  "plan",
  "search_progress",
  "search_query",
  "findings",
  "critic",
  "final_report",
  "error",
]);

/** Narrow an arbitrary frame to a known event, or reject it. */
export function isKnownFrame(frame: WireFrame): frame is WireEvent {
  // A malformed frame (null, a number, a bare string) reaches here off the
  // wire. Reading `.type` off it threw and took the entire trace down, so a
  // single bad frame blanked the message — guard before dereferencing.
  if (frame === null || typeof frame !== "object") return false;
  const t = (frame as { type?: unknown }).type;
  return typeof t === "string" && KNOWN.has(t as WireEvent["type"]);
}

// ---------------------------------------------------------------------------
// Timeline model
// ---------------------------------------------------------------------------

export type StepStatus = "pending" | "running" | "completed" | "error";

export type StepKind =
  | "thinking"  // reasoning prose
  | "todo"      // the research plan / to-do list
  | "tool"      // a search or a page view
  | "evidence"  // extracted claims
  | "gate"      // critic / verification
  | "final";    // the answer

/** A rendered chip: a search result or a fetched page. */
export type TraceChip = {
  id: string;
  label: string;
  url: string;
  /** The source label: publisher or domain. */
  meta?: string;
  /** Retrieval provenance, rendered as a muted "via <engine>". Never a source. */
  via?: string;
  /** Page was actually opened during the run. */
  fetched?: boolean;
};

export type TraceStep = {
  id: string;
  kind: StepKind;
  /** Short label shown in the rail, e.g. "Search the web". */
  title: string;
  /** One-line body. Long bodies stay expandable. */
  content: string;
  status: StepStatus;
  /** ms since epoch — the frame's arrival, not render time. */
  timestamp: number;
  chips?: TraceChip[];
  /** Sub-steps: a to-do item under a search, a claim under a gate. */
  children?: TraceStep[];
  /** True when this is intermediate reasoning that "Less steps" hides. */
  minor?: boolean;
  /** The query this step is about, when it is a search. */
  query?: string;
  error?: string;
};

/** Reduce the accumulated wire frames into the timeline. */
export function buildTrace(
  frames: readonly WireFrame[],
  opts: { now?: () => number } = {},
): TraceStep[] {
  const now = opts.now ?? (() => Date.now());
  const steps: TraceStep[] = [];
  let n = 0;
  const id = (p: string) => `${p}-${n++}`;

  // Tolerate a missing/non-array prop rather than throwing mid-render: a
  // throw here unmounts the whole message list behind the error boundary.
  const list = Array.isArray(frames) ? frames : [];

  for (const frame of list) {
    // Forward compatibility: an event type this build does not know is
    // skipped, not rendered as noise.
    if (!isKnownFrame(frame)) continue;
    const ev = frame;
    const t = ev.type;
    const ts = typeof ev.__ts === "number" ? ev.__ts : now();

    switch (t) {
      case "progress": {
        const msg = str(ev.message);
        if (msg) {
          steps.push({
            id: id("progress"), kind: "thinking", title: "Run started",
            content: msg, status: "completed", timestamp: ts, minor: true,
          });
        }
        break;
      }

      case "intent": {
        // Reasoning prose assembled from the REAL classification fields.
        const bits: string[] = [];
        const qt = str(ev.query_type);
        const dom = str(ev.domain);
        const lvl = str(ev.explanation_level);
        if (qt) bits.push(`${/^[aeiou]/i.test(qt) ? "an" : "a"} ${qt} question`);
        if (dom) bits.push(`in the ${dom} domain`);
        if (lvl) bits.push(`answered at a ${lvl} level`);
        const lead = bits.length
          ? `Treating this as ${bits.join(", ")}.`
          : "Classified the question.";
        const extra: string[] = [];
        if (ev.ambiguity === true) {
          extra.push("The wording is ambiguous, so more than one reading was considered.");
        }
        if (ev.underspecified === true) {
          extra.push("The question is underspecified; the answer states what it assumes.");
        }
        const senses = Array.isArray(ev.senses) ? ev.senses.length : 0;
        if (senses > 1) extra.push(`Found ${senses} plausible readings.`);

        steps.push({
          id: id("intent"), kind: "thinking",
          title: "Understand the question",
          content: [lead, ...extra].join(" "),
          status: "completed", timestamp: ts, minor: true,
        });
        break;
      }

      case "route": {
        const path = str(ev.path) || "research";
        const reason = str(ev.reason);
        steps.push({
          id: id("route"), kind: "gate", title: "Choose how to answer",
          content: reason ? `Routed to ${path} — ${reason}` : `Routed to ${path}.`,
          status: "completed", timestamp: ts, minor: true,
        });
        break;
      }

      case "plan": {
        const items = (ev.items ?? []).map(str).filter(Boolean);
        steps.push({
          id: id("plan"), kind: "todo", title: "Update to-do list",
          content: items.length
            ? `${items.length} line${items.length === 1 ? "" : "s"} of investigation planned.`
            : "Planned the investigation.",
          status: items.length ? "completed" : "running",
          timestamp: ts,
          children: items.map((q, i) => ({
            id: `${id("plan-item")}-${i}`,
            kind: "todo" as StepKind,
            title: q,
            content: "",
            status: "pending" as StepStatus,
            timestamp: ts,
            minor: true,
          })),
        });
        break;
      }

      case "search_progress": {
        const count = num(ev.snippets);
        // An aggregate indicator, shown until real per-query detail arrives.
        if (count != null) {
          steps.push({
            id: id("searching"), kind: "tool", title: "Gathering sources",
            content: `${count} source${count === 1 ? "" : "s"} retrieved so far.`,
            status: "running", timestamp: ts, minor: true,
          });
        }
        break;
      }

      case "search_query": {
        const q = str(ev.query);
        const hits = (ev.results ?? []).filter((h) => h && str(h.url));

        // Real per-query detail supersedes the aggregate counter.
        const agg = steps.findIndex(
          (s) => s.id.startsWith("searching-") && s.status === "running",
        );
        if (agg >= 0) steps.splice(agg, 1);

        // The backend re-emits a query as its results arrive, so the SAME query
        // appears more than once. Merge into the existing step: one search is
        // one step, gaining its chips over time. Appending instead would show
        // 22 "Search the web" rows for 6 real searches.
        if (q) {
          const existingIdx = steps.findIndex(
            (s) => s.kind === "tool" && s.query === q && !s.error,
          );
          if (existingIdx >= 0) {
            const existing = steps[existingIdx];
            const merged = new Map(
              (existing.chips ?? []).map((c) => [c.url, c]),
            );
            for (const h of hits) {
              const url = str(h.url);
              const prev = merged.get(url);
              if (prev) {
                // The same URL can arrive twice — once bare, once with its
                // content fetched. Keep the richer version.
                if (!prev.label || prev.label === "source") prev.label = labelOf(h, url);
                // Likewise for identity/provenance: a later frame that carries
                // a real domain or engine should not be discarded in favour of
                // an earlier, emptier one.
                if (!prev.meta) prev.meta = sourceLabelOf(h, url);
                if (!prev.via) prev.via = provenanceOf(h);
                if (h.fetched) prev.fetched = true;
                continue;
              }
              merged.set(url, {
                id: `${existing.id}-chip-${merged.size}`,
                label: labelOf(h, url),
                url,
                meta: sourceLabelOf(h, url),
                via: provenanceOf(h),
                fetched: h.fetched === true,
              });
            }
            existing.chips = [...merged.values()];
            syncFetchedChild(existing);
            existing.status = "completed";
            break;
          }
        }

        const chips: TraceChip[] = hits.map((h, i) => ({
          id: `${id("chip")}-${i}`,
          label: labelOf(h, str(h.url)),
          url: str(h.url),
          meta: sourceLabelOf(h, str(h.url)),
          via: provenanceOf(h),
          fetched: h.fetched === true,
        }));

        const step: TraceStep = {
          id: id("search"), kind: "tool",
          // The query belongs in the label, matching the reference's
          // `Search on the web for "…"` rows, rather than in a separate line.
          title: q ? `Search on the web for "${q}"` : "Search the web",
          content: "",
          status: "completed", timestamp: ts, chips, query: q,
        };
        syncFetchedChild(step);
        steps.push(step);
        break;
      }

      case "findings": {
        const items = (ev.items ?? []).filter((f) => f && str(f.claim));
        if (!items.length) break;
        const verified = ev.verified_update === true;
        steps.push({
          id: id(verified ? "verified" : "findings"),
          kind: "evidence",
          title: verified ? "Verify extracted claims" : "Extract claims",
          content: items.map((f) => str(f.claim)).join("\n"),
          status: "completed", timestamp: ts, minor: !verified,
          children: items.slice(0, 12).map((f, i) => ({
            id: `${id("claim")}-${i}`,
            kind: "evidence" as StepKind,
            title: str(f.source) || "unsourced",
            content: str(f.claim),
            status: (f.verified === true ? "completed" : "pending") as StepStatus,
            timestamp: ts,
            minor: true,
          })),
        });
        break;
      }

      case "critic": {
        const it = num(ev.iteration);
        const reason = str(ev.reason);
        steps.push({
          id: id("critic"), kind: "gate", title: "Check the evidence",
          content: [it != null ? `Review round ${it}.` : "Reviewed the evidence.", reason]
            .filter(Boolean)
            .join(" "),
          status: "completed", timestamp: ts, minor: true,
        });
        break;
      }

      case "direct_answer": {
        steps.push({
          id: id("direct"), kind: "final",
          title: "Answer from existing knowledge",
          content: str(ev.answer) || "Answered without searching.",
          status: "completed", timestamp: ts,
        });
        break;
      }

      case "final_report": {
        steps.push({
          id: id("final"), kind: "final", title: "Write the answer",
          content: str(ev.report) || "Final answer.",
          status: "completed", timestamp: ts,
        });
        break;
      }

      case "error": {
        const msg = str(ev.message) || "The run failed.";
        // Attach to the last running step, so the failure reads as "this step
        // broke" rather than as a separate orphan row.
        const running = [...steps].reverse().find((s) => s.status === "running");
        if (running) {
          running.status = "error";
          running.error = msg;
        } else {
          steps.push({
            id: id("error"), kind: "gate", title: "Run failed",
            content: msg, status: "error", timestamp: ts,
          });
        }
        break;
      }

      default:
        // Unreachable: isKnownFrame already excluded unknown types.
        break;
    }
  }

  return steps;
}

// --- helpers ---------------------------------------------------------------

function str(v: unknown): string {
  if (typeof v === "string") return v.trim();
  if (v == null) return "";
  // Never surface the JS "[object Object]" placeholder. An error frame whose
  // `message` arrived as a structured object (or a validation-detail list) must
  // render as readable text, not as "[object Object]" — which is exactly what a
  // user saw as the entire failure message.
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    if (typeof o.message === "string") return o.message.trim();
    if (typeof o.msg === "string") return o.msg.trim();
    if (typeof o.detail !== "undefined") return str(o.detail);
    if (Array.isArray(v)) {
      return v.map((x) => str(x)).filter(Boolean).join("; ");
    }
    try {
      return JSON.stringify(v);
    } catch {
      return "";
    }
  }
  return String(v);
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/**
 * The source label for a hit: who PUBLISHED it, never which engine found it.
 *
 * The order matters. `domain` first, then `publisher`, then the hostname parsed
 * from the URL, and only then the raw `source` field — and even then it is
 * rejected if it has the legacy composite-provider shape ("searxng:google cse").
 * That guard is what makes already-persisted runs render correctly: their
 * recorded frames still carry the engine string in `source`, and without it a
 * replayed run would show the old mislabelling forever.
 *
 * The URL is always present on a search hit, so in practice the hostname is the
 * reliable floor and the engine can never surface as a publisher.
 */
const LEGACY_COMPOSITE_PROVIDER_RE = /^[a-z0-9_-]+:\s*\S/i;

export function sourceLabelOf(h: SearchHit, url: string): string {
  const domain = str(h.domain).trim().toLowerCase();
  if (domain) return domain;
  const publisher = str(h.publisher).trim();
  if (publisher && !LEGACY_COMPOSITE_PROVIDER_RE.test(publisher)) return publisher;
  const host = hostOf(url);
  if (host) return host;
  const source = str(h.source).trim();
  if (source && !LEGACY_COMPOSITE_PROVIDER_RE.test(source)) return source;
  return "";
}

/** Retrieval provenance: the engine that returned this hit, for "via …". */
export function provenanceOf(h: SearchHit): string {
  return str(h.via).trim();
}

/**
 * Chip label: the page's own title, which is what a reader scans for. The
 * hostname is a fallback for the rare result with no title — a bare domain is
 * far more informative than "Global Energy Review 2026".
 */
function labelOf(h: SearchHit, url: string): string {
  return str(h.title) || sourceLabelOf(h, url) || "source";
}

/**
 * "View web page" child row — the pages the run actually opened.
 *
 * Distinct from the search's own chips: those are everything a query returned,
 * these are the subset whose content was fetched and read. Built only from the
 * wire's `is_content_fetched`, so an unfetched result never appears here.
 */
function syncFetchedChild(step: TraceStep): void {
  const chips = step.chips ?? [];
  const opened = chips.filter((c) => c.fetched);
  // Suppressed unless it is a strict subset. When every result was opened the
  // row would simply repeat the chips above it, and a row that adds no
  // information is noise. This pipeline fetches the top few per query, so the
  // row appears exactly when opening a page told the reader something new.
  if (!opened.length || opened.length === chips.length) {
    step.children = undefined;
    return;
  }
  const viewId = `${step.id}-view`;
  const existingChild = step.children?.find((c) => c.id === viewId);
  const child: TraceStep = {
    id: viewId,
    kind: "tool",
    title: "View web page",
    content: "",
    status: "completed",
    timestamp: existingChild?.timestamp ?? step.timestamp,
    minor: true,
    chips: opened.map((c, i) => ({ ...c, id: `${viewId}-chip-${i}` })),
  };
  const others = (step.children ?? []).filter((c) => c.id !== viewId);
  step.children = [child, ...others];
}

/** Hostname for a chip label: "forbes.com" from a full URL. */
export function hostOf(url: string): string {
  const m = /^[a-z]+:\/\/([^/?#]+)/i.exec(url || "");
  if (!m) return "";
  // Authority is `[userinfo@]host[:port]`. Everything but the host has to come
  // off: `user:pw@example.com:8443` and `example.com` are the same source, and
  // rendering the raw authority as a source label shows credentials and ports
  // to the reader. Lowercased too, so Example.COM and example.com are one source.
  const authority = m[1];
  const hostOnly = authority.slice(authority.lastIndexOf("@") + 1).split(":")[0];
  return hostOnly.replace(/^www\./i, "").toLowerCase();
}

/** The agent stage a run is in, derived from the frames themselves. */
export type TraceStage = {
  id: string;
  label: string;
};

/**
 * Which stage the pipeline is in, read off the most recent frame.
 *
 * Derived strictly from real event types — no timers, no guessing — so the
 * label cannot claim work that did not happen. Scans backwards for the last
 * frame the build understands and returns null for an empty or wholly
 * unrecognised stream, rather than defaulting to a reassuring "Thinking".
 */
export function currentStage(frames: readonly WireFrame[]): TraceStage | null {
  if (!Array.isArray(frames)) return null;
  for (let i = frames.length - 1; i >= 0; i -= 1) {
    const frame = frames[i];
    if (!frame || !isKnownFrame(frame)) continue;
    switch (frame.type) {
      case "progress":
      case "intent":
        return { id: "thinking", label: "Thinking" };
      case "route":
      case "plan":
        return { id: "planning", label: "Planning the research" };
      case "search_progress":
      case "search_query":
        return { id: "searching", label: "Searching sources" };
      case "findings":
        return { id: "reading", label: "Reading sources" };
      case "critic":
        return { id: "critiquing", label: "Checking the evidence" };
      case "direct_answer":
      case "final_report":
        return { id: "writing", label: "Writing the answer" };
      case "error":
        return { id: "stopped", label: "Stopped" };
    }
  }
  return null;
}

/** "Less steps" keeps milestones and drops intermediate reasoning. */
export function summarize(steps: readonly TraceStep[]): TraceStep[] {
  const keep = steps.filter((s) => !s.minor);
  return keep.length ? keep : steps.slice();
}