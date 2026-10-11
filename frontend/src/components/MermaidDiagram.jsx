import { useEffect, useRef, useState } from "react";
import mermaid from "mermaid";

/* Renders a mermaid diagram fence as an SVG figure.
 *
 * The writer emits a ```mermaid block; without this it would print as raw
 * diagram source. Rendering is best-effort: a malformed diagram (models emit
 * them) falls back to the labelled source block rather than showing a broken
 * figure or crashing the report. mermaid is initialized once, lazily, with the
 * panel theme so diagrams match light/dark. */

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

export default function MermaidDiagram({ source }) {
  const [svg, setSvg] = useState("");
  const [failed, setFailed] = useState(false);
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
    <figure className="answer-figure">
      <div
        ref={hostRef}
        className="answer-mermaid"
        // mermaid returns sanitized SVG (securityLevel: strict).
        dangerouslySetInnerHTML={{ __html: svg }}
      />
    </figure>
  );
}
