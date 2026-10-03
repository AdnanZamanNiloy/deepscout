import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  useCallback,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  buildTrace,
  summarize,
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
  /** Hide intermediate reasoning, keeping only milestones. */
  keyStepsOnly?: boolean;
  onKeyStepsOnlyChange?: (on: boolean) => void;
  /** Render prop for a step body that needs richer content. */
  renderContent?: (step: TraceStep) => ReactNode;
  /** Cap on rendered rows; older ones collapse into a count. */
  maxSteps?: number;
  className?: string;
};

const STAGGER = 0.04;

/** Prose longer than this is clamped behind a toggle. */
const CLAMP_CHARS = 320;

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
  renderContent,
}: {
  step: TraceStep;
  open: boolean;
  onToggle: (id: string) => void;
  index: number;
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
  const tooLong = step.content.length > CLAMP_CHARS;
  const clamped = tooLong && !open;
  const shown = clamped ? `${step.content.slice(0, CLAMP_CHARS).trimEnd()}…` : step.content;

  return (
    <motion.li
      className="apt-row"
      data-kind={step.kind}
      data-status={step.status}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration: 0.26,
        delay: Math.min(index, 14) * STAGGER,
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
            onClick={() => (tooLong || step.chips?.length ? onToggle(step.id) : undefined)}
            aria-expanded={tooLong || step.chips?.length ? open : undefined}
            disabled={!tooLong && !step.chips?.length}
            tabIndex={!tooLong && !step.chips?.length ? -1 : undefined}
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
            {(clamped ? [shown] : paragraphs).map((p, i) => (
              <p
                className={isFinal ? "apt-prose apt-pre" : "apt-prose"}
                key={`${step.id}-p${i}`}
              >
                <Prose text={p} />
              </p>
            ))}
            {tooLong ? (
              <button
                type="button"
                className="apt-row-toggle"
                onClick={() => onToggle(step.id)}
                aria-expanded={open}
              >
                <span className="apt-muted">
                  {open ? "Show less" : `Show ${step.content.length - CLAMP_CHARS} more characters`}
                </span>
              </button>
            ) : null}
          </div>
        ) : null}

        {step.error ? <p className="apt-prose">{step.error}</p> : null}

        <AnimatePresence initial={false}>
          {open && step.chips?.length ? (
            <motion.div
              key="chips"
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

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function AgentPipelineTrace({
  events,
  status = "idle",
  defaultOpen = true,
  keyStepsOnly = false,
  onKeyStepsOnlyChange,
  renderContent,
  maxSteps = 100,
  className,
}: AgentPipelineTraceProps) {
  const [expanded, setExpanded] = useState(defaultOpen);
  const [openOverride, setOpenOverride] = useState<Record<string, boolean>>({});
  const reduce = useReducedMotion();
  const listRef = useRef<HTMLOListElement | null>(null);
  const pinnedRef = useRef(true);

  const all = useMemo(() => buildTrace(events), [events]);
  const filtered = useMemo(
    () => (keyStepsOnly ? summarize(all) : all),
    [all, keyStepsOnly],
  );
  const steps = useMemo(() => filtered.slice(-maxSteps), [filtered, maxSteps]);
  const hidden = filtered.length - steps.length;

  const toggleStep = useCallback((id: string) => {
    setOpenOverride((prev) => ({ ...prev, [id]: !defaultOpenFor(id) }));
  }, [all]);

  // Open-ness is DERIVED during render rather than seeded by an effect. An
  // effect only runs in the browser, so server-rendered and first-paint markup
  // came out collapsed and every source chip flashed shut before appearing.
  const defaultOpenFor = useCallback(
    (id: string) => {
      const s = all.find((x) => x.id === id);
      if (!s || s.kind === "final") return false;
      return Boolean(s.chips?.length) || s.content.length > CLAMP_CHARS;
    },
    [all],
  );

  const isOpen = useCallback(
    (id: string) => (id in openOverride ? openOverride[id] : defaultOpenFor(id)),
    [openOverride, defaultOpenFor],
  );

  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el || !pinnedRef.current || !expanded) return;
    el.scrollTop = el.scrollHeight;
  }, [steps.length, expanded]);

  const onScroll = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  }, []);

  const running = status === "running";

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

        <div className="apt-head-right">
          {running ? <span className="apt-live">live</span> : null}
          <span className="apt-count">
            {filtered.length} step{filtered.length === 1 ? "" : "s"}
          </span>
          {onKeyStepsOnlyChange ? (
            <button
              type="button"
              className="apt-keytoggle"
              onClick={() => onKeyStepsOnlyChange(!keyStepsOnly)}
              aria-pressed={keyStepsOnly}
              title="Hide intermediate reasoning and keep only milestones"
            >
              Key steps
            </button>
          ) : null}
        </div>
      </header>

      <AnimatePresence initial={false}>
        {expanded ? (
          <motion.div
            id="apt-body"
            key="body"
            className="apt-body"
            initial={reduce ? false : { height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={reduce ? { opacity: 0 } : { height: 0, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
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
                ref={listRef}
                onScroll={onScroll}
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
                    renderContent={renderContent}
                  />
                ))}
              </ol>
            )}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </section>
  );
}

export type { StepStatus };