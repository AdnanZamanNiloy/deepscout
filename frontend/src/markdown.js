/* Markdown block parser for research reports.
 *
 * Pure, DOM-free, JSX-free so it can be unit-tested on Node's built-in runner
 * (a .jsx import is rejected by plain Node). The renderer in AnswerCard.jsx
 * maps the block model to React elements; all the tricky text handling lives
 * here where it is testable.
 *
 * The goal is a report that renders cleanly whatever shape the writer emitted:
 * headings keep their hierarchy, GFM pipe tables become real tables (even when
 * ragged, missing a separator row, or padded with `<br>`), bullet and numbered
 * lists stay lists, code fences become code blocks, citations and links become
 * anchors, and stray HTML artifacts (`<br>`, `<br/>`) are removed rather than
 * printed literally.
 *
 * It never drops content: a block it cannot classify is emitted as a paragraph.
 */

const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const BULLET_RE = /^\s*([-*+])\s+(.*)$/;
const ORDERED_RE = /^\s*(\d{1,3})[.)]\s+(.*)$/;
const FENCE_RE = /^\s*(```|~~~)\s*([A-Za-z0-9_+-]*)\s*$/;
const BLOCKQUOTE_RE = /^\s*>\s?(.*)$/;
const HR_RE = /^\s*([-*_])(\s*\1){2,}\s*$/;
/* A GFM table separator cell: ---, :---, ---:, :---: (with optional spaces). */
const SEP_CELL_RE = /^\s*:?-{1,}:?\s*$/;
/* Inline ordered markers used to split a run-on list that reached us flattened
 * onto one line ("1. a 2. b 3. c"). Reports persisted before the writer
 * preserved line breaks still store this shape. */
const INLINE_ORDERED_RE = /(?:^|\s)(\d{1,2})[.)]\s+(?=\S)/g;

/* If `body` is a run-on numbered list, split it back into items. Conservative:
 * the markers must be consecutive (1,2,3) AND every recovered item must start
 * with a bold lead-in — the shape the "N) **Label** - note" block uses. Prose
 * that merely contains "phase 2) of the plan" is left alone. Returns null when
 * the shape does not match. */
export function splitInlineOrderedItems(body, startNum) {
  INLINE_ORDERED_RE.lastIndex = 0;
  const marks = [];
  let m;
  while ((m = INLINE_ORDERED_RE.exec(body)) !== null) marks.push(m);
  if (!marks.length) return null;
  const nums = marks.map((mk) => Number(mk[1]));
  if (!nums.every((n, i) => n === startNum + 1 + i)) return null;
  // The text before the first marker is item `startNum` itself; then each
  // marker begins the next item.
  const parts = [];
  const first = body.slice(0, marks[0].index).trim();
  if (first) parts.push(first);
  for (let i = 0; i < marks.length; i++) {
    const from = marks[i].index + marks[i][0].length;
    const to = i + 1 < marks.length ? marks[i + 1].index : body.length;
    const part = body.slice(from, to).trim();
    if (part) parts.push(part);
  }
  if (!parts.length || !parts.every((p) => p.startsWith("**"))) return null;
  return parts;
}

/* Remove literal HTML line-break artifacts the writer/LLM emitted, and stray
 * raw-html tags that would otherwise print as text. `<br>` variants become a
 * real space (they separated content) rather than vanishing and gluing words. */
export function stripHtmlArtifacts(text) {
  return String(text ?? "")
    .replace(/<br\s*\/?>/gi, " ")
    // Block-level tags separated content, so they become a space; inline tags
    // (bold/italic) are just removed without splitting the surrounding word.
    .replace(/<\/?(?:p|div|br|hr|tr|td|th|li|ul|ol|h[1-6])\s*\/?>/gi, " ")
    .replace(/<\/?(?:span|strong|em|b|i|u|code|a)\s*\/?>/gi, "")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/[ \t]+\n/g, "\n")
    .trim();
}

/* Split a table row on unescaped pipes, dropping the optional leading/trailing
 * pipe GFM allows. Cells keep their inner pipes if escaped (`\|`). */
export function splitTableRow(line) {
  let s = String(line ?? "").trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  const cells = [];
  let cur = "";
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (ch === "\\" && s[i + 1] === "|") {
      cur += "|";
      i++;
      continue;
    }
    if (ch === "|") {
      cells.push(cur.trim());
      cur = "";
      continue;
    }
    cur += ch;
  }
  cells.push(cur.trim());
  return cells;
}

function isSeparatorRow(line) {
  const cells = splitTableRow(line);
  if (cells.length < 2) return false;
  return cells.every((c) => SEP_CELL_RE.test(c));
}

/* Does the run of lines starting at `i` look like a table? At least two rows
 * where the first has a pipe, or a header + separator pair. */
function tableAt(lines, i) {
  const line = lines[i];
  if (!line || !line.includes("|")) return null;
  const header = splitTableRow(line);
  if (header.length < 2) return null;
  const rows = [];
  const aligns = header.map(() => "left");
  let j = i + 1;
  let sawSeparator = false;
  if (j < lines.length && isSeparatorRow(lines[j])) {
    sawSeparator = true;
    const seps = splitTableRow(lines[j]);
    seps.forEach((s, k) => {
      const left = s.startsWith(":");
      const right = s.endsWith(":");
      aligns[k] = left && right ? "center" : right ? "right" : left ? "left" : "left";
    });
    j++;
  }
  while (j < lines.length && lines[j].includes("|") && lines[j].trim()) {
    if (isSeparatorRow(lines[j])) {
      j++;
      continue;
    }
    rows.push(splitTableRow(lines[j]));
    j++;
  }
  // A single pipe-containing line with no separator row is not a table — it is
  // prose that happens to contain a pipe. Require a separator OR >=2 body rows.
  if (!sawSeparator && rows.length < 2) return null;
  return { header, rows, aligns, end: j };
}

/* Normalise a ragged table so every row has exactly `width` cells: short rows
 * are padded, long rows keep their overflow in the last cell (never dropped). */
function normalizeTable(header, rows, width) {
  const fit = (cells) => {
    const out = cells.slice(0, width);
    while (out.length < width) out.push("");
    if (cells.length > width) {
      out[width - 1] = `${out[width - 1]} ${cells.slice(width).join(" ")}`.trim();
    }
    return out;
  };
  return { header: fit(header), rows: rows.map(fit) };
}

/* Parse report Markdown into an ordered list of blocks:
 *   { type: "heading", level, text }
 *   { type: "paragraph", text }
 *   { type: "list", ordered, items: [string] }
 *   { type: "table", header: [string], rows: [[string]], aligns: [string] }
 *   { type: "code", lang, text }
 *   { type: "quote", text }
 *   { type: "hr" }
 */
export function parseBlocks(markdown) {
  const text = String(markdown ?? "").replace(/\r\n?/g, "\n");
  const lines = text.split("\n");
  const blocks = [];
  let para = [];
  let list = null;

  const flushPara = () => {
    if (!para.length) return;
    const joined = stripHtmlArtifacts(para.join("\n").trim());
    // Preserve intentional internal line breaks as spaces within one paragraph
    // (the reader still gets one flowing paragraph, not a stray <br>).
    const clean = joined.replace(/\n+/g, " ").replace(/\s{2,}/g, " ").trim();
    if (clean) blocks.push({ type: "paragraph", text: clean });
    para = [];
  };
  const flushList = () => {
    if (list && list.items.length) blocks.push(list);
    list = null;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Fenced code block: consume verbatim until the closing fence.
    const fence = line.match(FENCE_RE);
    if (fence) {
      flushPara();
      flushList();
      const marker = fence[1];
      const lang = fence[2] || "";
      const body = [];
      i++;
      while (i < lines.length && !new RegExp(`^\\s*${marker}\\s*$`).test(lines[i])) {
        body.push(lines[i]);
        i++;
      }
      blocks.push({ type: "code", lang, text: body.join("\n") });
      continue;
    }

    // Blank line ends any open paragraph/list.
    if (!line.trim()) {
      flushPara();
      flushList();
      continue;
    }

    // Table (checked before lists so a `|` row is never mistaken for text).
    const table = tableAt(lines, i);
    if (table) {
      flushPara();
      flushList();
      const width = table.header.length;
      const { header, rows } = normalizeTable(table.header, table.rows, width);
      blocks.push({ type: "table", header, rows, aligns: table.aligns });
      i = table.end - 1;
      continue;
    }

    const heading = line.match(HEADING_RE);
    if (heading) {
      flushPara();
      flushList();
      blocks.push({
        type: "heading",
        level: heading[1].length,
        text: stripHtmlArtifacts(heading[2].trim()),
      });
      continue;
    }

    if (HR_RE.test(line)) {
      flushPara();
      flushList();
      blocks.push({ type: "hr" });
      continue;
    }

    const quote = line.match(BLOCKQUOTE_RE);
    if (quote) {
      flushPara();
      flushList();
      const body = [quote[1]];
      while (i + 1 < lines.length && BLOCKQUOTE_RE.test(lines[i + 1])) {
        i++;
        body.push(lines[i].match(BLOCKQUOTE_RE)[1]);
      }
      blocks.push({ type: "quote", text: stripHtmlArtifacts(body.join(" ").trim()) });
      continue;
    }

    const bullet = line.match(BULLET_RE);
    const ordered = line.match(ORDERED_RE);
    if (bullet || ordered) {
      flushPara();
      const isOrdered = Boolean(ordered);
      if (list && list.ordered !== isOrdered) flushList();
      if (!list) list = { type: "list", ordered: isOrdered, items: [] };
      const body = stripHtmlArtifacts((ordered ? ordered[2] : bullet[2]).trim());
      if (ordered) {
        // A numbered list that reached us flattened onto one line.
        const split = splitInlineOrderedItems(body, Number(ordered[1]));
        if (split) {
          split.forEach((part) => list.items.push(part));
          continue;
        }
      }
      list.items.push(body);
      continue;
    }

    // Indented continuation of the current list item (wrapped output).
    if (list && /^\s+/.test(line)) {
      const last = list.items.length - 1;
      if (last >= 0) {
        list.items[last] = `${list.items[last]} ${stripHtmlArtifacts(line.trim())}`.trim();
        continue;
      }
    }

    // Plain text: open list closes; accumulate into the current paragraph.
    flushList();
    para.push(line);
  }

  flushPara();
  flushList();
  return blocks;
}
