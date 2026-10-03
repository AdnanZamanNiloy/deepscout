/**
 * Raw pipeline events → a small number of high-level phases.
 *
 * The wire emits one frame per low-level action: 16 aggregate `search_progress`
 * ticks, 25 `search_query` frames, 6 `findings` batches, 3 `critic` rounds. That
 * is the right granularity for a debugger and the wrong granularity for a
 * reader — shown flat it is ~41 rows of noise that buries the actual story.
 *
 * So this module folds the stream into phases the way a person would describe
 * the work afterwards ("understood the question, planned three angles, searched
 * the web across 22 queries, pulled N claims out of M sources, reviewed the
 * evidence three times, wrote the answer"), and keeps every underlying frame
 * reachable inside the phase it belongs to.
 *
 * Two rules govern everything here:
 *
 *  1. **Nothing is invented.** Every title, count and summary is derived from
 *     fields that arrived on the wire. A phase with no events does not exist.
 *  2. **Nothing is dropped.** Folding is a change of presentation, not of data:
 *     every claim, query, result URL and review round survives as a detail
 *     inside its phase, and `flattenRawSteps` still exposes the frame stream
 *     verbatim for debugging.
 *
 * Pure: same events in, same phases out. No clock, no randomness, no I/O —
 * which is what makes the transform testable without a running backend.
 */

import {
  hostOf,
  type Finding,
  type SearchHit,
  type WireFrame,
} from "./events.ts";

// ---------------------------------------------------------------------------
// Model
// ---------------------------------------------------------------------------

export type PhaseStatus = "running" | "completed" | "error";

export type PhaseKind =
  | "understand"
  | "plan"
  | "research"
  | "evidence"
  | "review"
  | "answer"
  | "error";

/** One search: the query as asked, and what came back. */
export type QueryDetail = {
  kind: "query";
  id: string;
  query: string;
  hits: SearchHit[];
  /** Distinct hosts, most frequent first — the "top domains" chips. */
  domains: { label: string; url: string; count: number }[];
};

/** A batch of extracted claims. */
export type ClaimsDetail = {
  kind: "claims";
  id: string;
  claims: Finding[];
  /** True when the wire flagged this batch as a verification pass. */
  verified: boolean;
};

/** One critic round. */
export type RoundDetail = {
  kind: "round";
  id: string;
  round: number | null;
  reason: string;
};

/** The planned angles. */
export type TodoDetail = {
  kind: "todo";
  id: string;
  items: string[];
};

/** Free-form reasoning derived from real classification fields. */
export type TextDetail = {
  kind: "text";
  id: string;
  label: string;
  body: string;
};

/** The final answer plus the headline numbers from the audit. */
export type ReportDetail = {
  kind: "report";
  id: string;
  report: string;
  confidence: number | null;
  degraded: string[];
};

export type PhaseDetail =
  | QueryDetail
  | ClaimsDetail
  | RoundDetail
  | TodoDetail
  | TextDetail
  | ReportDetail;

/** Counts a phase headline can quote. Absent keys are not shown at all. */
export type PhaseStats = {
  angles?: number;
  queries?: number;
  issued?: number;
  domains?: number;
  snippets?: number;
  claims?: number;
  /** Distinct hosts behind the claims. */
  sources?: number;
  verified?: number;
  rounds?: number;
  confidence?: number;
};

export type HighLevelStep = {
  id: string;
  kind: PhaseKind;
  title: string;
  /** One line, always. */
  summary: string;
  status: PhaseStatus;
  /** Arrival stamp of the first frame in the phase. */
  startedAt: number;
  /** Arrival stamp of the last one — drives the duration readout. */
  endedAt: number;
  stats: PhaseStats;
  details: PhaseDetail[];
  error?: string;
};

// ---------------------------------------------------------------------------
// Accumulator
// ---------------------------------------------------------------------------

type Acc = {
  kind: PhaseKind;
  startedAt: number;
  endedAt: number;
  texts: TextDetail[];
  queries: Map<string, QueryDetail>;
  /** Highest `search_progress.snippets` seen — the counter is monotone. */
  snippets: number;
  /** Highest `issued` count reported by the backend. */
  issued: number;
  claims: Map<string, Finding>;
  sawVerifiedBatch: boolean;
  rounds: RoundDetail[];
  todos: TodoDetail | null;
  report: ReportDetail | null;
  answer: string | null;
  error?: string;
};

// ---------------------------------------------------------------------------
// Transform
// ---------------------------------------------------------------------------

export type TransformOptions = {
  /** Timestamp fallback for frames that carry no `__ts`. */
  now?: () => number;
  /**
   * When the run is over, no phase is left "running". The component passes
   * `status !== "running"`; the default keeps the transform usable on a partial
   * stream without having to say so.
   */
  final?: boolean;
};

/**
 * Fold a raw frame stream into high-level phases.
 *
 * Phases appear in the order their first event arrived, so the visual order is
 * the real execution order rather than an imposed template.
 */
export function transformToDeerFlowSteps(
  rawEvents: readonly WireFrame[],
  opts: TransformOptions = {},
): HighLevelStep[] {
  const now = opts.now ?? (() => Date.now());
  const order: PhaseKind[] = [];
  const accs = new Map<PhaseKind, Acc>();

  const accFor = (kind: PhaseKind, ts: number): Acc => {
    let a = accs.get(kind);
    if (!a) {
      a = {
        kind,
        startedAt: ts,
        endedAt: ts,
        texts: [],
        queries: new Map(),
        snippets: 0,
        issued: 0,
        claims: new Map(),
        sawVerifiedBatch: false,
        rounds: [],
        todos: null,
        report: null,
        answer: null,
      };
      accs.set(kind, a);
      order.push(kind);
    }
    if (ts < a.startedAt) a.startedAt = ts;
    if (ts > a.endedAt) a.endedAt = ts;
    return a;
  };

  for (const frame of rawEvents) {
    if (!frame || typeof frame.type !== "string") continue;
    const raw = frame as unknown as Record<string, unknown>;
    const ts = typeof raw.__ts === "number" && Number.isFinite(raw.__ts) ? raw.__ts : now();

    switch (raw.type) {
      // -- Understanding -----------------------------------------------------
      case "intent": {
        const a = accFor("understand", ts);
        a.texts.push({
          kind: "text",
          id: `intent-${a.texts.length}`,
          label: "Reading",
          body: intentProse(raw),
        });
        break;
      }
      case "route": {
        const a = accFor("understand", ts);
        const path = str(raw.path) || "research";
        const reason = str(raw.reason);
        a.texts.push({
          kind: "text",
          id: `route-${a.texts.length}`,
          label: "Routing",
          body: reason ? `Chose the ${path} path — ${reason}` : `Chose the ${path} path.`,
        });
        break;
      }

      // -- Planning ----------------------------------------------------------
      case "plan": {
        const a = accFor("plan", ts);
        const items = arr(raw.items).map(str).filter(Boolean);
        // The wire re-emits the plan as it is revised; the latest wins so the
        // phase shows the plan actually in force, not a superseded draft.
        a.todos = { kind: "todo", id: "plan", items };
        break;
      }

      // -- Research ----------------------------------------------------------
      case "search_progress": {
        const a = accFor("research", ts);
        const n = typeof raw.snippets === "number" && Number.isFinite(raw.snippets) ? raw.snippets : 0;
        // A repeated tick with a lower count is a stale snapshot, not progress.
        if (n > a.snippets) a.snippets = n;
        break;
      }
      case "search_query": {
        const a = accFor("research", ts);
        const issued =
          typeof raw.issued === "number" && Number.isFinite(raw.issued) ? raw.issued : 0;
        if (issued > a.issued) a.issued = issued;
        const query = str(raw.query);
        // The counter frame carries no query text — it only reports how many
        // queries ran, which is a stat, not a step of its own.
        if (!query) break;
        const hits = arr(raw.results).filter(
          (h): h is SearchHit =>
            !!h && typeof h === "object" && !!str((h as SearchHit).url),
        );
        const prev = a.queries.get(query);
        const seen = new Set((prev?.hits ?? []).map((h) => str(h.url)));
        const merged = [...(prev?.hits ?? [])];
        for (const h of hits) {
          const url = str(h.url);
          if (seen.has(url)) continue;
          seen.add(url);
          merged.push(h);
        }
        a.queries.set(query, {
          kind: "query",
          id: `q-${query}`,
          query,
          hits: merged,
          domains: domainsOf(merged),
        });
        break;
      }

      // -- Evidence ----------------------------------------------------------
      case "findings": {
        const a = accFor("evidence", ts);
        const items = arr(raw.items).filter(
          (f): f is Finding =>
            !!f && typeof f === "object" && !!str((f as Record<string, unknown>).claim),
        );
        if (!items.length) break;
        if (raw.verified_update === true) a.sawVerifiedBatch = true;
        for (const f of items) {
          const key = normalizeClaim(str(f.claim));
          const prev = a.claims.get(key);
          // Batches re-emit the same claims as verification annotates them, so
          // merge rather than append: a later `verified: true` must win over an
          // earlier null, but must not erase a reason already filled in.
          a.claims.set(key, prev ? ({ ...prev, ...stripEmpty(f) } as Finding) : f);
        }
        break;
      }

      // -- Review ------------------------------------------------------------
      case "critic": {
        const a = accFor("review", ts);
        const round = typeof raw.iteration === "number" ? raw.iteration : null;
        a.rounds.push({
          kind: "round",
          id: `round-${a.rounds.length}`,
          round,
          reason: str(raw.reason),
        });
        break;
      }

      // -- Answer ------------------------------------------------------------
      case "final_report": {
        const a = accFor("answer", ts);
        a.report = {
          kind: "report",
          id: "report",
          report: str(raw.report),
          confidence: typeof raw.confidence === "number" ? raw.confidence : null,
          degraded: arr(raw.degraded).map(str).filter(Boolean),
        };
        break;
      }
      case "direct_answer": {
        const a = accFor("answer", ts);
        a.answer = str(raw.answer) || "Answered from existing knowledge.";
        break;
      }

      case "error": {
        const msg = str(raw.message) || "The run failed.";
        // Attach to whichever phase was mid-flight so the failure reads as
        // "this phase broke"; only a failure with nothing running gets its own.
        const open = [...accs.values()].reverse().find((x) => x.kind !== "answer" && !x.error);
        if (open) open.error = msg;
        else accFor("error", ts).error = msg;
        break;
      }

      default:
        // Unknown frame types are skipped, never rendered as noise. They remain
        // reachable through flattenRawSteps for debugging.
        break;
    }
  }

  // While a run is in flight the newest phase is the one still working; once
  // the run is over nothing is running. `final` is what distinguishes the two.
  const lastKind = order.length ? order[order.length - 1] : null;
  const out: HighLevelStep[] = [];
  for (const kind of order) {
    const phase = buildPhase(accs.get(kind)!, {
      final: opts.final === true,
      running: !opts.final && kind === lastKind,
    });
    if (phase) out.push(phase);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Phase builders
// ---------------------------------------------------------------------------

function buildPhase(
  a: Acc,
  ctx: { final: boolean; running: boolean },
): HighLevelStep | null {
  const base = {
    id: a.kind,
    kind: a.kind,
    startedAt: a.startedAt,
    endedAt: a.endedAt,
  };
  const status: PhaseStatus = a.error ? "error" : ctx.running ? "running" : "completed";

  switch (a.kind) {
    case "understand": {
      const body = a.texts.map((t) => t.body).filter(Boolean);
      if (!body.length) return null;
      const bits = a.texts
        .map((t) => t.body.match(/Read this as (.+?)\./)?.[1])
        .filter((x): x is string => Boolean(x))
        .join(" · ");
      return {
        ...base,
        title: "Understood the question",
        summary: bits || body[0],
        status,
        stats: {},
        details: a.texts,
        error: a.error,
      };
    }

    case "plan": {
      const items = a.todos?.items ?? [];
      if (!items.length) return null;
      return {
        ...base,
        title: `Planned ${plural(items.length, "investigation angle")}`,
        summary: items[0],
        status,
        stats: { angles: items.length },
        details: [a.todos!],
        error: a.error,
      };
    }

    case "research": {
      const queries = [...a.queries.values()];
      if (!queries.length && !a.snippets) return null;
      const domains = new Set<string>();
      for (const q of queries) for (const d of q.domains) domains.add(d.label);
      const issued = Math.max(a.issued, queries.length);
      const title = queries.length
        ? `Searched the web · ${plural(queries.length, "query", "queries")}`
        : "Searching the web";
      const summary = countList([
        queries.length ? plural(domains.size, "domain") : null,
        a.snippets ? `${a.snippets} sources retrieved` : null,
        issued > queries.length ? `${issued - queries.length} returned nothing` : null,
      ]);
      return {
        ...base,
        title,
        summary: summary || (a.snippets ? `${a.snippets} sources retrieved` : "Searching"),
        status,
        stats: {
          queries: queries.length,
          domains: domains.size,
          snippets: a.snippets,
          ...(issued > queries.length ? { issued } : {}),
        },
        details: queries,
        error: a.error,
      };
    }

    case "evidence": {
      const claims = [...a.claims.values()];
      if (!claims.length) return null;
      const hosts = new Set<string>();
      for (const c of claims) {
        const h = hostOf(str(c.source)) || str(c.source);
        if (h) hosts.add(h);
      }
      const verified = claims.filter((c) => c.verified === true).length;
      const title = a.sawVerifiedBatch
        ? `Verified ${verified} of ${claims.length} claims`
        : `Extracted ${plural(claims.length, "claim")} · ${plural(hosts.size, "source")}`;
      const summary = a.sawVerifiedBatch
        ? `${verified} confirmed against their cited source, ${claims.length - verified} unconfirmed`
        : countList([
            plural(claims.length, "claim"),
            plural(hosts.size, "source"),
            verified ? `${verified} verified` : null,
          ]);
      return {
        ...base,
        title,
        summary,
        status,
        stats: {
          claims: claims.length,
          ...(hosts.size ? { sources: hosts.size } : {}),
          ...(verified ? { verified } : {}),
        },
        details: [{ kind: "claims", id: "claims", claims, verified: a.sawVerifiedBatch }],
        error: a.error,
      };
    }

    case "review": {
      if (!a.rounds.length) return null;
      const n = a.rounds.length;
      const last = a.rounds[a.rounds.length - 1];
      return {
        ...base,
        title: `Evidence review · ${plural(n, "round")}`,
        summary: last.reason ? clip(last.reason, 140) : "Reviewed the collected evidence.",
        status,
        stats: { rounds: n },
        details: a.rounds,
        error: a.error,
      };
    }

    case "answer": {
      const report = a.report;
      const body = report?.report ?? a.answer ?? "";
      if (!body) return null;
      const conf = report?.confidence ?? null;
      const degraded = report?.degraded ?? [];
      const summary = countList([
        conf != null ? `confidence ${conf.toFixed(2)}` : null,
        degraded.length ? `${plural(degraded.length, "degraded source")}` : null,
      ]);
      return {
        ...base,
        title: report ? "Wrote the answer" : "Answered without searching",
        summary: summary || "Delivered from the evidence gathered.",
        status,
        stats: conf != null ? { confidence: conf } : {},
        details: [
          report
            ? report
            : { kind: "report", id: "report", report: body, confidence: conf, degraded },
        ],
        error: a.error,
      };
    }

    case "error": {
      return {
        ...base,
        title: "Run failed",
        summary: a.error ?? "The run failed.",
        status: "error",
        stats: {},
        details: [],
        error: a.error,
      };
    }

    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Full-trace view
// ---------------------------------------------------------------------------

export type RawStep = {
  id: string;
  /** The literal wire type, including types this build does not model. */
  type: string;
  label: string;
  detail: string;
  at: number;
  known: boolean;
};

/**
 * One row per wire frame, for the "Full trace" debugging view.
 *
 * Unlike the phase view this deliberately does NOT skip unknown types: the
 * point of the raw view is to show what actually arrived, so a new backend
 * event is visible here rather than silently missing.
 */
export function flattenRawSteps(
  rawEvents: readonly WireFrame[],
  opts: { now?: () => number } = {},
): RawStep[] {
  const now = opts.now ?? (() => Date.now());
  const out: RawStep[] = [];
  let i = 0;
  for (const frame of rawEvents) {
    if (!frame || typeof frame.type !== "string") continue;
    const raw = frame as unknown as Record<string, unknown>;
    const type = str(raw.type);
    const at = typeof raw.__ts === "number" && Number.isFinite(raw.__ts) ? raw.__ts : now();
    out.push({
      id: `raw-${i++}`,
      type,
      label: RAW_LABELS[type] ?? type,
      detail: rawDetail(raw),
      at,
      known: type in RAW_LABELS,
    });
  }
  return out;
}

const RAW_LABELS: Record<string, string> = {
  progress: "Progress",
  intent: "Classify intent",
  route: "Route",
  direct_answer: "Direct answer",
  plan: "Plan",
  search_progress: "Source counter",
  search_query: "Search query",
  findings: "Extract claims",
  critic: "Evidence review",
  final_report: "Final report",
  error: "Error",
};

/** A one-line factual description of a frame's payload — never a paraphrase. */
function rawDetail(raw: Record<string, unknown>): string {
  const t = str(raw.type);
  if (t === "search_query") {
    const n = arr(raw.results).length;
    const q = str(raw.query);
    if (!q) return typeof raw.issued === "number" ? `${raw.issued} queries issued so far` : "";
    return n ? `${plural(n, "result")} · ${domainsOf(arr(raw.results) as SearchHit[]).map((d) => d.label).join(", ")}` : "no results";
  }
  if (t === "search_progress") return `${str(raw.snippets)} sources so far`;
  if (t === "findings") return `${plural(arr(raw.items).length, "claim")}`;
  if (t === "plan") return `${plural(arr(raw.items).length, "angle")}`;
  if (t === "critic") {
    const it = typeof raw.iteration === "number" ? raw.iteration : null;
    return [it != null ? `round ${it}` : null, clip(str(raw.reason), 90)].filter(Boolean).join(" · ");
  }
  if (t === "intent") return clip(intentProse(raw), 90);
  if (t === "route") return [str(raw.path), clip(str(raw.reason), 70)].filter(Boolean).join(" · ");
  if (t === "final_report") {
    const c = typeof raw.confidence === "number" ? `confidence ${raw.confidence.toFixed(2)}` : "";
    return [c, `${str(raw.report).length} chars`].filter(Boolean).join(" · ");
  }
  if (t === "direct_answer") return clip(str(raw.answer), 90);
  if (t === "error") return str(raw.message);
  if (t === "progress") return str(raw.message);
  // Unknown type: show its keys rather than inventing a description.
  const keys = Object.keys(raw).filter((k) => k !== "__ts" && k !== "type");
  return keys.length ? `keys: ${keys.slice(0, 6).join(", ")}` : "";
}

// ---------------------------------------------------------------------------
// Replay trail
// ---------------------------------------------------------------------------

/**
 * One persisted `agent_events` row, as `GET /research/{id}/trace` returns it.
 *
 * This is a DIFFERENT and coarser source than the live NDJSON stream: it records
 * one row per node execution (`search: end`, `critic: end`, …) with a small
 * payload, not the individual queries, claims or URLs. So replay can rebuild the
 * shape of a run and its real counts, but not its per-result detail.
 */
export type NodeTrailEvent = {
  node?: string;
  event_type?: string;
  /** JSON text on the wire, but tolerate an already-parsed object. */
  payload?: unknown;
  started_at?: string;
  ended_at?: string;
};

/** Node name → the phase it belongs to. Unknown nodes are skipped. */
const NODE_PHASE: Record<string, PhaseKind> = {
  intent: "understand",
  route: "understand",
  planner: "plan",
  plan: "plan",
  search: "research",
  summarizer: "evidence",
  analyst: "evidence",
  verifier: "evidence",
  critic: "review",
  synthesizer: "answer",
  finalize: "answer",
};

/**
 * Rebuild high-level phases from a persisted node trail.
 *
 * Everything here is derived from payload fields the trace endpoint actually
 * stored, and nothing else: the run's shape and its counts, never invented query
 * text or claim bodies. A node with no recognisable payload contributes its
 * timing and nothing more.
 */
export function transformNodeTrail(nodes: readonly NodeTrailEvent[]): HighLevelStep[] {
  const order: PhaseKind[] = [];
  const buckets = new Map<PhaseKind, NodeTrailEvent[]>();

  for (const n of nodes) {
    if (!n || typeof n !== "object") continue;
    const kind = NODE_PHASE[str(n.node)];
    if (!kind) continue;
    if (!buckets.has(kind)) {
      buckets.set(kind, []);
      order.push(kind);
    }
    buckets.get(kind)!.push(n);
  }

  const out: HighLevelStep[] = [];
  for (const kind of order) {
    const rows = buckets.get(kind)!;
    const phase = buildTrailPhase(kind, rows);
    if (phase) out.push(phase);
  }
  return out;
}

function buildTrailPhase(kind: PhaseKind, rows: readonly NodeTrailEvent[]): HighLevelStep | null {
  const times = rows.map((r) => trailTime(r)).filter((t): t is number => t != null);
  const startedAt = times.length ? Math.min(...times) : 0;
  const endedAt = times.length ? Math.max(...times) : startedAt;

  const payloads = rows.map((r) => parsePayload(r.payload));
  // Latest row wins for a scalar field: a node can appear more than once and
  // the final execution is the one whose numbers describe the finished run.
  const pickNum = (...keys: string[]): number | null => {
    for (let i = payloads.length - 1; i >= 0; i--) {
      for (const k of keys) {
        const v = payloads[i][k];
        if (typeof v === "number" && Number.isFinite(v)) return v;
      }
    }
    return null;
  };

  const base = { id: `replay-${kind}`, kind, startedAt, endedAt };

  switch (kind) {
    case "understand": {
      const domain = str(lastPayloadValue(payloads, "domain"));
      const path = str(lastPayloadValue(payloads, "path"));
      const summary = countList([
        domain ? `${domain} domain` : null,
        path ? `${path} path` : null,
      ]);
      return {
        ...base,
        title: "Understood the question",
        summary: summary || "Classified the question.",
        status: "completed",
        stats: {},
        details: [],
      };
    }
    case "plan": {
      const angles = pickNum("sub_questions");
      return {
        ...base,
        title: angles != null ? `Planned ${plural(angles, "investigation angle")}` : "Planned the investigation",
        // The angle text was not persisted, so it is not shown rather than
        // shown as a blank.
        summary: angles != null ? `${plural(angles, "angle")} sent to research` : "",
        status: "completed",
        stats: angles != null ? { angles } : {},
        details: [],
      };
    }
    case "research": {
      const results = pickNum("results");
      return {
        ...base,
        title: results != null ? `Searched the web · ${plural(results, "source")}` : "Searched the web",
        summary: results != null ? `${plural(results, "source")} retrieved` : "",
        status: "completed",
        stats: results != null ? { snippets: results } : {},
        details: [],
      };
    }
    case "evidence": {
      const facts = pickNum("facts");
      const total = pickNum("total");
      const verified = pickNum("verified");
      const parts = countList([
        facts != null ? plural(facts, "claim") : null,
        verified != null && total != null ? `${verified} verified of ${total}` : null,
      ]);
      const title =
        verified != null && total != null
          ? `Verified ${verified} of ${total} claims`
          : facts != null
            ? `Extracted ${plural(facts, "claim")}`
            : "Extracted claims";
      return {
        ...base,
        title,
        summary: parts,
        status: "completed",
        stats: {
          ...(facts != null ? { claims: facts } : {}),
          ...(verified != null ? { verified } : {}),
        },
        details: [],
      };
    }
    case "review": {
      const n = rows.length;
      const last = payloads[payloads.length - 1];
      const sufficient = last?.is_sufficient;
      return {
        ...base,
        title: `Evidence review · ${plural(n, "round")}`,
        summary:
          sufficient === true
            ? "Concluded the evidence was sufficient."
            : sufficient === false
              ? "Concluded more evidence was needed."
              : "Reviewed the collected evidence.",
        status: "completed",
        stats: { rounds: n },
        details: rows.map((r, i) => {
          const p = parsePayload(r.payload);
          const it = typeof p.iteration === "number" ? p.iteration : null;
          return {
            kind: "round" as const,
            id: `replay-round-${i}`,
            round: it,
            reason:
              p.is_sufficient === true
                ? "Sufficient."
                : p.is_sufficient === false
                  ? "Insufficient — more evidence requested."
                  : "",
          };
        }),
      };
    }
    case "answer": {
      const cited = pickNum("cited");
      const supported = pickNum("supported");
      const summary = countList([
        cited != null ? `${cited} citations` : null,
        supported != null && cited != null ? `${supported} supported` : null,
      ]);
      return {
        ...base,
        title: "Wrote the answer",
        summary: summary || "Delivered from the evidence gathered.",
        status: "completed",
        stats: {},
        details: [],
      };
    }
    case "error":
      return null;
    default:
      return null;
  }
}

function parsePayload(payload: unknown): Record<string, unknown> {
  if (payload && typeof payload === "object") return payload as Record<string, unknown>;
  if (typeof payload !== "string" || !payload.trim()) return {};
  try {
    const v = JSON.parse(payload);
    return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function lastPayloadValue(payloads: readonly Record<string, unknown>[], key: string): unknown {
  for (let i = payloads.length - 1; i >= 0; i--) {
    if (payloads[i][key] != null) return payloads[i][key];
  }
  return undefined;
}

function trailTime(n: NodeTrailEvent): number | null {
  for (const v of [n.ended_at, n.started_at]) {
    if (typeof v === "string" && v) {
      const t = Date.parse(v);
      if (Number.isFinite(t)) return t;
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function str(v: unknown): string {
  return typeof v === "string" ? v.trim() : v == null ? "" : String(v);
}

function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

/** Case/punctuation-insensitive claim key, so re-emits collapse. */
function normalizeClaim(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

/** Keep an incoming field only when it actually carries a value. */
function stripEmpty(f: unknown): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (!f || typeof f !== "object") return out;
  for (const [k, v] of Object.entries(f as Record<string, unknown>)) {
    if (v === null || v === undefined || v === "") continue;
    out[k] = v;
  }
  return out;
}

/** Distinct hosts for a result set, most frequent first. */
export function domainsOf(
  hits: readonly SearchHit[],
): { label: string; url: string; count: number }[] {
  const tally = new Map<string, { url: string; count: number }>();
  for (const h of hits) {
    const url = str(h?.url);
    const host = hostOf(url);
    if (!host) continue;
    const prev = tally.get(host);
    if (prev) prev.count += 1;
    else tally.set(host, { url, count: 1 });
  }
  return [...tally.entries()]
    .map(([label, v]) => ({ label, ...v }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

/** Reasoning prose assembled only from fields the wire actually sent. */
function intentProse(frame: Record<string, unknown>): string {
  const bits: string[] = [];
  const qt = str(frame.query_type);
  const dom = str(frame.domain);
  const lvl = str(frame.explanation_level);
  // No article: the wire sends bare adjectives ("analytical"), so `a ${qt}`
  // renders as "a analytical question".
  if (qt) bits.push(`${qt} question`);
  if (dom) bits.push(`${dom} domain`);
  if (lvl) bits.push(`${lvl} explanation`);
  const lead = bits.length ? `Read this as ${bits.join(", ")}.` : "Classified the question.";
  const extra: string[] = [];
  if (frame.ambiguity === true) extra.push("The wording is ambiguous, so more than one reading was weighed.");
  if (frame.underspecified === true) extra.push("It is underspecified, so the answer states what it assumes.");
  const senses = arr(frame.senses).length;
  if (senses > 1) extra.push(`Found ${senses} plausible readings.`);
  return [lead, ...extra].join(" ");
}

function countList(parts: (string | null | undefined)[]): string {
  return parts.filter((p): p is string => Boolean(p)).join(" · ");
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

function clip(s: string, max: number): string {
  const t = s.trim();
  if (t.length <= max) return t;
  return `${t.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
}

export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function formatClock(ts: number): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}