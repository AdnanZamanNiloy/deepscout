import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useCallback, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";

import {
  flattenRawSteps,
  formatClock,
  formatDuration,
  transformNodeTrail,
  transformToDeerFlowSteps,
  type ClaimsDetail,
  type HighLevelStep,
  type NodeTrailEvent,
  type PhaseDetail,
  type PhaseKind,
  type QueryDetail,
  type RawStep,
} from "./flow.ts";
import type { Finding, WireEvent } from "./events.ts";

export type AgentPipelineTraceProps = {
  /** Live NDJSON frames from the backend, oldest first. */
  events: readonly WireEvent[];
  /**
   * Persisted node trail for a restored session. Used only when there is no
   * live frame stream: replay records one row per node, not per result, so the
   * phases it rebuilds carry the run's shape and counts but no per-result
   * detail. The panel says so rather than implying the detail was empty.
   */
  trail?: readonly NodeTrailEvent[];
  /** Overall run state; drives the header and the "running" glow. */
  status?: "idle" | "running" | "done" | "error";
  /** Start collapsed (e.g. for a finished run the reader already scanned). */
  defaultOpen?: boolean;
  className?: string;
};

type Mode = "less" | "full";

const STAGGER = 0.05;
const EASE = [0.22, 1, 0.36, 1] as const;

/** Claims shown before the "show all" expander. */
const CLAIM_PAGE = 10;

/** Phases that open themselves: whatever is working, and the answer. */
function openByDefault(p: HighLevelStep): boolean {
  return p.status === "running" || p.kind === "answer";
}

export default function AgentPipelineTrace({
  events,
  trail,
  status = "idle",
  defaultOpen = true,
  className,
}: AgentPipelineTraceProps) {
  const [mode, setMode] = useState<Mode>("less");
  const [expandedPanel, setExpandedPanel] = useState(defaultOpen);
  /** Explicit user choices win over openByDefault for the life of the run. */
  const [override, setOverride] = useState<Map<string, boolean>>(() => new Map());
  const listRef = useRef<HTMLOListElement | null>(null);
  const pinnedRef = useRef(true);
  const reduce = useReducedMotion();

  const final = status !== "running";
  // A restored run has no raw frames; rebuild from its node trail instead.
  const replay = events.length === 0 && (trail?.length ?? 0) > 0;
  const phases = useMemo(
    () => (replay ? transformNodeTrail(trail!) : transformToDeerFlowSteps(events, { final })),
    [replay, trail, events, final],
  );
  const raw = useMemo(() => (replay ? [] : flattenRawSteps(events)), [replay, events]);

  const runningCount = phases.filter((p) => p.status === "running").length;

  const isOpen = useCallback(
    (p: HighLevelStep) => (override.has(p.id) ? override.get(p.id)! : openByDefault(p)),
    [override],
  );

  const toggle = useCallback(
    (id: string, currentlyOpen: boolean) =>
      setOverride((prev) => {
        const next = new Map(prev);
        next.set(id, !currentlyOpen);
        return next;
      }),
    [],
  );

  // Auto-follow the newest phase only while the reader is already at the
  // bottom; scrolling up to read something must not be fought by the script.
  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el || !pinnedRef.current || !expandedPanel) return;
    el.scrollTop = el.scrollHeight;
  }, [phases.length, expandedPanel, mode]);

  const onScroll = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 56;
  }, []);

  const counts =
    mode === "less"
      ? { label: "Less steps", n: phases.length, unit: phases.length === 1 ? "phase" : "phases" }
      : { label: "Full trace", n: raw.length, unit: raw.length === 1 ? "event" : "events" };

  return (
    <section className={`af ${className ?? ""}`} data-status={status}>
      <header className="af-head">
        <button
          type="button"
          className="af-disclosure"
          onClick={() => setExpandedPanel((v) => !v)}
          aria-expanded={expandedPanel}
          aria-controls="af-body"
        >
          <span className="af-caret" aria-hidden="true" data-open={expandedPanel} />
          <span className="af-disclosure-label">
            {expandedPanel ? "Hide agent flow" : "Show agent flow"}
          </span>
          {status === "running" ? (
            <span className="af-live">
              <span className="af-live-dot" aria-hidden="true" />
              live
            </span>
          ) : null}
        </button>

        {replay ? (
          <span className="af-provenance" title="Rebuilt from the saved run timeline">
            reconstructed
          </span>
        ) : (
        <div
          className="af-modes"
          role="group"
          aria-label="Trace detail level"
          data-count={counts.n}
        >
          <button
            type="button"
            className="af-mode"
            data-active={mode === "less"}
            aria-pressed={mode === "less"}
            onClick={() => setMode("less")}
          >
            Less steps
            <span className="af-mode-count">
              {phases.length} {phases.length === 1 ? "phase" : "phases"}
            </span>
          </button>
          <button
            type="button"
            className="af-mode"
            data-active={mode === "full"}
            aria-pressed={mode === "full"}
            onClick={() => setMode("full")}
          >
            Full trace
            <span className="af-mode-count">
              {raw.length} {raw.length === 1 ? "event" : "events"}
            </span>
          </button>
        </div>
        )}
      </header>

      <AnimatePresence initial={false}>
        {expandedPanel ? (
          <motion.div
            id="af-body"
            key="body"
            className="af-body"
            initial={reduce ? false : { height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={reduce ? { opacity: 0 } : { height: 0, opacity: 0 }}
            transition={{ duration: 0.28, ease: EASE }}
          >
            {mode === "less" ? (
              phases.length === 0 ? (
                <p className="af-empty">
                  {status === "running"
                    ? "Reading the question…"
                    : "No agent steps were recorded for this run."}
                </p>
              ) : (
                <ol
                  className="af-phases"
                  ref={listRef}
                  onScroll={onScroll}
                  aria-live="polite"
                  aria-relevant="additions"
                  aria-label="Agent flow phases"
                >
                  {phases.map((p, i) => (
                    <Phase
                      key={p.id}
                      phase={p}
                      index={i}
                      open={isOpen(p)}
                      onToggle={() => toggle(p.id, isOpen(p))}
                    />
                  ))}
                </ol>
              )
            ) : (
              <RawTrace rows={raw} />
            )}
          </motion.div>
        ) : null}
      </AnimatePresence>

      {runningCount > 0 && expandedPanel && mode === "less" ? (
        <p className="af-foot" aria-live="polite">
          <span className="af-live-dot" aria-hidden="true" />
          Working on {runningCount === 1 ? "the current phase" : `${runningCount} phases`}
        </p>
      ) : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Phase
// ---------------------------------------------------------------------------

function Phase({
  phase,
  index,
  open,
  onToggle,
}: {
  phase: HighLevelStep;
  index: number;
  open: boolean;
  onToggle: () => void;
}) {
  const reduce = useReducedMotion();
  const hasDetail = phase.details.length > 0 && phase.kind !== "answer";
  const duration = formatDuration(phase.endedAt - phase.startedAt);

  return (
    <motion.li
      className="af-phase"
      data-kind={phase.kind}
      data-status={phase.status}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration: reduce ? 0 : 0.3,
        delay: reduce ? 0 : Math.min(index, 8) * STAGGER,
        ease: EASE,
      }}
    >
      <span className="af-rail" aria-hidden="true">
        <StatusDot status={phase.status} />
      </span>

      <div className="af-phase-main">
        <button
          type="button"
          className="af-phase-head"
          onClick={hasDetail ? onToggle : undefined}
          aria-expanded={hasDetail ? open : undefined}
          disabled={!hasDetail}
        >
          <span className="af-icon" aria-hidden="true">
            <PhaseIcon kind={phase.kind} />
          </span>

          <span className="af-phase-text">
            <span className="af-phase-title">{phase.title}</span>
            {phase.summary ? <span className="af-phase-summary">{phase.summary}</span> : null}
          </span>

          <span className="af-phase-meta">
            {duration ? <span className="af-duration">{duration}</span> : null}
            <time className="af-time" dateTime={new Date(phase.startedAt).toISOString()}>
              {formatClock(phase.startedAt)}
            </time>
            {hasDetail ? (
              <span className="af-caret af-caret-inline" aria-hidden="true" data-open={open} />
            ) : null}
          </span>
        </button>

        {phase.stats.queries || phase.stats.claims || phase.stats.angles ? (
          <p className="af-statline">
            {phase.stats.angles ? <Stat>{phase.stats.angles} angles</Stat> : null}
            {phase.stats.queries ? <Stat>{phase.stats.queries} queries</Stat> : null}
            {phase.stats.domains ? <Stat>{phase.stats.domains} domains</Stat> : null}
            {phase.stats.claims ? <Stat>{phase.stats.claims} claims</Stat> : null}
            {phase.stats.sources ? <Stat>{phase.stats.sources} sources</Stat> : null}
            {phase.stats.rounds ? <Stat>{phase.stats.rounds} rounds</Stat> : null}
          </p>
        ) : null}

        {phase.error ? <p className="af-error">{phase.error}</p> : null}

        {phase.kind === "answer" ? <AnswerMeta phase={phase} /> : null}

        <AnimatePresence initial={false}>
          {hasDetail && open ? (
            <motion.div
              className="af-details"
              key="details"
              initial={reduce ? false : { height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={reduce ? { opacity: 0 } : { height: 0, opacity: 0 }}
              transition={{ duration: 0.24, ease: EASE }}
            >
              {phase.details.map((d) => (
                <Detail key={d.id} detail={d} />
              ))}
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>
    </motion.li>
  );
}

function Stat({ children }: { children: ReactNode }) {
  return <span className="af-stat">{children}</span>;
}

function AnswerMeta({ phase }: { phase: HighLevelStep }) {
  const conf = phase.stats.confidence;
  const degraded = phase.details.find((d) => d.kind === "report");
  const list = degraded && degraded.kind === "report" ? degraded.degraded : [];
  return (
    <p className="af-answermeta">
      {conf != null ? (
        <span className="af-stat" data-tone={conf >= 0.75 ? "good" : conf >= 0.55 ? "mid" : "low"}>
          confidence {conf.toFixed(2)}
        </span>
      ) : null}
      {list.map((d) => (
        <span className="af-stat" data-tone="low" key={d}>
          degraded: {d}
        </span>
      ))}
      <span className="af-answernote">Full answer is below.</span>
    </p>
  );
}

// ---------------------------------------------------------------------------
// Details
// ---------------------------------------------------------------------------

function Detail({ detail }: { detail: PhaseDetail }) {
  switch (detail.kind) {
    case "text":
      return (
        <div className="af-detail">
          <p className="af-detail-label">{detail.label}</p>
          <p className="af-detail-text">{detail.body}</p>
        </div>
      );
    case "todo":
      return (
        <ol className="af-angles">
          {detail.items.map((q, i) => (
            <li key={`${q}-${i}`}>{q}</li>
          ))}
        </ol>
      );
    case "query":
      return <QueryRow detail={detail} />;
    case "claims":
      return <Claims detail={detail} />;
    case "round":
      return (
        <div className="af-round">
          <span className="af-round-tag">{detail.round != null ? `Round ${detail.round}` : "Review"}</span>
          <p className="af-round-reason">{detail.reason || "No reasoning recorded."}</p>
        </div>
      );
    case "report":
      // The answer body is rendered by AnswerCard directly below the flow;
      // repeating the whole report here would be the wall of text this view
      // exists to avoid.
      return null;
    default:
      return null;
  }
}

function QueryRow({ detail }: { detail: QueryDetail }) {
  const [open, setOpen] = useState(false);
  const domains = detail.domains.slice(0, 6);
  const rest = detail.hits.length;
  return (
    <div className="af-detail af-query">
      <div className="af-query-head">
        <p className="af-query-text">{detail.query}</p>
        <span className="af-query-count">
          {detail.hits.length} {detail.hits.length === 1 ? "source" : "sources"}
        </span>
      </div>
      {domains.length ? (
        <p className="af-domains">
          {domains.map((d) => (
            <a
              key={d.label}
              className="af-domain"
              href={/^https?:\/\//i.test(d.url) ? d.url : undefined}
              target="_blank"
              rel="noreferrer noopener"
              title={d.count > 1 ? `${d.count} results from ${d.label}` : d.label}
            >
              {d.label}
              {d.count > 1 ? <span className="af-domain-n">{d.count}</span> : null}
            </a>
          ))}
        </p>
      ) : null}
      {rest ? (
        <>
          <button type="button" className="af-more" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
            {open ? "Hide results" : `Show ${rest} result${rest === 1 ? "" : "s"}`}
          </button>
          <AnimatePresence initial={false}>
            {open ? (
              <motion.ul
                className="af-urls"
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2, ease: EASE }}
              >
                {detail.hits.map((h) => (
                  <li key={h.url}>
                    <a href={h.url} target="_blank" rel="noreferrer noopener">
                      {h.title || h.url}
                    </a>
                    <span className="af-url-host">{h.source ?? ""}</span>
                  </li>
                ))}
              </motion.ul>
            ) : null}
          </AnimatePresence>
        </>
      ) : null}
    </div>
  );
}

function Claims({ detail }: { detail: ClaimsDetail }) {
  const [all, setAll] = useState(false);
  const shown = all ? detail.claims : detail.claims.slice(0, CLAIM_PAGE);
  const hidden = detail.claims.length - shown.length;
  return (
    <div className="af-detail af-claims">
      <ul className="af-claim-list">
        {shown.map((c: Finding, i) => (
          <li key={`${c.claim}-${i}`} data-verified={c.verified === true}>
            <span className="af-claim-tick" aria-hidden="true">
              {c.verified === true ? <CheckIcon /> : null}
            </span>
            <span className="af-claim-text">{c.claim}</span>
            {c.source ? (
              <a
                className="af-claim-source"
                href={/^https?:\/\//i.test(c.source) ? c.source : undefined}
                target="_blank"
                rel="noreferrer noopener"
              >
                {c.source}
              </a>
            ) : null}
          </li>
        ))}
      </ul>
      {hidden > 0 ? (
        <button type="button" className="af-more" onClick={() => setAll(true)}>
          Show {hidden} more claim{hidden === 1 ? "" : "s"}
        </button>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Raw view
// ---------------------------------------------------------------------------

function RawTrace({ rows }: { rows: readonly RawStep[] }) {
  const reduce = useReducedMotion();
  if (!rows.length) {
    return <p className="af-empty">No events were received for this run.</p>;
  }
  return (
    <ol className="af-raw" aria-label="Raw event stream">
      {rows.map((r, i) => (
        <motion.li
          key={r.id}
          className="af-raw-row"
          data-known={r.known}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: reduce ? 0 : 0.22, delay: reduce ? 0 : Math.min(i, 20) * 0.012, ease: EASE }}
        >
          <code className="af-raw-type">{r.type}</code>
          <span className="af-raw-label">{r.label}</span>
          {r.detail ? <span className="af-raw-detail">{r.detail}</span> : null}
          <time className="af-time" dateTime={new Date(r.at).toISOString()}>
            {formatClock(r.at)}
          </time>
        </motion.li>
      ))}
    </ol>
  );
}

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------

function StatusDot({ status }: { status: HighLevelStep["status"] }) {
  if (status === "running") {
    return (
      <span className="af-dot af-dot-running" aria-label="in progress">
        <span className="af-dot-shimmer" aria-hidden="true" />
      </span>
    );
  }
  if (status === "error") {
    return (
      <span className="af-dot af-dot-error" aria-label="failed">
        <BangIcon />
      </span>
    );
  }
  return (
    <span className="af-dot af-dot-done" aria-label="complete">
      <CheckIcon />
    </span>
  );
}

function PhaseIcon({ kind }: { kind: PhaseKind }) {
  switch (kind) {
    case "understand":
      return <BrainIcon />;
    case "plan":
      return <ListIcon />;
    case "research":
      return <GlobeIcon />;
    case "evidence":
      return <FileIcon />;
    case "review":
      return <ScaleIcon />;
    case "answer":
      return <SparkIcon />;
    case "error":
      return <BangIcon />;
    default:
      return <DotIcon />;
  }
}

const S = { fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

function CheckIcon() {
  return (
    <svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">
      <path d="M3.5 8.4 6.6 11.5 12.5 5" {...S} strokeWidth={2} />
    </svg>
  );
}

function BangIcon() {
  return (
    <svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">
      <path d="M8 4.2v4.4M8 11.4v.2" {...S} strokeWidth={2} />
    </svg>
  );
}

function DotIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <circle cx="8" cy="8" r="2.4" {...S} />
    </svg>
  );
}

function BrainIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M6.4 3.2a1.9 1.9 0 0 0-2.7 2.7 1.9 1.9 0 0 0-.6 3.3 1.9 1.9 0 0 0 2.3 2.6c.5.2 1 .2 1.4.1" {...S} />
      <path d="M9.6 3.2a1.9 1.9 0 0 1 2.7 2.7 1.9 1.9 0 0 1 .6 3.3 1.9 1.9 0 0 1-2.3 2.6c-.5.2-1 .2-1.4.1" {...S} />
      <path d="M8 3v9.9" {...S} />
    </svg>
  );
}

function ListIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M6 4.5h6.5M6 8h6.5M6 11.5h4" {...S} />
      <path d="M3.2 4.4l.9.9 1-1.4M3.2 7.9l.9.9 1-1.4M3.2 11.4l.9.9 1-1.4" {...S} />
    </svg>
  );
}

function GlobeIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <circle cx="8" cy="8" r="5.2" {...S} />
      <path d="M2.9 8h10.2M8 2.8c1.5 1.6 2.2 3.4 2.2 5.2S9.5 11.6 8 13.2C6.5 11.6 5.8 9.8 5.8 8S6.5 4.4 8 2.8Z" {...S} />
    </svg>
  );
}

function FileIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M9 2.6H4.8c-.6 0-1.1.5-1.1 1.1v8.6c0 .6.5 1.1 1.1 1.1h6.4c.6 0 1.1-.5 1.1-1.1V5.9L9 2.6Z" {...S} />
      <path d="M9 2.6v3.3h3.3M6 8.6h4M6 10.8h2.6" {...S} />
    </svg>
  );
}

function ScaleIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M8 3v10M4.4 13h7.2M3 6.2h10" {...S} />
      <path d="M3 6.2 1.6 10h2.8L3 6.2ZM13 6.2 11.6 10h2.8L13 6.2Z" {...S} />
    </svg>
  );
}

function SparkIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M8 2.4 9.3 6l3.6 1.3L9.3 8.6 8 12.2 6.7 8.6 3.1 7.3 6.7 6 8 2.4Z" {...S} />
    </svg>
  );
}

export type { HighLevelStep } from "./flow.ts";
export type { WireEvent } from "./events.ts";