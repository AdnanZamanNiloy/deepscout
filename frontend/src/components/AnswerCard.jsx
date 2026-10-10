import { confidenceLabel, parseReport } from "../lib";
import { parseBlocks } from "../markdown";
import {
  IconAlert, IconChart, IconCheckCircle, IconDoc,
} from "./icons";
import ExportMenu from "./ExportMenu";
/* Final report card — renders ONLY backend-produced content:
 * report markdown sections, findings events. */

/* Inline markdown renderer: the report body is markdown, so `**bold**`,
 * `*italic*`, `` `code` ``, and `[text](url)` must render as elements
 * instead of printing their markers literally. Returns an array of nodes so
 * it can nest inside <p>/<li>/<td> without wrapping in a block element.
 * Citation markers like [1] / [12] become small chips so a reader can see the
 * grounding instead of reading raw brackets. */
function renderInline(text) {
  // Any stray HTML break left in a single line is a space, not literal text.
  const src = String(text ?? "").replace(/<br\s*\/?>/gi, " ");
  if (!src) return src;
  const nodes = [];
  // Order matters: links, then bold, then italic, then code, then citations.
  const re = /(\[([^\]]+)\]\((https?:\/\/[^\s)]+)\))|(\*\*([^*]+)\*\*)|(\*([^*\n]+)\*)|(`([^`]+)`)|(\[(\d{1,3})\])/g;
  let last = 0;
  let m;
  let k = 0;
  while ((m = re.exec(src)) !== null) {
    if (m.index > last) nodes.push(src.slice(last, m.index));
    if (m[2] && m[3]) {
      nodes.push(
        <a key={k++} href={m[3]} target="_blank" rel="noreferrer noopener">{m[2]}</a>
      );
    } else if (m[5] !== undefined) {
      nodes.push(<strong key={k++}>{m[5]}</strong>);
    } else if (m[7] !== undefined) {
      nodes.push(<em key={k++}>{m[7]}</em>);
    } else if (m[9] !== undefined) {
      nodes.push(<code key={k++}>{m[9]}</code>);
    } else if (m[11] !== undefined) {
      nodes.push(<sup key={k++} className="cite-ref">{m[11]}</sup>);
    }
    last = re.lastIndex;
  }
  if (last < src.length) nodes.push(src.slice(last));
  return nodes;
}

/* Render a parsed table into a scrollable, aligned <table>. Cell content still
 * goes through renderInline so bold/links/citations inside a table cell work. */
function renderTable(block, key) {
  const { header, rows, aligns } = block;
  return (
    <div className="answer-table-wrap" key={`table-${key}`}>
      <table className="answer-table">
        <thead>
          <tr>
            {header.map((cell, j) => (
              <th key={j} style={{ textAlign: aligns[j] || "left" }}>{renderInline(cell)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri}>
              {row.map((cell, ci) => (
                <td key={ci} style={{ textAlign: aligns[ci] || "left" }}>{renderInline(cell)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* Rich renderer for the report body. All parsing/cleaning lives in the pure
 * markdown module (testable on plain Node); this only maps the block model to
 * React elements. Backend owns the words — this maps markers to elements and
 * never drops content (an unclassified block is emitted as a paragraph). */
function renderRichText(body) {
  const blocks = parseBlocks(body);
  const out = [];
  let key = 0;
  for (const block of blocks) {
    switch (block.type) {
      case "heading": {
        // Emit a semantically correct heading element per source level so the
        // document outline (H1 -> H2 -> H3) matches the writer's hierarchy.
        // Styling is driven by the level class, so every level is visually
        // distinct and consistent (Claude/ChatGPT-style, not ad-hoc sizes).
        const level = Math.min(Math.max(block.level, 1), 6);
        const Tag = `h${level}`;
        out.push(
          <Tag key={`h-${key++}`} className={`answer-heading md-h${level}`}>
            {renderInline(block.text)}
          </Tag>
        );
        break;
      }
      case "list": {
        const Tag = block.ordered ? "ol" : "ul";
        out.push(
          <Tag key={`list-${key++}`} className="answer-list">
            {block.items.map((item, j) => <li key={j}>{renderInline(item)}</li>)}
          </Tag>
        );
        break;
      }
      case "table":
        out.push(renderTable(block, key++));
        break;
      case "code":
        out.push(
          <pre key={`code-${key++}`} className="answer-code">
            <code>{block.text}</code>
          </pre>
        );
        break;
      case "quote":
        out.push(
          <blockquote key={`quote-${key++}`} className="answer-quote">
            {renderInline(block.text)}
          </blockquote>
        );
        break;
      case "hr":
        out.push(<hr key={`hr-${key++}`} className="answer-hr" />);
        break;
      case "paragraph":
      default:
        // Source legend lines ("[1] title — url") get their own tight style.
        if (/^\[\d+\]\s/.test(block.text)) {
          out.push(<p key={`src-${key++}`} className="source-line">{renderInline(block.text)}</p>);
        } else {
          out.push(<p key={`p-${key++}`} className="answer-para">{renderInline(block.text)}</p>);
        }
        break;
    }
  }
  return out;
}

/* Describe a degraded run honestly from the agents that actually fell back.
 * The old banner hardcoded "LLM providers were unavailable ... extractive,
 * uncited ... confidence is capped" for EVERY degraded agent, which is wrong:
 * an agent can fall back for a bad prompt, a size rejection or an invalid
 * model response, not only a provider outage; the synthesizer's fallback
 * DOES cite its claims; and only summarizer/synthesizer fallbacks cap
 * confidence. Names below mirror the backend's EXTRACTIVE_FALLBACK_AGENTS. */
const EXTRACTIVE_AGENTS = new Set(["summarizer", "synthesizer"]);
const AGENT_LABELS = {
  intent: "intent classification",
  planner: "planning",
  summarizer: "evidence extraction",
  verifier: "verification",
  critic: "critique",
  synthesizer: "synthesis",
};
// Backend reason codes (app/core/degradation.py + agent-local codes): WHY a
// stage degraded. Provider causes and evidence causes must read differently —
// a provider outage is not thin evidence.
const REASON_LABELS = {
  "provider-transient": "the LLM provider was temporarily unavailable (rate limit, timeout or outage)",
  "provider-hard": "the LLM provider rejected the request permanently (auth, quota or size)",
  provider_timeout: "the LLM provider timed out",
  providers_unavailable: "no LLM provider could serve the request",
  llm_error: "the LLM call failed",
  "weak-evidence": "the extracted evidence was too thin or unusable",
  no_facts_parsed: "no usable claims could be parsed from the sources",
  llm_returned_no_facts: "the model returned no usable claims",
  llm_returned_nothing: "the model returned nothing usable for the prompt size (not a provider outage)",
  payload_too_large: "the source payload was too large for the provider",
};

function reasonText(reason) {
  return REASON_LABELS[reason] || (reason ? String(reason).replace(/_/g, " ") : "");
}

function describeDegradation(degraded, reasons = {}, providerDegraded = false) {
  const labels = degraded.map((a) => AGENT_LABELS[a] || a);
  const extractive = degraded.filter((a) => EXTRACTIVE_AGENTS.has(a));
  const parts = [];
  // Name the concrete causes first, deduped — this is what distinguishes a
  // provider-transient run from a genuinely weak-evidence run.
  const causes = [];
  for (const agent of degraded) {
    const text = reasonText(reasons[agent]);
    if (text && !causes.includes(text)) causes.push(text);
  }
  if (providerDegraded && !causes.some((c) => /provider/.test(c))) {
    causes.push("an LLM provider failed during the run");
  }
  if (extractive.length) {
    parts.push(
      `${labels.join(", ")} ran on deterministic extraction instead of a model-generated response`
    );
    parts.push(
      "claims are unrewritten source text, so the answer is less polished and confidence is capped"
    );
  } else {
    parts.push(
      `the model response for ${labels.join(", ")} was unavailable or unusable, so a rule-based fallback was used`
    );
    parts.push("treat those sections as lower-assurance");
  }
  if (causes.length) parts.push(`cause: ${causes.join("; ")}`);
  return parts.join(" — ") + ".";
}

export default function AnswerCard({ run }) {
  const findings = Array.isArray(run.findings) ? run.findings : [];
  const sections = parseReport(run.report || "");
  const total = findings.length;
  const verified = findings.filter((f) => f && f.verified === true).length;
  const conflicts = sections.contradictions ? sections.contradictions.split(/\n+/).filter((l) => l.trim()).length : 0;
  const degraded = Array.isArray(run.degraded) ? run.degraded.filter(Boolean) : [];
  const degradedNotice = degraded.length
    ? describeDegradation(degraded, run.degradedReasons || {}, run.providerDegraded === true)
    : "";
  const support = typeof run.answerSupport === "number" ? Math.round(run.answerSupport * 100) : null;
  const outlineSections = run.outline && Array.isArray(run.outline.sections) ? run.outline.sections : [];

  return (
    <div className="answer anim-rise">
      {(degraded.length || run.providerDegraded === true) ? (
        <div className="degraded-banner" role="alert">
          <IconAlert size={16} />
          <div>
            <strong>Degraded run — treat with caution.</strong>{" "}
            {degradedNotice ||
              "an LLM provider failed during the run, so some output came from deterministic fallbacks."}
          </div>
        </div>
      ) : null}

      {run.directAnswer ? (
        <div className="answer-lead">{renderRichText(run.directAnswer.answer || "")}</div>
      ) : sections.finalAnswer ? (
        <div className="answer-lead">{renderRichText(sections.finalAnswer)}</div>
      ) : (
        <p className="answer-lead">No answer was produced for this run.</p>
      )}

      <div className="answer-actions">
        <ExportMenu runId={run.runId} />
      </div>

      {outlineSections.length ? (
        <div className="outline-block">
          <h3 style={{ fontSize: 15.5, fontWeight: 650, margin: "0 0 8px" }}>
            Report outline{run.sectionWise ? " (section-wise synthesis)" : ""}
          </h3>
          <ol className="outline-list">
            {outlineSections.map((s) => (
              <li key={`${s.axis}-${s.title}`}>
                <b>{s.title}</b>
                {s.coverage_goal ? <span className="sub2"> — {s.coverage_goal}</span> : null}
              </li>
            ))}
          </ol>
        </div>
      ) : null}

      {sections.contradictions ? (
        <div className="contradiction">
          <strong>Contradictions detected</strong>
          {"\n"}{sections.contradictions}
        </div>
      ) : null}

      {run.audit ? (
        <details className="audit-block">
          <summary>Research audit &amp; trace</summary>
          <div className="audit-body">{renderRichText(run.audit)}</div>
        </details>
      ) : null}
    </div>
  );
}

export function ReplayAnswerCard({ trace }) {
  /* Read-only replay built from the persisted trace — same card, zero new fetches. */
  const report = trace.final_report?.report_markdown || "";
  const run = {
    runId: trace.run_id || null,
    report,
    audit: trace.final_report?.audit_markdown || "",
    confidence: typeof trace.final_report?.confidence === "number" ? trace.final_report.confidence : null,
    findings: (trace.claims || []).map((c) => ({
      claim: c.claim,
      source: c.source_url,
      verified: c.verified === 1 || c.verified === true,
      confidence: c.confidence,
      agent: c.agent || "",
      challenged: c.challenged === 1 || c.challenged === true,
    })),
    snippets: (trace.sources || []).length,
  };
  // parseReport treats the whole document as the answer when there is no legacy
  // "# Final Answer" wrapper, so both adaptive and legacy reports render.
  return <AnswerCard run={run} />;
}
