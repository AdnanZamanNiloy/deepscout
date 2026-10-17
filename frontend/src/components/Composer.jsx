import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { MODE_META } from "../lib";
import { IS_DEMO } from "../demo";
import { IconArrowUp, IconCheck, IconChevronDown, IconStop } from "./icons";

const MODES = ["quick", "standard", "deep"];

/* Gap between the selector and the dropdown, in px. */
const MENU_GAP = 6;

export default function Composer({
  value, onChange, onSubmit, running, onAbort, mode, onModeChange, placeholder, hitl, onHitlChange,
}) {
  const [open, setOpen] = useState(false);
  // "down" opens below the selector (preferred); "up" flips above it when the
  // viewport below is too short. Chosen from measured space, never hardcoded.
  const [placement, setPlacement] = useState("down");
  const menuRef = useRef(null);
  const panelRef = useRef(null);
  const areaRef = useRef(null);

  // Decide the open direction from the selector's rect and the menu's real
  // height. Prefers below; flips above only when below cannot fit the menu
  // but above can. Recalculated on resize/scroll while open.
  const place = useCallback(() => {
    const wrap = menuRef.current;
    const panel = panelRef.current;
    if (!wrap || !panel) return;
    const rect = wrap.getBoundingClientRect();
    const menuHeight = panel.offsetHeight || 0;
    const spaceBelow = window.innerHeight - rect.bottom - MENU_GAP;
    const spaceAbove = rect.top - MENU_GAP;
    if (spaceBelow < menuHeight && spaceAbove > spaceBelow) {
      setPlacement("up");
    } else {
      setPlacement("down");
    }
  }, []);

  useLayoutEffect(() => {
    if (open) place();
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const onReflow = () => place();
    window.addEventListener("resize", onReflow);
    window.addEventListener("scroll", onReflow, true);
    return () => {
      window.removeEventListener("resize", onReflow);
      window.removeEventListener("scroll", onReflow, true);
    };
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    const el = areaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 150)}px`;
    }
  }, [value]);

  /* A static demo cannot answer a new question: there is no backend behind it.
   * Sending is disabled so a visitor is never handed a RECORDED run in reply to
   * a question they actually asked: the answer would be true and about
   * something else, which is worse than no answer. The rail's real chats remain
   * fully explorable, so the demo still shows the product doing real work. */
  const demoLocked = IS_DEMO;
  const canSend = !demoLocked && value.trim().length >= 5 && !running;

  const submit = () => {
    const v = value.trim();
    if (demoLocked || v.length < 5 || running) return;
    onSubmit(v);
  };

  return (
    <div className="composer">
      <textarea
        ref={areaRef}
        rows={1}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            // While a run is streaming the draft cannot be sent, so Enter must
            // not be swallowed — it inserts a newline and the draft keeps
            // building. Only intercept it when a send will actually happen.
            if (running || demoLocked) return;
            e.preventDefault();
            submit();
          }
        }}
        placeholder={
          running
            ? "Type your next question… stop the run to send"
            : demoLocked
              /* Say what is really possible rather than inviting a send that
                 cannot happen. */
              ? "Open a recorded research chat on the left…"
              : placeholder || "Ask a research question…"
        }
        /* Editable while a run streams. Disabling the field stopped the user
           drafting a follow-up while waiting — they could only watch. Sending
           is still gated (canSend / submit), so a draft cannot jump the queue
           and silently queue behind the active run. */
        aria-label="Research question"
      />
      {demoLocked && !running ? (
        <p className="composer-hint" role="status">
          Static demo: no backend, so only the recorded chats can be opened.
        </p>
      ) : running && value.trim().length >= 5 ? (
        <p className="composer-hint" role="status">
          Stop the current run to send this question.
        </p>
      ) : null}
      <div className="composer-toolbar">
        <div className="mode-wrap" ref={menuRef}>
          <button
            type="button"
            className="pill-btn"
            onClick={() => setOpen((o) => !o)}
            disabled={running}
            aria-expanded={open}
            aria-haspopup="menu"
            title="Research mode — changes how many report sections are planned and the run's time budget"
          >
            {MODE_META[mode]?.label || mode}
            <IconChevronDown size={13} />
          </button>
          {open && !running ? (
            <div
              ref={panelRef}
              className={`mode-menu mode-menu-${placement}`}
              role="menu"
              aria-label="Research mode"
            >
              {MODES.map((m) => (
                <button
                  key={m}
                  type="button"
                  role="menuitemradio"
                  aria-checked={m === mode}
                  className={m === mode ? "picked" : ""}
                  onClick={() => { onModeChange(m); setOpen(false); }}
                >
                  <span>
                    {MODE_META[m].label}
                    <small>{MODE_META[m].hint}</small>
                  </span>
                  {m === mode ? <IconCheck size={13} /> : null}
                </button>
              ))}
            </div>
          ) : null}
        </div>
        <button
          type="button"
          className="pill-btn hitl-toggle"
          role="switch"
          aria-checked={hitl === true}
          onClick={() => onHitlChange?.(!hitl)}
          disabled={running}
          title={
            hitl === true
              ? "On — you approve the outline before research starts"
              : "Off — research starts as soon as the plan is ready"
          }
        >
          {/* The mark slot is always in the DOM (hidden when off) so turning
              the switch on cannot reflow the toolbar. */}
          <span className="hitl-mark" aria-hidden="true">
            {hitl === true ? <IconCheck size={12} strokeWidth={3} /> : null}
          </span>
          Review plan first
        </button>
        <span className="toolbar-spacer" />
        {running ? (
          <button type="button" className="send-btn is-abort" onClick={onAbort} title="Stop this run" aria-label="Stop this run">
            <IconStop size={14} />
          </button>
        ) : (
          <button
            type="button"
            className="send-btn"
            onClick={submit}
            disabled={!canSend}
            title={canSend ? "Send (Enter)"
              : demoLocked ? "Sending is disabled in the static demo"
                : "Type at least 5 characters"}
            aria-label="Send research question"
          >
            <IconArrowUp size={15} />
          </button>
        )}
      </div>
    </div>
  );
}
