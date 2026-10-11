/* DeepScout landing — a working front door.
 *
 * Design direction: the hero shows the product's ACTUAL shape rather than
 * describing it. The left column is the promise; the right column is a working
 * preview of the console — sidebar, question, streamed report, evidence
 * outcomes, follow-up box — built from the same components and vocabulary the
 * real UI uses, so the first screen and the product cannot drift apart.
 *
 * Everything below the hero (capabilities, trust model, modes) is real product
 * detail mapped to real pipeline modules, not filler.
 *
 * The console preview is ILLUSTRATIVE and says so: it carries role="img" with
 * one descriptive label, so a screen reader gets a sentence instead of a fake
 * UI, and no inner element is focusable or announced as a live control. Its
 * figures are sample values for shape only — no run state is fabricated or
 * fetched. */

import ThemeToggle from "./ThemeToggle";
import BrandLockup, { BrandMark } from "./Brand";
import {
  IconAgents, IconArrowUp, IconArrowRight, IconChart, IconCheckCircle,
  IconCompass, IconDoc, IconFlask, IconGithub, IconLayers, IconMissions,
  IconPlay, IconPlus, IconRoute, IconSearch, IconShield, IconShieldCheck,
  IconSpark, IconTarget,
} from "./icons";

const GITHUB_URL = "https://github.com/AdnanZamanNiloy/deepscout-ai";

/* The sample "Watch demo" runs. Exported so App can launch the exact question
 * the hero advertises — a demo that ran something else than the button
 * promised would be the kind of quiet mismatch this repo keeps fixing. */
export const DEMO_QUERY = "What will be the most in demand skill in 2027?";

const PIPELINE = [
  { name: "Orchestrator", desc: "Scores complexity, sets agent count and iteration caps" },
  { name: "Planner", desc: "Decomposes the question into delegation contracts" },
  { name: "Search", desc: "Retrieves, ranks and fetches sources per sub-question" },
  { name: "Summarizer", desc: "Extracts standalone factual claims with domain overlays" },
  { name: "Verifier", desc: "Checks each claim against its own cited source" },
  { name: "Critic", desc: "Challenges assumptions and forces expansion on gaps" },
  { name: "Synthesizer", desc: "Writes the cited report with a numbered source legend" },
];

/* Console preview content. Sample questions only — nothing here is read from a
 * run, and no statistic is presented as a measured result. Rows mirror the real
 * sidebar's shape: a title line that truncates, and the mode beneath it. */
const PREVIEW_RECENT = [
  { title: DEMO_QUERY, mode: "standard", tone: "live", active: true },
  { title: "What are the latest advances in AI agents", mode: "standard", tone: "done" },
  { title: "How does graph RAG differ from vector search", mode: "standard", tone: "done" },
];

/* Capabilities — what the system does that a single-prompt assistant does not.
 * Every line maps to a real module in the pipeline (planner, evidence grader,
 * confidence engine, contradiction resolver, depth controller). */
const CAPABILITIES = [
  {
    icon: IconRoute,
    title: "Question decomposition",
    desc: "Hard queries are split into delegation contracts with explicit axes and search types, so every dimension of the question is researched, not just the most quotable one.",
  },
  {
    icon: IconSearch,
    title: "Primary-first retrieval",
    desc: "Sources are tiered — official statistics and peer-reviewed work outrank media, blogs and aggregators — and the retriever aims queries at authoritative publishers before falling back to the open web.",
  },
  {
    icon: IconShieldCheck,
    title: "Claim-level verification",
    desc: "Each extracted claim is checked against the exact source that produced it. Unsupported numbers, garbled fragments and unattributable statements are demoted or dropped before they reach the report.",
  },
  {
    icon: IconLayers,
    title: "Evidence grading A–D",
    desc: "Claims are graded on primacy, independent corroboration and numeric grounding. Grades drive what leads the answer, what is hedged, and what is omitted.",
  },
  {
    icon: IconFlask,
    title: "Contradiction handling",
    desc: "When credible sources disagree, DeepScout surfaces the conflict and what it turns on — scope, timeframe, method — instead of silently averaging it into a false single figure.",
  },
  {
    icon: IconCompass,
    title: "Calibrated confidence",
    desc: "A deterministic confidence engine scores source quality, diversity, citation coverage and cross-source agreement — and caps the result when the run degraded, so a weak pool can never read as a confident one.",
  },
  {
    icon: IconTarget,
    title: "Adaptive depth",
    desc: "The depth controller decides when the evidence is sufficient and stops. Quick, standard and deep modes set research breadth and iteration budgets to the stakes.",
  },
  {
    icon: IconChart,
    title: "Auditable output",
    desc: "Every sentence that states a fact carries a citation marker tied to a numbered source legend. The audit layer carries confidence, conflicts, decisions and the evidence ledger — separate from the answer.",
  },
];

/* Research modes — the orchestrator's real MODE_PRESETS, three of them.
 * The "Executive" mode this list used to advertise was removed from the
 * backend (its decision/contested handling folded into `deep`), so listing it
 * here described a mode that cannot be selected. The agent and pass counts are
 * the preset values themselves, not marketing round numbers. */
const MODES = [
  { name: "Quick", desc: "A fast, tight answer with a single research pass. Two agents, no expansion loops — for lookups and simple questions." },
  { name: "Standard", desc: "Balanced breadth and verification for everyday research questions. Three agents, up to three passes." },
  { name: "Deep", desc: "Wider decomposition, deeper evidence requirements and a pass budget that scales with the research map." },
];

/* Guarantees — the non-negotiable behaviours the pipeline enforces. */
const GUARANTEES = [
  { icon: IconCheckCircle, text: "Every factual sentence carries a citation tied to a source it can be traced to." },
  { icon: IconShield, text: "A single-source claim is flagged provisional and demoted; it never leads a field-level conclusion." },
  { icon: IconLayers, text: "Weak or degraded evidence is bounded in confidence — the report cannot overstate it." },
  { icon: IconSpark, text: "The direct answer leads; analysis, mechanisms and caveats support it." },
];

/* A preview of the console, drawn from the real UI's parts: the sidebar's
 * nav and recent list, the thread's question bubble, and the answer's report
 * head. Spans only — no buttons or links — because role="img" promises the
 * whole block is one picture, and a focusable control inside it would be a
 * promise the page cannot keep. */
function ConsolePreview() {
  const today = new Date().toLocaleDateString("en-GB");
  return (
    <div
      className="lp-window"
      role="img"
      aria-label="A DeepScout research run in the console: the question at the top, a streamed cited report in the middle, and a follow-up box below."
    >
      <div className="lp-window-bar">
        <span className="lp-traffic" aria-hidden="true">
          <i className="lp-stop" /><i className="lp-min" /><i className="lp-max" />
        </span>
      </div>

      <div className="lp-window-body">
        <div className="lp-rail">
          <div className="lp-rail-brand">
            <BrandMark size={17} />
            <span>DeepScout</span>
          </div>
          <div className="lp-rail-new">
            <IconPlus size={11} /> New research
          </div>
          <div className="lp-rail-nav">
            <span className="lp-rail-item">
              <IconMissions size={12} /> Research<span className="lp-rail-count">30</span>
            </span>
            <span className="lp-rail-item"><IconCompass size={12} /> Model Controls</span>
            <span className="lp-rail-item"><IconAgents size={12} /> Agents</span>
          </div>
          {/* The real rail's section head carries a "+N more" affordance; without
              it the preview reads as a sidebar that has nothing to scroll to. */}
          <div className="lp-rail-head">
            <span>Recent</span>
            <span className="lp-rail-more">+24 more</span>
          </div>
          <div className="lp-recent">
            {PREVIEW_RECENT.map((item) => (
              <span className={`lp-recent-row${item.active ? " active" : ""}`} key={item.title}>
                <span className={`dot ${item.tone}`} />
                <span className="lp-recent-body">
                  <span className="lp-recent-name">{item.title}</span>
                  <span className="lp-recent-sub">{item.mode}</span>
                </span>
              </span>
            ))}
          </div>
        </div>

        <div className="lp-stage">
          {/* The real title bar: chat title on the left, collapse control on the
              right. The title is the question here, so the preview states what
              the run was about instead of showing a generic label. */}
          <div className="lp-stage-top">
            <span className="lp-stage-query">{DEMO_QUERY}</span>
            <span className="lp-collapse" aria-hidden="true">‹</span>
          </div>

          <div className="lp-stage-body">
            <div className="lp-bubble">{DEMO_QUERY}</div>
            {/* The run's own summary bar: what it spent, and how long it took. */}
            <div className="lp-run-meta">
              <span className="lp-run-steps">
                <span className="lp-chev" aria-hidden="true">⌄</span> Less steps
              </span>
              <span className="lp-run-count">Worked for 2m 26s</span>
            </div>
            <h3 className="lp-report-title">
              The Most Demanding Skill of 2027: Rewriting Learned Procedures When the
              Foundations Shift
            </h3>
            <div className="lp-report-date">Date: {today}</div>
            <h4 className="lp-report-sub">Introduction</h4>
            <p className="lp-report-text">
              When a procedure that has run reliably for years suddenly rests on assumptions
              that no longer hold, the operator&rsquo;s job is not to learn a new procedure but to
              recognize the old one is invalid, suppress the habit that automates it, and build a
              workable substitute…
            </p>
          </div>

          <div className="lp-composer">
            <span className="lp-composer-field">Ask a follow-up question…</span>
            <span className="lp-composer-row">
              <span className="lp-composer-mode">
                Standard <span className="lp-chev" aria-hidden="true">⌄</span>
              </span>
              <span className="lp-composer-send"><IconArrowUp size={13} /></span>
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Landing({ onStart, onDocs, onDemo }) {
  return (
    <div className="landing">
      <header className="landing-nav">
        <div className="landing-nav-inner">
          <a
            className="landing-brand"
            href="#/"
            onClick={(e) => e.preventDefault()}
            aria-label="DeepScout — Multi-Agent Research System"
          >
            <BrandLockup size="lg" />
          </a>
          <nav className="landing-links" aria-label="Primary">
            <a className="landing-link" href="#landing-about">
              <span className="landing-link-label">About</span>
            </a>
            <a className="landing-link" href="#landing-capabilities">
              <span className="landing-link-label">Capabilities</span>
            </a>
            <a className="landing-link" href="#landing-trust">
              <span className="landing-link-label">Trust</span>
            </a>
            <a className="landing-link" href="#landing-modes">
              <span className="landing-link-label">Modes</span>
            </a>
            <a className="landing-link" href="#/docs" onClick={(e) => { e.preventDefault(); onDocs?.(); }}>
              <IconDoc size={15} />
              <span className="landing-link-label">Docs</span>
            </a>
            <a className="landing-link" href={GITHUB_URL} target="_blank" rel="noreferrer" aria-label="GitHub repository">
              <IconGithub size={15} />
              <span className="landing-link-label">GitHub</span>
            </a>
            <ThemeToggle />
          </nav>
        </div>
      </header>

      <main className="landing-main" id="landing-about">
        <div className="landing-copy">
          <h1 className="landing-title">
            Turn complex questions into{" "}
            <span className="landing-title-accent">verified answers.</span>
          </h1>
          <p className="landing-desc">
            DeepScout uses a team of specialized agents to research, analyze, and verify
            claims — so you get clear, well-sourced answers you can trust.
          </p>

          <div className="landing-actions">
            <button type="button" className="landing-cta" onClick={onStart}>
              <IconSpark size={15} /> Start research <IconArrowRight size={16} />
            </button>
            <button type="button" className="landing-cta-ghost" onClick={() => onDemo?.(DEMO_QUERY)}>
              <IconPlay size={16} /> Watch demo
            </button>
          </div>
          <span className="landing-cta-note">
            No signup. Bring your own OpenAI-compatible key, or use the default chain.
          </span>
        </div>

        <ConsolePreview />
      </main>

      {/* Capabilities — the product, section by section. */}
      <section className="landing-section" id="landing-capabilities" aria-labelledby="landing-cap-head">
        <div className="landing-section-inner">
          <header className="landing-section-head">
            <span className="landing-section-eyebrow">Capabilities</span>
            <h2 id="landing-cap-head" className="landing-section-title">
              A research pipeline, not a single prompt
            </h2>
            <p className="landing-section-desc">
              Each stage exists because a language model on its own will hallucinate a
              confident answer. DeepScout adds the machinery that makes an answer checkable.
            </p>
          </header>
          <div className="landing-grid">
            {CAPABILITIES.map((cap) => {
              const Icon = cap.icon;
              return (
                <article className="landing-card" key={cap.title}>
                  <span className="landing-card-icon" aria-hidden="true"><Icon size={18} /></span>
                  <h3 className="landing-card-title">{cap.title}</h3>
                  <p className="landing-card-desc">{cap.desc}</p>
                </article>
              );
            })}
          </div>
        </div>
      </section>

      {/* The team, and the trust model. */}
      <section className="landing-section landing-section-alt" id="landing-trust" aria-labelledby="landing-trust-head">
        <div className="landing-section-inner">
          <div className="landing-split">
            <div className="landing-split-copy">
              <span className="landing-section-eyebrow">Trust model</span>
              <h2 id="landing-trust-head" className="landing-section-title">
                Answers you can audit
              </h2>
              <p className="landing-section-desc">
                DeepScout separates the answer a reader wants from the provenance a reviewer
                needs. The report states the conclusion and its citations; the audit layer
                carries every measured score, conflict and evidence gap behind it.
              </p>
              <ul className="landing-guarantees">
                {GUARANTEES.map((g) => {
                  const Icon = g.icon;
                  return (
                    <li className="landing-guarantee" key={g.text}>
                      <span className="landing-guarantee-icon" aria-hidden="true"><Icon size={16} /></span>
                      <span>{g.text}</span>
                    </li>
                  );
                })}
              </ul>
            </div>
            <div className="landing-split-panel" aria-label="The seven-agent pipeline, in execution order">
              <div className="landing-split-panel-head">
                <IconAgents size={16} aria-hidden="true" />
                <span>The team behind every answer</span>
              </div>
              <ol className="spine">
                {PIPELINE.map((node, i) => (
                  <li key={node.name} className="spine-node">
                    <span className="spine-idx">{i + 1}</span>
                    <span className="spine-body">
                      <span className="spine-title">{node.name}</span>
                      <span className="spine-desc">{node.desc}</span>
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          </div>
        </div>
      </section>

      {/* Research modes. */}
      <section className="landing-section" id="landing-modes" aria-labelledby="landing-modes-head">
        <div className="landing-section-inner">
          <header className="landing-section-head">
            <span className="landing-section-eyebrow">Research modes</span>
            <h2 id="landing-modes-head" className="landing-section-title">
              Depth matched to the stakes
            </h2>
            <p className="landing-section-desc">
              The same pipeline runs at three depths. Each sets its own research breadth,
              evidence requirements and iteration budget.
            </p>
          </header>
          <div className="landing-modes">
            {MODES.map((m) => (
              <article className="landing-mode" key={m.name}>
                <h3 className="landing-mode-name">{m.name}</h3>
                <p className="landing-mode-desc">{m.desc}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* Closing call to action. */}
      <section className="landing-cta-band" aria-label="Start research">
        <div className="landing-section-inner landing-cta-band-inner">
          <div>
            <h2 className="landing-cta-band-title">Ask a hard question.</h2>
            <p className="landing-cta-band-desc">
              Watch the pipeline run live, then read an answer with the evidence attached.
            </p>
          </div>
          <div className="landing-cta-band-actions">
            <button type="button" className="landing-cta" onClick={onStart}>
              <IconSpark size={15} /> Start research
            </button>
            <button
              type="button"
              className="landing-cta-secondary"
              onClick={() => onDocs?.()}
            >
              <IconDoc size={15} /> Read the docs
            </button>
          </div>
        </div>
      </section>

      <footer className="landing-footer">
        <div className="landing-footer-inner">
          <div className="landing-footer-brand">
            <BrandLockup size="sm" className="landing-footer-mark" />
            <p className="landing-footer-tag">
              Multi-Agent Research System — cited, verified, auditable answers from a
              coordinated team of specialist agents.
            </p>
          </div>

          <nav className="landing-footer-col" aria-label="Product">
            <h4>Sections</h4>
            <a href="#landing-about">About</a>
            <a href="#landing-capabilities">Capabilities</a>
            <a href="#landing-trust">Trust model</a>
            <a href="#landing-modes">Research modes</a>
          </nav>

          <nav className="landing-footer-col" aria-label="Resources">
            <h4>Resources</h4>
            <a href="#/docs" onClick={(e) => { e.preventDefault(); onDocs?.(); }}>Documentation</a>
            <a href={GITHUB_URL} target="_blank" rel="noreferrer">Source code</a>
            <a href={`${GITHUB_URL}/issues`} target="_blank" rel="noreferrer">Report an issue</a>
          </nav>

          <nav className="landing-footer-col" aria-label="System">
            <h4>System</h4>
            <button type="button" className="landing-footer-linkbtn" onClick={onStart}>
              Start research
            </button>
            <span className="landing-footer-meta">7-agent pipeline</span>
            <span className="landing-footer-meta">3 research modes</span>
            <span className="landing-footer-meta">Bring-your-own key or default chain</span>
          </nav>
        </div>

      </footer>
    </div>
  );
}