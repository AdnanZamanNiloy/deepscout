import { createElement, type ReactNode } from "react";

/* Inline markdown, rendered to React nodes.
 *
 * ONE implementation, shared by the trace and the answer card. These two used
 * to carry separate renderers and the trace got the weaker one: it handled
 * only URLs and quoted spans, so `**bold**`, `*italic*` and `` `code` `` in a
 * streamed step reached the DOM as literal asterisks — the raw-markdown text
 * visible in the live trace. Two renderers for one job means one of them is
 * always behind; this module is the one.
 *
 * Design constraints, in order of importance:
 *
 * 1. NO RAW MARKERS. A half-arrived `**bold` must not flash as `**bold`. Any
 *    unterminated marker is emitted as literal text, which is the honest
 *    rendering of what has actually streamed so far — and is stable frame to
 *    frame, so the row does not flicker as the closing marker lands.
 * 2. SANITIZED. Nothing is passed to dangerouslySetInnerHTML; text nodes come
 *    from slicing the source string, so backend content cannot inject markup.
 *    Link hrefs are restricted to http/https by the regex itself.
 * 3. STABLE DURING STREAMING. Rendering is a pure function of the text, so an
 *    unchanged string produces an unchanged tree. Keys are positional.
 */

/* Links, bold, italic, code, then bare citation markers. Order matters:
 * `**` must be tried before `*`, and a link before either. */
const INLINE_RE =
  /(\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\))|(\*\*([^*\n]+)\*\*)|(\*([^*\n]+)\*)|(`([^`\n]+)`)|(\[(\d{1,3})\])/g;

/** Bare URLs, which the agents emit inline in trace steps. */
const URL_RE = /https?:\/\/[^\s<>()[\]"]+/g;

/* An opening marker with no partner: `**bold`, `` `code ``, `[text](url`.
 * Rendered as literal text so nothing is silently swallowed. */
function isUnterminated(src: string, from: number): boolean {
  const bold = src.startsWith("**", from);
  const star = src.startsWith("*", from);
  const tick = src.startsWith("`", from);
  if (!bold && !star && !tick) return false;
  const closer = bold ? "**" : star ? "*" : "`";
  // A closer that only exists BEFORE this position does not pair with it.
  return src.indexOf(closer, from + closer.length) === -1;
}

/**
 * Render inline markdown to an array of React nodes.
 *
 * Returns nodes (never a wrapper element) so callers can place them inside a
 * <p>, <li> or <td> without introducing invalid nesting.
 */
export function renderInline(text: string, keyBase = "i"): ReactNode[] {
  const nodes: ReactNode[] = [];
  // A stray <br> from a streaming fragment is a space, not literal text.
  const src = String(text ?? "").replace(/<br\s*\/?>/gi, " ");
  if (!src) return nodes;

  const re = new RegExp(INLINE_RE.source, "g");
  let last = 0;
  let k = 0;
  let m: RegExpExecArray | null;

  while ((m = re.exec(src)) !== null) {
    const whole = m[0];
    const at = m.index;
    if (isUnterminated(src, at)) continue; // leave it for the next chunk
    if (at > last) nodes.push(src.slice(last, at));
    const key = `${keyBase}-${k++}`;

    if (m[2] !== undefined && m[3] !== undefined) {
      nodes.push(
        createElement(
          "a",
          {
            key,
            href: m[3],
            target: "_blank",
            rel: "noreferrer noopener",
          },
          m[2],
        ),
      );
    } else if (m[5] !== undefined) {
      nodes.push(createElement("strong", { key }, m[5]));
    } else if (m[7] !== undefined) {
      nodes.push(createElement("em", { key }, m[7]));
    } else if (m[9] !== undefined) {
      nodes.push(createElement("code", { key }, m[9]));
    } else if (m[11] !== undefined) {
      nodes.push(createElement("sup", { key, className: "cite-ref" }, m[11]));
    }
    last = re.lastIndex;
  }
  if (last < src.length) nodes.push(src.slice(last));
  return nodes;
}

/** True when the text contains at least one inline marker worth parsing. */
export function hasInlineMarkdown(text: string): boolean {
  const src = String(text ?? "");
  return /\*\*[^*\n]+\*\*|\*[^*\n]+\*|`[^`\n]+`|\[[^\]\n]+\]\(https?:\/\//.test(src);
}

/**
 * Drop a markdown marker left dangling at the very end of a string.
 *
 * This exists for TRUNCATED content — an LLM response cut off mid-token, a
 * stream interrupted, a stored note that ends inside `**Lattice-based`. Such a
 * marker can never find its partner, so it would sit in the UI as literal
 * asterisks forever. Removing only the trailing opener is honest about what was
 * lost, and unlike leaving it, is stable.
 *
 * Deliberately narrow: only a marker that OPENS at the end is dropped, so
 * ordinary text containing a lone `*` or a stray backtick is untouched. While
 * text is still streaming, a brief trailing opener is normal and resolves as
 * the run continues — the renderer emits it literally until then, which is why
 * this runs on the final text and not on every frame.
 */
export function trimDanglingMarkers(text: string): string {
  let src = String(text ?? "").replace(/\s+$/, "");
  // Only an ODD number of a marker means one of them is unpaired — a truncated
  // span. A paired span has an even count and must be left completely alone:
  // stripping the closing backtick of "`ok`" corrupted correctly-formatted text.
  // Repeat, because removing one can expose another.
  for (let i = 0; i < 4; i += 1) {
    const before = src;

    // `**` op...  : unpaired emphasis leaves a bare "**" mid-sentence.
    if ((src.match(/\*\*/g) || []).length % 2 === 1) {
      const idx = src.lastIndexOf("**");
      src = (src.slice(0, idx) + src.slice(idx + 2)).replace(/\s+$/, "");
      continue;
    }

    // A truncated link "…[label](http://ex" prints its brackets, so drop the
    // whole unfinished link rather than the marker alone.
    const link = /\[[^\]\n]*\]\([^\s)]*$/.exec(src);
    if (link) {
      src = src.slice(0, link.index).replace(/\s+$/, "");
      continue;
    }

    // A lone ` or single * that cannot pair (odd count, and not part of a **).
    if ((src.match(/`/g) || []).length % 2 === 1) {
      const idx = src.lastIndexOf("`");
      src = (src.slice(0, idx) + src.slice(idx + 1)).replace(/\s+$/, "");
      continue;
    }
    // A lone single * is ambiguous: it is a valid emphasis opener AND a
    // legitimate literal character ("5*3", "see note*"). Only strip it when it
    // actually LOOKS like an opener — preceded by whitespace/start and followed
    // by a word character — so a literal asterisk in prose is never deleted.
    const starText = src.replace(/\*\*/g, "\u0000");
    if ((starText.match(/\*/g) || []).length % 2 === 1) {
      const openers = [...starText.matchAll(/(^|\s)\*(\w)/g)];
      if (openers.length) {
        const at = openers[openers.length - 1].index + openers[openers.length - 1][1].length;
        src = (src.slice(0, at) + src.slice(at + 1)).replace(/\s+$/, "");
        continue;
      }
    }

    if (src === before) break;
  }
  return src;
}

/** The ordered-list prefix of a line: "3. " or "3) ", else null. */
export function orderedItemIndex(line: string): number | null {
  const m = /^\s*(\d{1,3})[.)]\s+/.exec(String(line ?? ""));
  return m ? Number(m[1]) : null;
}

/** Strip an ordered-list prefix, returning the item body. */
export function stripOrderedItem(line: string): string {
  return String(line ?? "").replace(/^\s*\d{1,3}[.)]\s+/, "");
}

/** The bullet prefix of a line: "- ", "* ", "+ ", or null. */
export function bulletItem(line: string): boolean {
  return /^\s*[-*+]\s+/.test(String(line ?? ""));
}

/** Strip a bullet prefix, returning the item body. */
export function stripBullet(line: string): string {
  return String(line ?? "").replace(/^\s*[-*+]\s+/, "");
}
