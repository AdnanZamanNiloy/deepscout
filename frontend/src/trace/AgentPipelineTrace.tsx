import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  buildTrace,
  currentStage,
  type StepKind,
  type StepStatus,
  type TraceChip,
  type TraceStep,
  type WireEvent,
} from "./events";

export type { TraceStep, WireEvent };

export type AgentPipelineTraceProps = {
  /** Live NDJSON frames from the backend, oldest first. */
  events: readonly WireEvent[];
  /** Overall run state; drives the live pulse and empty/error copy. */
  status?: "idle" | "running" | "done" | "error";
  /** Start collapsed. */
  defaultOpen?: boolean;
  /** Render prop for a step body that needs richer content. */
  renderContent?: (step: TraceStep) => ReactNode;
  /** Cap on rendered rows; older ones collapse into a count. */
  maxSteps?: number;
  className?: string;
};

const STAGGER = 0.04;

/** A paragraph longer than this is clipped to two lines behind a toggle. */
const CLAMP_CHARS = 240;

/** Claims shown before the "show all" toggle appears on an evidence row. */
const COLLAPSED_CLAIMS = 4;

// ---------------------------------------------------------------------------
// Icons — monochrome, 24-grid, sized by CSS. One per row kind so a reader can
// scan the rail and know what kind of action happened without reading labels.
// ---------------------------------------------------------------------------

function Icon({ kind }: { kind: StepKind }) {
  const common = {
    viewBox: "0 0 24 24",
    // Width/height are set here as attributes, not only in CSS. An inline SVG
    // carrying a viewBox and no intrinsic size stretches to fill its container,
    // so a single missing CSS rule rendered a full-page magnifying glass. The
    // class sizes and positions it; the attributes are the floor.
    width: 15,
    height: 15,
    className: "apt-icon",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (kind) {
    case "tool":
      return (
        <svg {...common}>
          <circle cx="11" cy="11" r="8" />
          <path d="m21 21-4.3-4.3" />
        </svg>
      );
    case "todo":
      return (
        <svg {...common}>
          <path d="m3 7 2 2 4-4" />
          <path d="m3 17 2 2 4-4" />
          <path d="M13 6h8" />
          <path d="M13 12h8" />
          <path d="M13 18h8" />
        </svg>
      );
    case "evidence":
      return (
        <svg {...common}>
          <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" />
          <path d="M14 2v4a2 2 0 0 0 2 2h4" />
          <path d="M10 9H8" />
          <path d="M16 13H8" />
          <path d="M16 17H8" />
        </svg>
      );
    case "gate":
      return (
        <svg {...common}>
          <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />
          <path d="m9 12 2 2 4-4" />
        </svg>
      );
    case "final":
      return (
        <svg {...common}>
          <path d="M9.94 15.5A2 2 0 0 0 8.5 14.06l-6.14-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.14a.5.5 0 0 1 .96 0L14.06 8.5A2 2 0 0 0 15.5 9.94l6.14 1.58a.5.5 0 0 1 0 .96L15.5 14.06a2 2 0 0 0-1.44 1.44l-1.58 6.14a.5.5 0 0 1-.96 0z" />
        </svg>
      );
    case "thinking":
    default:
      return null;
  }
}

/** Kinds that read as prose bullets rather than tool rows. */
function isProse(kind: StepKind): boolean {
  return kind === "thinking";
}

// ---------------------------------------------------------------------------
// Prose rendering
//
// The trace's premium quality comes from the text reading like a narrative, so
// URLs and quoted queries inside a sentence are highlighted inline — the same
// treatment the reference gives an inline path. Purely presentational: the text
// is whatever the backend sent.
// ---------------------------------------------------------------------------

const URL_RE = /https?:\/\/[^\s<>()[\]"]+/g;

function Prose({ text }: { text: string }) {
  const nodes: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  URL_RE.lastIndex = 0;
  let key = 0;

  while ((m = URL_RE.exec(text)) !== null) {
    if (m.index > last) nodes.push(...highlightQuotes(text.slice(last, m.index), `t${key++}`));
    const url = m[0];
    nodes.push(
      <a
        key={`u${key++}`}
        className="apt-inline"
        href={url}
        target="_blank"
        rel="noreferrer noopener"
      >
        {url}
      </a>,
    );
    last = m.index + url.length;
  }
  if (last < text.length) nodes.push(...highlightQuotes(text.slice(last), `t${key++}`));

  return <>{nodes}</>;
}

/** Wrap "quoted spans" in the inline chip treatment. */
function highlightQuotes(chunk: string, keyBase: string): ReactNode[] {
  if (!chunk.includes('"')) return [chunk];
  const out: ReactNode[] = [];
  const parts = chunk.split(/("[^"]*")/g);
  parts.forEach((part, i) => {
    if (part.startsWith('"') && part.endsWith('"') && part.length > 2) {
      out.push(
        <code key={`${keyBase}-q${i}`} className="apt-inline">
          {part.slice(1, -1)}
        </code>,
      );
    } else if (part) {
      out.push(part);
    }
  });
  return out;
}

// ---------------------------------------------------------------------------
// Rows
// ---------------------------------------------------------------------------

/** A retrieved URL, set as a monospace path chip with a left accent bar. */
function CodeChip({ chip }: { chip: TraceChip }) {
  const safe = /^https?:\/\//i.test(chip.url);
  // The label sits in its own span because `text-overflow: ellipsis` only works
  // on a block-ish box. On the flex pill it silently did nothing, so an
  // over-long title was hard-clipped mid-word ("...Conflict · tavi") with no
  // ellipsis and no width cap.
  const body = (
    <>
      <span className="apt-code-label">{chip.label}</span>
      {chip.meta ? <span className="apt-chip-meta">{chip.meta}</span> : null}
    </>
  );
  const full = chip.meta ? `${chip.label} — ${chip.meta}` : chip.label;
  if (!safe) {
    return (
      <div className="apt-code" title={full}>
        {body}
      </div>
    );
  }
  return (
    <a
      className="apt-code"
      href={chip.url}
      target="_blank"
      rel="noreferrer noopener"
      title={`${full}\n${chip.url}`}
    >
      {body}
    </a>
  );
}

function Row({
  step,
  open,
  onToggle,
  index,
  total,
  renderContent,
}: {
  step: TraceStep;
  open: boolean;
  onToggle: (id: string) => void;
  index: number;
  /** Sibling count, so the stagger can run from the newest row backwards. */
  total: number;
  renderContent?: (s: TraceStep) => ReactNode;
}) {
  const prose = isProse(step.kind) || step.kind === "evidence";
  // Claims arrive newline-separated and read well as separate bullets, which is
  // how the reference sets out a narrative. The final report is markdown, so
  // splitting it on newlines would turn "# Answer" into its own bullet — keep
  // that block intact and let CSS preserve its line breaks.
  const isFinal = step.kind === "final";
  const paragraphs = useMemo(
    () => {
      if (!step.content) return [];
      if (isFinal) return [step.content];
      return step.content.split("\n").filter((p) => p.trim());
    },
    [step.content, isFinal],
  );
  const isEvidence = step.kind === "evidence";

  // Long prose is clipped, never character-sliced. Slicing at a fixed offset
  // cut mid-sentence and, on evidence, merged separate claims into one run-on
  // paragraph. A CSS line clamp keeps the text intact and readable.
  const bulk = isEvidence && paragraphs.length > COLLAPSED_CLAIMS;
  const visible = bulk && !open ? paragraphs.slice(0, COLLAPSED_CLAIMS) : paragraphs;
  const longProse = !open && visible.some((p) => p.length > CLAMP_CHARS);
  // The clamp class goes on every collapsed claim, even short ones: a two-line
  // clamp is a no-op on text that already fits, and applying it conditionally
  // made the preview height jump around as claims arrived.
  const applyClamp = !open && (isEvidence || longProse);
  // A toggle appears only when there is genuinely more to read — a bulk batch,
  // or prose too long for the clamp. Two short claims get no toggle.
  const collapsible = bulk || longProse;

  return (
    <motion.li
      className="apt-row"
      data-kind={step.kind}
      data-status={step.status}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration: 0.3,
        // Staggered from the NEWEST row backwards. Delaying by absolute index
        // meant a row arriving at position 40 waited 0.56s before it was even
        // visible, so a burst of frames looked like a blank gap and then an
        // instant pop. Now the newest row leads and the ones above it cascade.
        delay: Math.min(Math.max(0, total - 1 - index), 5) * STAGGER,
        ease: [0.22, 1, 0.36, 1],
      }}
    >
      <span className="apt-gutter" aria-hidden="true">
        <Icon kind={step.kind} />
      </span>

      <div className="apt-row-body">
        {/* Tool rows get a short label above their detail, matching the
            reference's "Check … directory" rows. */}
        {!prose ? (
          <button
            type="button"
            className="apt-row-toggle"
            onClick={() => (collapsible || step.chips?.length ? onToggle(step.id) : undefined)}
            aria-expanded={collapsible || step.chips?.length ? open : undefined}
            disabled={!collapsible && !step.chips?.length}
            tabIndex={!collapsible && !step.chips?.length ? -1 : undefined}
          >
            <span className="apt-tool-label">{step.title}</span>
          </button>
        ) : null}

        {/* The final report is already rendered in full by the answer card.
            Repeating it inline would bury the trace, so it stays behind an
            explicit toggle even when it is short enough to skip the clamp. */}
        {isFinal && !open && !renderContent ? (
          <button
            type="button"
            className="apt-row-toggle"
            onClick={() => onToggle(step.id)}
            aria-expanded={false}
          >
            <span className="apt-muted">Show report</span>
          </button>
        ) : null}

        {renderContent ? (
          renderContent(step)
        ) : isFinal && !open ? null : paragraphs.length ? (
          <div>
            {visible.map((p, i) => (
              <p
                className={
                  isFinal
                    ? "apt-prose apt-pre"
                    : applyClamp
                      ? "apt-prose apt-claim"
                      : "apt-prose"
                }
                key={`${step.id}-p${i}`}
              >
                <Prose text={p} />
              </p>
            ))}
            {collapsible ? (
              <button
                type="button"
                className="apt-row-toggle"
                onClick={() => onToggle(step.id)}
                aria-expanded={open}
              >
                <span className="apt-muted">
                  {open
                    ? "Show less"
                    : bulk
                      ? `Show all ${paragraphs.length} claims`
                      : "Show more"}
                </span>
              </button>
            ) : null}
          </div>
        ) : null}

        {step.error ? <p className="apt-prose">{step.error}</p> : null}

        {/* Keyed on the chip count too: the backend re-emits a search as its
            results land, and those chips arrive on a row that is ALREADY open.
            Without the count in the key the block never re-animated, so sources
            appeared with no transition at all — the "sometimes nothing
            animates" symptom. */}
        <AnimatePresence initial={false}>
          {open && step.chips?.length ? (
            <motion.div
              key={`chips-${step.chips.length}`}
              className="apt-chips"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
              style={{ overflow: "hidden" }}
            >
              {step.chips.map((c) => (
                <CodeChip key={c.id} chip={c} />
              ))}
            </motion.div>
          ) : null}
        </AnimatePresence>

        <AnimatePresence initial={false}>
          {open && step.children?.length ? (
            <motion.ul
              className="apt-nested"
              key="children"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
              style={{ overflow: "hidden" }}
            >
              {step.children.map((c, i) => (
                <Row
                  key={c.id}
                  step={c}
                  open
                  onToggle={onToggle}
                  index={i}
                  total={(step.children ?? []).length}
                  renderContent={renderContent}
                />
              ))}
            </motion.ul>
          ) : null}
        </AnimatePresence>
      </div>
    </motion.li>
  );
}

/** "2m 41s" / "45s" / "1h 5m". Zero-padded only where it aids scanning. */
export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes < 60) return `${minutes}m ${seconds}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Wall-clock time the run has taken, from the first frame the client received
 * to the latest.
 *
 * Measured from the frames' own arrival stamps rather than a start timestamp
 * the stream never sends, so it reflects what the reader actually experienced.
 * While the run is live the end is `now`, not the last frame — otherwise the
 * figure would sit frozen between arrivals and read as stalled.
 *
 * Returns null until at least two frames exist: a single frame has no span,
 * and showing "Worked for 0s" the instant the first event lands is noise.
 */
function useElapsed(
  events: readonly WireEvent[],
  running: boolean,
): number | null {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!running) return undefined;
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [running]);

  let first: number | null = null;
  let last: number | null = null;
  let seen = 0;
  for (const e of events) {
    if (!e) continue;
    // The client's arrival stamp wins for a live run; the server's emission
    // stamp is the only one present on replayed frames, so it is the fallback
    // that keeps the duration visible after a reload.
    const client = typeof e.__ts === "number" ? e.__ts : null;
    const server = typeof e.ts === "number" ? e.ts : null;
    const t = client ?? server;
    if (t === null) continue;
    seen += 1;
    if (first === null) first = t;
    last = t;
  }
  // A single frame yields first === last, so the ordering check alone cannot
  // reject it — the count has to. Without this the header reads "Worked for
  // 0s" the instant the first event lands.
  if (seen < 2 || first === null || last === null || last < first) return null;

  // A backwards system clock must not render a negative duration.
  const end = running ? Math.max(last, Date.now()) : last;
  return Math.max(0, end - first);
}

export default function AgentPipelineTrace({
  events,
  status = "idle",
  defaultOpen = true,
  renderContent,
  maxSteps = 100,
  className,
}: AgentPipelineTraceProps) {
  const [expanded, setExpanded] = useState(defaultOpen);
  const wasLive = useRef(status === "running");
  const [openOverride, setOpenOverride] = useState<Record<string, boolean>>({});
  const reduce = useReducedMotion();

  // Open automatically the moment a run starts streaming.
  //
  // `defaultOpen` cannot express this: the trace mounts with its message,
  // before any frame arrives, so the run is still idle and a prop change never
  // reaches a useState initialiser. Watching the idle -> running edge is the
  // only place the transition is observable.
  //
  // Deliberately NOT applied to a restored session — replaying a finished run
  // should not unroll a long trace into the thread unasked.
  useEffect(() => {
    if (status === "running" && !wasLive.current) setExpanded(true);
    wasLive.current = status === "running";
  }, [status]);

  const all = useMemo(() => buildTrace(events), [events]);
  const steps = useMemo(() => all.slice(-maxSteps), [all, maxSteps]);
  const hidden = all.length - steps.length;

  // Open-ness is DERIVED during render rather than seeded by an effect. An
  // effect only runs in the browser, so server-rendered and first-paint markup
  // came out collapsed and every source chip flashed shut before appearing.
  const defaultOpenFor = useCallback(
    (id: string) => {
      const s = all.find((x) => x.id === id);
      // Only source chips open by default. Auto-opening long prose was the
      // defect: a critic rationale runs to thousands of characters, so the rule
      // "open it if it's long" expanded exactly the rows that were unwieldy.
      // Long text now stays clipped until asked for.
      if (!s || s.kind === "final") return false;
      return Boolean(s.chips?.length);
    },
    [all],
  );

  // Declared after defaultOpenFor and dependent on it: reading a const before
  // its declaration is a temporal-dead-zone hazard, and leaving it out of the
  // deps let the toggle act on a stale default.
  const toggleStep = useCallback(
    (id: string) => {
      setOpenOverride((prev) => ({ ...prev, [id]: !defaultOpenFor(id) }));
    },
    [defaultOpenFor],
  );

  const isOpen = useCallback(
    (id: string) => (id in openOverride ? openOverride[id] : defaultOpenFor(id)),
    [openOverride, defaultOpenFor],
  );

  const running = status === "running";
  const elapsedMs = useElapsed(events, running);
  const stage = useMemo(() => currentStage(events), [events]);

  return (
    <section className={`apt ${className ?? ""}`} data-status={status}>
      <header className="apt-head">
        <button
          type="button"
          className="apt-toggle"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          aria-controls="apt-body"
        >
          <span className="apt-caret" aria-hidden="true" data-open={expanded} />
          <span>Less steps</span>
        </button>

        {/* Elapsed time only. The LIVE badge and the "Key steps" control were
            both removed from this header: a live run already animates and the
            duration counts up on its own, so the badge was redundant, and the
            milestone filter was one more control competing with the text. */}
        <div className="apt-head-right">
          {elapsedMs !== null ? (
            <span
              className="apt-count"
              title={`${all.length} step${all.length === 1 ? "" : "s"}`}
            >
              Worked for {formatDuration(elapsedMs)}
            </span>
          ) : null}
        </div>
      </header>

      {/* No height animation on the body. `overflow: hidden` is required to
          clip such an animation, so while the animated height lagged the real
          content, arriving frames were clipped away and the panel flashed
          blank — worst exactly when frames arrived fastest. The body is no
          longer a fixed-height scroller, so animating its height bought
          nothing. Opacity plus a small offset reveals it without ever hiding
          rows. */}
      <AnimatePresence initial={false}>
        {expanded ? (
          <motion.div
            id="apt-body"
            key="body"
            className="apt-body"
            initial={reduce ? false : { opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
          >
            {hidden > 0 ? (
              <p className="apt-hidden-note">
                {hidden} earlier step{hidden === 1 ? "" : "s"} not shown
              </p>
            ) : null}

            {steps.length === 0 ? (
              <p className="apt-empty">
                {status === "running"
                  ? "Starting the research run…"
                  : "No pipeline steps were recorded for this run."}
              </p>
            ) : (
              <ol
                className="apt-flow"
                aria-live="polite"
                aria-relevant="additions"
                aria-label="Agent pipeline steps"
              >
                {steps.map((s, i) => (
                  <Row
                    key={s.id}
                    step={s}
                    open={isOpen(s.id)}
                    onToggle={toggleStep}
                    index={i}
                    total={steps.length}
                    renderContent={renderContent}
                  />
                ))}
              </ol>
            )}

            {/* The stage lives at the BOTTOM of the trace, as a status line
                under the last row — where the eye already is while reading a
                live run, and where it reads as "this is where it is now"
                rather than competing with the elapsed time in the header.
                Derived from the last real frame, so it cannot claim a stage
                the run never reached. */}
            {running && stage ? (
              <p className="apt-stage" data-stage={stage.id} key={stage.id}>
                <span className="apt-stage-dot" aria-hidden="true" />
                {stage.label}
              </p>
            ) : null}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </section>
  );
}

export type { StepStatus };