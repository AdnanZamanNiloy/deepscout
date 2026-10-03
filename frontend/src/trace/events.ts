/**
 * Typed wire-event schema for the pipeline trace.
 *
 * Every field mirrors a REAL event the backend emits on
 * `POST /api/research/stream` (NDJSON). Nothing is invented: if the wire does
 * not carry it, the trace does not show it. A trace that fabricates a
 * plausible-looking search story is worse than no trace.
 *
 * Wire events (measured against the running backend):
 *   progress | intent | route | direct_answer | plan | search_progress
 *   search_query | findings | critic | final_report | error
 *
 * This file is the schema and nothing else. Folding a frame stream into the
 * reader-facing phases lives in `flow.ts`, which imports these types.
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
  /**
   * Present only on the counter frame, which carries no query and reports how
   * many queries have run in total. It is a stat, never a search of its own.
   */
  issued?: number;
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

/** Hostname for a result URL: "forbes.com" from a full URL. */
export function hostOf(url: string): string {
  const m = /^[a-z]+:\/\/([^/?#]+)/i.exec(url || "");
  if (!m) return "";
  return m[1].replace(/^www\./i, "");
}
