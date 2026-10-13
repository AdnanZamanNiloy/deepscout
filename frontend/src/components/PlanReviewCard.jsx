import { useEffect, useState } from "react";
import {
  IconArrowRight,
  IconCheck,
  IconChevronLeft,
  IconChevronRight,
  IconPencil,
  IconX,
} from "./icons";

/* Seconds left until the server's review deadline (never negative). */
function secondsLeft(deadlineTs) {
  if (!Number.isFinite(deadlineTs)) return Infinity;
  return Math.max(0, Math.ceil(deadlineTs - Date.now() / 1000));
}

/* m:ss, so a five-minute window reads as a clock, not as four digits. */
function formatCountdown(seconds) {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/* Human-in-the-loop plan review.
 *
 * Shown when a run has paused after planning and is waiting for the user to
 * approve the proposed research outline before section research begins.
 *
 * The shape is a step card: one proposed outline per step, its sections as
 * multi-select rows, a selection counter, and navigation in the header. The
 * user can:
 *   - step between the proposed outlines (header arrows + counter),
 *   - tick/untick the sections to research (rows are checkbox rows),
 *   - append their own section ("Something else"),
 *   - add a free-text direction for the research,
 *   - rewrite the outline in bulk ("Edit as text"),
 * then Start research (commits the outline) or Skip (asks the planner to
 * re-plan). Closing the card skips the review and starts immediately.
 *
 * The decision is POSTed to /api/research/{runId}/plan, which releases the
 * paused run. Every section starts ticked: approving the outline as proposed
 * is the default, and the user narrows it down rather than building it up.
 */
export default function PlanReviewCard({ review, onSubmit }) {
  const alternatives = review?.alternatives || [];
  const submitting = review?.submitting === true;
  const error = review?.error || "";

  /* The server owns the deadline. Counting down to it is the difference
   * between "choose a plan" and a silent five-minute hang that ends with a
   * report the user never asked for. */
  const deadlineTs = review?.deadlineTs;
  const [remaining, setRemaining] = useState(() => secondsLeft(deadlineTs));

  const [step, setStep] = useState(0);
  /* Per-outline working list (mutable: user may append/remove/rewrite) and
   * the set of ticked sections within it. Kept per step so stepping back and
   * forth never discards a choice. */
  const [lists, setLists] = useState(() => alternatives.map((alt) => [...alt]));
  const [picked, setPicked] = useState(() => alternatives.map((alt) => new Set(alt)));
  const [draft, setDraft] = useState("");
  const [feedback, setFeedback] = useState("");
  const [editing, setEditing] = useState(false);
  const [bulkText, setBulkText] = useState("");

  const total = alternatives.length;
  const list = lists[step] || [];
  const chosen = picked[step] || new Set();
  const proposed = new Set(alternatives[step] || []);
  const selected = list.filter((title) => chosen.has(title));
  const isLast = total <= 1 || step >= total - 1;
  /* Past the deadline the run continues on its own, so the card must stop
   * accepting choices that the backend will now reject. */
  const closed = Number.isFinite(deadlineTs) && remaining <= 0;
  const question =
    total > 1 ? "Which sections should the scout cover?" : "Approve the research outline?";

  useEffect(() => {
    if (!Number.isFinite(deadlineTs)) return undefined;
    const tick = () => setRemaining(secondsLeft(deadlineTs));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [deadlineTs]);

  // A new review — a new run, or the planner re-requesting review after the user
  // sent it back — restarts the walk from the first step with every section
  // ticked. Keyed on the request id: a revised outline reuses the run id, so
  // keeping the previous picks would approve sections that no longer exist.
  useEffect(() => {
    setStep(0);
    setLists(alternatives.map((alt) => [...alt]));
    setPicked(alternatives.map((alt) => new Set(alt)));
    setDraft("");
    setFeedback("");
    setEditing(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [review?.requestId, review?.runId, total]);

  const editList = (next) => {
    setLists((prev) => {
      const copy = prev.slice();
      copy[step] = next;
      return copy;
    });
  };

  const editPicked = (next) => {
    setPicked((prev) => {
      const copy = prev.slice();
      copy[step] = next;
      return copy;
    });
  };

  const toggle = (title) => {
    const next = new Set(chosen);
    if (next.has(title)) next.delete(title);
    else next.add(title);
    editPicked(next);
  };

  const remove = (title) => {
    editList(list.filter((t) => t !== title));
    const next = new Set(chosen);
    next.delete(title);
    editPicked(next);
  };

  const addDraft = () => {
    const title = draft.trim();
    if (!title || list.includes(title)) return;
    editList([...list, title]);
    editPicked(new Set([...chosen, title]));
    setDraft("");
  };

  const applyBulk = () => {
    const lines = bulkText
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    if (!lines.length) return;
    editList(lines);
    editPicked(new Set(lines));
    setEditing(false);
  };

  const openBulk = () => {
    setBulkText(list.join("\n"));
    setEditing(true);
  };

  const approve = () =>
    onSubmit({ action: "approve", sections: selected, feedback: feedback.trim() });

  const sendBack = () =>
    onSubmit({ action: "revise", sections: [], feedback: feedback.trim() });

  return (
    <section className="plan-review" aria-label="Plan review">
      <header className="pr-head">
        <div className="pr-head-text">
          <h3 className="pr-question">{question}</h3>
          {review?.title ? <p className="pr-subject">{review.title}</p> : null}
          {Number.isFinite(deadlineTs) ? (
            <p className="pr-deadline" data-closed={closed} role="status">
              {closed
                ? "Review window closed — continuing automatically."
                : `Starts automatically in ${formatCountdown(remaining)} if you don't choose.`}
            </p>
          ) : null}
        </div>
        <div className="pr-nav">
          <button
            type="button"
            className="pr-icon"
            onClick={openBulk}
            disabled={submitting || closed}
            aria-expanded={editing}
            title="Edit the outline as text"
            aria-label="Edit the outline as text"
          >
            <IconPencil size={14} />
          </button>
          {total > 1 ? (
            <>
              <button
                type="button"
                className="pr-icon"
                onClick={() => setStep((s) => Math.max(0, s - 1))}
                disabled={submitting || closed || step === 0}
                title="Previous outline"
                aria-label="Previous outline"
              >
                <IconChevronLeft size={15} />
              </button>
              <span className="pr-step" aria-live="polite">
                {step + 1} of {total}
              </span>
              <button
                type="button"
                className="pr-icon"
                onClick={() => setStep((s) => Math.min(total - 1, s + 1))}
                disabled={submitting || closed || isLast}
                title="Next outline"
                aria-label="Next outline"
              >
                <IconChevronRight size={15} />
              </button>
            </>
          ) : null}
          <button
            type="button"
            className="pr-icon"
            onClick={approve}
            disabled={submitting || closed}
            title="Skip the review and start research with this outline"
            aria-label="Skip the review and start research with this outline"
          >
            <IconX size={15} />
          </button>
        </div>
      </header>

      <div className="pr-options" role="group" aria-label="Proposed sections">
        {list.map((title) => (
          <div key={title} className="pr-row" data-extra={!proposed.has(title)}>
            <label className="pr-opt">
              <input
                type="checkbox"
                checked={chosen.has(title)}
                onChange={() => toggle(title)}
                disabled={submitting || closed}
              />
              <span className="pr-box" aria-hidden="true">
                {chosen.has(title) ? <IconCheck size={12} strokeWidth={3} /> : null}
              </span>
              <span className="pr-opt-text">{title}</span>
            </label>
            {proposed.has(title) ? null : (
              <button
                type="button"
                className="pr-remove"
                onClick={() => remove(title)}
                disabled={submitting || closed}
                title="Remove this section"
                aria-label={`Remove ${title}`}
              >
                <IconX size={13} />
              </button>
            )}
          </div>
        ))}

        <div className="pr-row pr-row-group">
          <span className="pr-box pr-box-empty" aria-hidden="true" />
          <input
            className="pr-add"
            value={draft}
            placeholder="Something else"
            aria-label="Add your own section"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== "Enter") return;
              e.preventDefault();
              addDraft();
            }}
            onBlur={addDraft}
            disabled={submitting || closed}
          />
        </div>

        <div className="pr-row pr-row-group">
          <input
            className="pr-add"
            value={feedback}
            placeholder="Anything the research should emphasise? (optional)"
            aria-label="What should the research emphasise?"
            onChange={(e) => setFeedback(e.target.value)}
            disabled={submitting || closed}
          />
        </div>
      </div>

      {editing ? (
        <div className="pr-bulk">
          <label className="pr-bulk-label" htmlFor="pr-sections">
            One section per line
          </label>
          <textarea
            id="pr-sections"
            className="pr-sections"
            value={bulkText}
            onChange={(e) => setBulkText(e.target.value)}
            rows={Math.max(4, bulkText.split("\n").length + 1)}
            disabled={submitting || closed}
          />
          <div className="pr-bulk-actions">
            <button type="button" className="btn btn-sm" onClick={() => setEditing(false)} disabled={submitting || closed}>
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={applyBulk}
              disabled={submitting || closed || !bulkText.trim()}
            >
              Apply outline
            </button>
          </div>
        </div>
      ) : null}

      {error ? <p className="pr-error">{error}</p> : null}

      <footer className="pr-foot">
        <span className="pr-count">{selected.length} selected</span>
        <div className="pr-foot-actions">
          <button
            type="button"
            className="pr-skip"
            onClick={sendBack}
            disabled={submitting || closed}
            title="Skip this outline and ask the planner for a different one"
          >
            Skip
          </button>
          {isLast ? (
            <button
              type="button"
              className="pr-go pr-go-wide"
              onClick={approve}
              disabled={submitting || closed || !selected.length}
            >
              {submitting ? "Starting…" : "Start research"}
            </button>
          ) : (
            <button
              type="button"
              className="pr-go"
              onClick={() => setStep((s) => Math.min(total - 1, s + 1))}
              disabled={submitting || closed}
              title="Next outline"
              aria-label="Next outline"
            >
              <IconArrowRight size={16} />
            </button>
          )}
        </div>
      </footer>
    </section>
  );
}