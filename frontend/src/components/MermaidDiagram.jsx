import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import mermaid from "mermaid";
import { IconX } from "./icons";

/* Renders a mermaid diagram fence as an SVG figure.
 *
 * The writer emits a ```mermaid block; without this it would print as raw
 * diagram source. Rendering is best-effort: a malformed diagram (models emit
 * them) falls back to the labelled source block rather than showing a broken
 * figure or crashing the report. mermaid is initialized once, lazily, with the
 * panel theme so diagrams match light/dark.
 *
 * Diagrams are routinely wider than the answer column, where the inline SVG
 * scales down until labels stop being readable. The fullscreen viewer opens
 * the same rendered SVG over an overlay with zoom (wheel / pinch / buttons)
 * and pan (drag / keyboard), so the reader can inspect a dense diagram at its
 * real size instead of squinting at a 40%-scale thumbnail.
 */

let initialized = false;
let renderSeq = 0;

function ensureInit() {
  if (initialized) return;
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: "neutral",
    fontFamily: "inherit",
  });
  initialized = true;
}

const MIN_SCALE = 0.2;
const MAX_SCALE = 8;
const clampScale = (s) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, s));

export default function MermaidDiagram({ source }) {
  const [svg, setSvg] = useState("");
  const [failed, setFailed] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const hostRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    const code = String(source || "").trim();
    if (!code) {
      setFailed(true);
      return () => { cancelled = true; };
    }
    ensureInit();
    const id = `mermaid-${Date.now()}-${renderSeq++}`;
    mermaid
      .render(id, code)
      .then(({ svg: out }) => {
        if (!cancelled) setSvg(out);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => { cancelled = true; };
  }, [source]);

  const openFullscreen = useCallback(() => setFullscreen(true), []);

  if (failed || !svg) {
    // Honest fallback: show the diagram source, clearly labelled, not as a
    // figure that silently failed to render.
    return (
      <figure className="answer-figure answer-figure--source">
        {svg ? null : <figcaption className="answer-figure-cap">Diagram</figcaption>}
        <pre className="answer-code"><code>{String(source || "").trim()}</code></pre>
      </figure>
    );
  }

  return (
    <>
      <figure className="answer-figure">
        <button
          type="button"
          className="answer-figure-expand"
          onClick={openFullscreen}
          title="Open fullscreen"
          aria-label="Open diagram fullscreen"
        >
          Expand
        </button>
        <div
          ref={hostRef}
          className="answer-mermaid"
          // Clicking the diagram opens the same viewer, so the affordance is
          // where the reader's attention already is.
          onClick={openFullscreen}
          // mermaid returns sanitized SVG (securityLevel: strict).
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      </figure>
      {fullscreen ? (
        <DiagramViewer svg={svg} onClose={() => setFullscreen(false)} />
      ) : null}
    </>
  );
}

/* Fullscreen diagram viewer: zoom + pan over the already-rendered SVG.
 *
 * Deliberately dependency-free. `svg` is the sanitized markup mermaid produced
 * for the inline figure — the same string is reused here, so what the reader
 * zooms into is exactly what the report rendered, not a re-parse.
 *
 * Zoom anchors on the pointer so wheel-zooming magnifies what is under the
 * cursor; pan is drag with a pointer capture so a fast drag cannot escape the
 * overlay. Scale/offset reset on open and on "Reset", and Escape closes.
 */
function DiagramViewer({ svg, onClose }) {
  // One atomic transform. Scale and offset must change together when zooming
  // (the pan compensates so the point under the cursor stays put); splitting
  // them across two useStates meant one updater had to call the other, which
  // is a render-phase side effect React runs twice under StrictMode — the
  // offset shift doubled and pointer-anchored zoom drifted.
  const [view, setView] = useState({ scale: 1, x: 0, y: 0 });
  const { scale } = view;
  // Mirrored in state (not only in the ref) because the cursor has to react
  // to it: a ref mutation does not re-render, so the stage would keep the
  // grab cursor mid-drag.
  const [dragging, setDragging] = useState(false);
  const stageRef = useRef(null);
  const dragRef = useRef(null);
  const pinchRef = useRef(null);

  const reset = useCallback(() => {
    setView({ scale: 1, x: 0, y: 0 });
  }, []);

  /** Zoom about a viewport point: the scale changes, that point stays put. */
  const zoomAt = useCallback((nextScale, clientX, clientY) => {
    setView((prev) => {
      const next = clampScale(nextScale);
      if (next === prev.scale) return prev;
      const box = stageRef.current?.getBoundingClientRect();
      const cx = clientX ?? (box ? box.left + box.width / 2 : 0);
      const cy = clientY ?? (box ? box.top + box.height / 2 : 0);
      // Pointer offset from the stage centre, in stage coordinates.
      const originX = box ? cx - (box.left + box.width / 2) : 0;
      const originY = box ? cy - (box.top + box.height / 2) : 0;
      const ratio = next / prev.scale;
      return {
        scale: next,
        x: originX - (originX - prev.x) * ratio,
        y: originY - (originY - prev.y) * ratio,
      };
    });
  }, []);

  // Wheel zoom. Registered non-passively so the page behind the overlay does
  // not scroll. It reads the live scale from a ref rather than the render
  // closure: rapid wheel events would otherwise compute from a stale scale
  // and the zoom would stall.
  const viewRef = useRef(view);
  viewRef.current = view;
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return undefined;
    const onWheel = (ev) => {
      ev.preventDefault();
      const factor = Math.exp(-ev.deltaY * 0.0015);
      zoomAt(viewRef.current.scale * factor, ev.clientX, ev.clientY);
    };
    stage.addEventListener("wheel", onWheel, { passive: false });
    return () => stage.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  // Escape closes; +/−/0 drive zoom from the keyboard. Arrow keys pan.
  useEffect(() => {
    const onKey = (ev) => {
      if (ev.key === "Escape") {
        ev.preventDefault();
        onClose();
        return;
      }
      if (ev.key === "+" || ev.key === "=") {
        ev.preventDefault();
        zoomAt(scale * 1.25);
        return;
      }
      if (ev.key === "-" || ev.key === "_") {
        ev.preventDefault();
        zoomAt(scale / 1.25);
        return;
      }
      if (ev.key === "0") {
        ev.preventDefault();
        reset();
        return;
      }
      const step = 60;
      const pan = (dx, dy) => setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy }));
      if (ev.key === "ArrowLeft") pan(-step, 0);
      else if (ev.key === "ArrowRight") pan(step, 0);
      else if (ev.key === "ArrowUp") pan(0, -step);
      else if (ev.key === "ArrowDown") pan(0, step);
      else return;
      ev.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, reset, scale, zoomAt]);

  // Lock the page scroll behind the overlay, so the modal behaves like a modal.
  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previous; };
  }, []);

  // Pan + pinch, driven entirely by POINTER events (one code path for mouse,
  // pen and touch). Active pointers are tracked by pointerId; the second one
  // landing turns a pan into a pinch.
  const pointersRef = useRef(new Map());

  const onPointerDown = (ev) => {
    pointersRef.current.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    ev.currentTarget.setPointerCapture?.(ev.pointerId);

    if (pointersRef.current.size >= 2) {
      // Second finger: stop panning, start pinching from this geometry.
      const [a, b] = [...pointersRef.current.values()];
      dragRef.current = null;
      setDragging(false);
      pinchRef.current = {
        startDistance: Math.hypot(a.x - b.x, a.y - b.y),
        startScale: scale,
      };
      return;
    }
    dragRef.current = {
      pointerId: ev.pointerId,
      startX: ev.clientX,
      startY: ev.clientY,
      originX: viewRef.current.x,
      originY: viewRef.current.y,
    };
    setDragging(true);
  };

  const onPointerMove = (ev) => {
    if (!pointersRef.current.has(ev.pointerId)) return;
    pointersRef.current.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });

    const pinch = pinchRef.current;
    if (pinch && pointersRef.current.size >= 2) {
      const [a, b] = [...pointersRef.current.values()];
      const distance = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinch.startDistance > 0) {
        zoomAt(
          pinch.startScale * (distance / pinch.startDistance),
          (a.x + b.x) / 2,
          (a.y + b.y) / 2,
        );
      }
      return;
    }

    const drag = dragRef.current;
    if (!drag || drag.pointerId !== ev.pointerId) return;
    setView((v) => ({
      ...v,
      x: drag.originX + (ev.clientX - drag.startX),
      y: drag.originY + (ev.clientY - drag.startY),
    }));
  };

  const endPointer = (ev) => {
    pointersRef.current.delete(ev.pointerId);
    // A pinch ends the moment either finger lifts; the next move re-anchors
    // the pan from the current transform so nothing jumps.
    if (pointersRef.current.size < 2) pinchRef.current = null;
    const drag = dragRef.current;
    if (drag && drag.pointerId === ev.pointerId) {
      ev.currentTarget.releasePointerCapture?.(ev.pointerId);
      dragRef.current = null;
      setDragging(false);
    }
    if (pointersRef.current.size === 1 && !dragRef.current) {
      const [id] = [...pointersRef.current.keys()];
      const p = pointersRef.current.get(id);
      dragRef.current = {
        pointerId: id,
        startX: p.x,
        startY: p.y,
        originX: viewRef.current.x,
        originY: viewRef.current.y,
      };
      setDragging(true);
    }
  };

  // Double-click toggles between "fit" and 2×, the fastest way in and out.
  const onDoubleClick = (ev) => {
    if (scale > 1.05) reset();
    else zoomAt(2, ev.clientX, ev.clientY);
  };

  return createPortal(
    <div
      className="diagram-viewer"
      role="dialog"
      aria-modal="true"
      aria-label="Diagram fullscreen"
      // Clicking the backdrop (not the diagram) closes, as a reader expects
      // from a lightbox.
      onMouseDown={(ev) => { if (ev.target === ev.currentTarget) onClose(); }}
    >
      <div className="diagram-viewer-bar">
        <span className="diagram-viewer-zoom" aria-live="polite">
          {Math.round(scale * 100)}%
        </span>
        <div className="diagram-viewer-actions">
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => zoomAt(scale / 1.25)}
            disabled={scale <= MIN_SCALE}
            title="Zoom out (-)"
          >
            −
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => zoomAt(scale * 1.25)}
            disabled={scale >= MAX_SCALE}
            title="Zoom in (+)"
          >
            +
          </button>
          <button type="button" className="btn btn-sm" onClick={reset} title="Reset zoom (0)">
            Reset
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={onClose}
            title="Close (Esc)"
            aria-label="Close fullscreen diagram"
          >
            <IconX size={14} />
          </button>
        </div>
      </div>
      <div
        ref={stageRef}
        className="diagram-viewer-stage"
        style={{ cursor: dragging ? "grabbing" : "grab" }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endPointer}
        onPointerCancel={endPointer}
        onDoubleClick={onDoubleClick}
      >
        <div
          className="diagram-viewer-canvas"
          style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${scale})` }}
          /* Same sanitized SVG the inline figure rendered. */
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      </div>
      <p className="diagram-viewer-hint">
        Scroll or pinch to zoom · drag to pan · double-click to toggle · Esc to close
      </p>
    </div>,
    document.body,
  );
}
