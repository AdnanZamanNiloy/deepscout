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
  /** Provider that returned it: tavily, ddg_text, wikipedia, … */
  source?: string;
  reliability?: number | null;
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

/** Any frame may carry the client's own arrival stamp. */
type Base = { __ts?: number };

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
  budget?: Record<string, unknown>;
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
  budget?: Record<string, unknown>;
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
  return KNOWN.has(frame.type);
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
  meta?: string;
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

  for (const frame of frames) {
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
        // No article before the value: the wire says "analytical", not "an
        // analytical", so `a ${qt}` produced "a analytical question".
        if (qt) bits.push(`${qt} question`);
        if (dom) bits.push(`${dom} domain`);
        if (lvl) bits.push(`${lvl} depth`);
        // No article at all: the wire supplies bare adjectives ("analytical"),
        // so inserting one produces "a analytical".
        const lead = bits.length
          ? `Read this as ${bits.join(", ")}.`
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
              if (!merged.has(url)) {
                merged.set(url, {
                  id: `${existing.id}-chip-${merged.size}`,
                  label: hostOf(url) || str(h.source) || str(h.title) || "source",
                  url,
                  meta: str(h.source),
                });
              }
            }
            existing.chips = [...merged.values()];
            existing.children = existing.chips.length
              ? [{
                  id: `${existing.id}-view`,
                  kind: "tool",
                  title: "View web page",
                  content: existing.chips.map((c) => c.url).join("\n"),
                  status: "completed",
                  timestamp: existing.timestamp,
                  minor: true,
                }]
              : undefined;
            existing.status = "completed";
            break;
          }
        }

        const chips: TraceChip[] = hits.map((h, i) => ({
          id: `${id("chip")}-${i}`,
          label: hostOf(str(h.url)) || str(h.source) || str(h.title) || "source",
          url: str(h.url),
          meta: str(h.source),
        }));

        steps.push({
          id: id("search"), kind: "tool", title: "Search the web",
          content: q ? `Search on the web for "${q}"` : "Searched the web.",
          status: "completed", timestamp: ts, chips, query: q,
          children: hits.length
            ? [{
                id: id("view"), kind: "tool" as StepKind,
                title: "View web page",
                content: hits.map((h) => str(h.url)).join("\n"),
                status: "completed" as StepStatus,
                timestamp: ts,
                minor: true,
              }]
            : undefined,
        });
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
  return typeof v === "string" ? v.trim() : v == null ? "" : String(v);
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Hostname for a chip label: "forbes.com" from a full URL. */
export function hostOf(url: string): string {
  const m = /^[a-z]+:\/\/([^/?#]+)/i.exec(url || "");
  if (!m) return "";
  return m[1].replace(/^www\./i, "");
}

/** "Less steps" keeps milestones and drops intermediate reasoning. */
export function summarize(steps: readonly TraceStep[]): TraceStep[] {
  const keep = steps.filter((s) => !s.minor);
  return keep.length ? keep : steps.slice();
}