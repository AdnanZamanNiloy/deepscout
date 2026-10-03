import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  useCallback,
  useEffect,
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

export type { TraceStep, WireEvent } from "./events";

export type AgentPipelineTraceProps = {
  /** Live NDJSON frames from the backend, oldest first. */
  events: readonly WireEvent[];
  /** Overall run state; drives the header summary. */
  status?: "idle" | "running" | "done" | "error";
  /** Start collapsed (e.g. for a finished run the reader already scanned). */
  defaultOpen?: boolean;
  /** Render prop for step bodies that need rich content. */
  renderContent?: (step: TraceStep) => ReactNode;
  /** Cap on rendered steps; older ones collapse into a count. */
  maxSteps?: number;
  className?: string;
};

const STAGGER = 0.045;

/** One line in the rail. */
function Step({
  step,
  depth,
  open,
  onToggle,
  index,
  renderContent,
}: {
  step: TraceStep;
  depth: number;
  open: boolean;
  onToggle: (id: string) => void;
  index: number;
  renderContent?: (s: TraceStep) => ReactNode;
}) {
  const isLong = step.content.length > 150 || Boolean(step.chips?.length) || Boolean(step.children?.length);
  const expandable = isLong || Boolean(step.children?.length);

  return (
    <motion.li
      className={`apt-step apt-step-${step.status}`}
      data-kind={step.kind}
      style={{ ["--apt-depth" as string]: depth }}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, delay: Math.min(index, 12) * STAGGER, ease: [0.22, 1, 0.36, 1] }}
    >
      <div className="apt-step-rail" aria-hidden="true">
        <span className="apt-dot" />
      </div>

      <div className="apt-step-main">
        <div className="apt-step-head">
          <span className="apt-kind" data-kind={step.kind}>
            {kindLabel(step.kind)}
          </span>
          <button
            type="button"
            className="apt-step-title"
            onClick={() => expandable && onToggle(step.id)}
            aria-expanded={expandable ? open : undefined}
            disabled={!expandable}
            title={expandable ? (open ? "Collapse" : "Expand") : undefined}
          >
            {step.title}
          </button>
          <time className="apt-time" dateTime={new Date(step.timestamp).toISOString()}>
            {formatTime(step.timestamp)}
          </time>
        </div>

        {step.content ? (
          <div className="apt-step-body">
            {renderContent ? renderContent(step) : <p>{step.content}</p>}
          </div>
        ) : null}

        {step.error ? <p className="apt-step-error">{step.error}</p> : null}

        <AnimatePresence initial={false}>
          {open && step.chips?.length ? (
            <motion.div
              className="apt-chips"
              key="chips"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
            >
              {step.chips.map((c) => (
                <Chip key={c.id} chip={c} />
              ))}
            </motion.div>
          ) : null}
        </AnimatePresence>

        <AnimatePresence initial={false}>
          {open && step.children?.length ? (
            <motion.ul
              className="apt-children"
              key="children"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
            >
              {step.children.map((c, i) => (
                <Step
                  key={c.id}
                  step={c}
                  depth={depth + 1}
                  open={open}
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

function Chip({ chip }: { chip: TraceChip }) {
  const safe = /^https?:\/\//i.test(chip.url);
  return (
    <a
      className="apt-chip"
      href={safe ? chip.url : undefined}
      target="_blank"
      rel="noreferrer noopener"
      title={chip.url}
    >
      {chip.label}
    </a>
  );
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
  const [lessSteps, setLessSteps] = useState(false);
  const [openIds, setOpenIds] = useState<Set<string>>(() => new Set());
  const reduce = useReducedMotion();
  const listRef = useRef<HTMLOListElement | null>(null);
  const pinnedRef = useRef(true);

  const all = useMemo(() => buildTrace(events), [events]);
  const steps = useMemo(
    () => (lessSteps ? summarize(all) : all).slice(-maxSteps),
    [all, lessSteps, maxSteps],
  );
  const hidden = all.length - steps.length;

  const toggleStep = useCallback((id: string) => {
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  // Seed newly finished steps as open so their content is visible without a click.
  useEffect(() => {
    setOpenIds((prev) => {
      const next = new Set(prev);
      let changed = false;
      for (const s of all) {
        const rich = s.content.length > 150 || s.chips?.length || s.children?.length;
        if (rich && !next.has(s.id) && !next.has(`-${s.id}`)) {
          next.add(`-${s.id}`);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [all]);

  // Open state is stored as `id` (user) or `-id` (auto); a user toggle wins.
  const isOpen = useCallback(
    (id: string) => openIds.has(id) || (openIds.has(`-${id}`) && !openIds.has(`~${id}`)),
    [openIds],
  );

  useLayoutEffect(() => {
    // Auto-follow the newest step only while the reader is at the bottom.
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
          <span>{expanded ? "Less steps" : "Show pipeline trace"}</span>
          {running ? <span className="apt-live">live</span> : null}
        </button>

        <div className="apt-head-right">
          <span className="apt-count">
            {all.length} step{all.length === 1 ? "" : "s"}
          </span>
          <button
            type="button"
            className="apt-minor-toggle"
            onClick={() => setLessSteps((v) => !v)}
            aria-pressed={lessSteps}
            title="Hide intermediate reasoning and keep only milestones"
          >
            Less steps
          </button>
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
            transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
          >
            {hidden > 0 ? (
              <p className="apt-hidden-note">{hidden} earlier step{hidden === 1 ? "" : "s"} not shown</p>
            ) : null}

            {steps.length === 0 ? (
              <p className="apt-empty">Waiting for the first step…</p>
            ) : (
              <ol
                className="apt-steps"
                ref={listRef}
                onScroll={onScroll}
                // Announce new steps without stealing focus.
                aria-live="polite"
                aria-relevant="additions"
                aria-label="Agent pipeline steps"
              >
                {steps.map((s, i) => (
                  <Step
                    key={s.id}
                    step={s}
                    depth={0}
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

function kindLabel(kind: StepKind): string {
  switch (kind) {
    case "thinking": return "Think";
    case "todo": return "To-do";
    case "tool": return "Tool";
    case "evidence": return "Evidence";
    case "gate": return "Gate";
    case "final": return "Answer";
  }
}

function formatTime(ts: number): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export type { StepStatus };