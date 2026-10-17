/* DeepScout landing — a working front door.
 *
 * Design direction: the hero shows the product's ACTUAL shape rather than
 * describing it. The left column is the promise; the right column is a working
 * preview of the console — sidebar, question, the collapsed pipeline trace, the
 * report head and the follow-up composer — drawn from the same components and
 * vocabulary the real UI uses, so the first screen and the product cannot drift
 * apart.
 *
 * The preview is kept honest by CONSTRUCTION rather than by discipline: every
 * row here mirrors a real element in Sidebar.jsx / App.jsx / Composer.jsx /
 * trace/AgentPipelineTrace.tsx, and it was last reconciled against them after
 * the rail was flattened. When the console changes shape, change it here in the
 * same commit — a mockup of a UI that no longer exists is worse than none,
 * because it is the first thing a visitor trusts.
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
  IconChevronDown, IconCompass, IconDoc, IconFlask, IconGithub, IconLayers,
  IconMissions, IconPanel, IconPlay, IconPlus, IconRoute, IconSearch,
  IconShield, IconShieldCheck, IconSpark, IconTarget,
} from "./icons";

const GITHUB_URL = "https://github.com/AdnanZamanNiloy/deepscout";

/* The sample "Watch demo" runs. Exported so App can launch the exact question
 * the hero advertises — a demo that ran something else than the button
 * promised would be the kind of quiet mismatch this repo keeps fixing. */
export const DEMO_QUERY = "What will be the most in demand skill in 2027?";

const PIPELINE = [
  { name: "Pathfinder", desc: "Discovers sources and maps the research landscape" },
  { name: "Research Architect", desc: "Designs the strategy and the report outline" },
  { name: "Review Gate", desc: "Approves the plan before investigation begins" },
  { name: "Scout Swarm", desc: "Investigates every section in parallel" },
  { name: "Narrative Writer", desc: "Crafts the introduction and conclusion" },
  { name: "Evidence Auditor", desc: "Checks claims, support and inconsistencies" },
  { name: "Diagram Designer", desc: "Creates a Mermaid diagram for the research" },
  { name: "Report Assembler", desc: "Combines the content into the final report" },
];

/* Console preview content. Sample questions only — nothing here is read from a
 * run, and no statistic is presented as a measured result. Rows mirror the real
 * sidebar's current shape: a single truncated line per session, with the status
 * dot. The real rail carries no section head and no "+N more" — the list simply
 * scrolls — and the mode lives on the session rather than under every title, so
 * neither is drawn here. */
const PREVIEW_RECENT = [
  { title: DEMO_QUERY, tone: "live", active: true },
  { title: "What are the latest advances in AI agents", tone: "done" },
  { title: "How does graph RAG differ from vector search", tone: "done" },
];

/* Capabilities — what the system does that a single-prompt assistant does not.
 * Every line maps to a real module in the pipeline: the planner
 * (app/agents/architect.py), the evidence grader and claim hygiene
 * (app/evidence/), the confidence engine and contradiction resolver
 * (app/core/), and the source tiering (app/evidence/sources/). */
const CAPABILITIES = [
  {
    icon: IconRoute,
    title: "Question decomposition",
    desc: "The question is researched first, then split into an outline whose sections are planned from what was actually found — so every part of the question is investigated, not just the most quotable one.",
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
    title: "Evidence grading",
    desc: "Claims are graded on source primacy, independent corroboration and numeric grounding. Grades drive what leads the answer, what is hedged, and what is omitted.",
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
    title: "Bounded effort",
    desc: "Every run is bounded before it starts: the mode fixes how many report sections are planned, and the run has an enforced wall-clock and model-call ceiling. When a ceiling is reached it stops expanding, sheds optional work in a fixed order, and says so — rather than running without limit.",
  },
  {
    icon: IconChart,
    title: "Auditable output",
    desc: "Every sentence that states a fact carries a citation marker tied to a numbered source legend. The audit layer carries confidence, conflicts, decisions and the evidence ledger — separate from the answer.",
  },
];

/* Research modes — the section counts the runner actually uses
 * (`_MODE_SECTIONS` in app/research/runner.py), the revision ceilings that go
 * with them (`_build_task`), and the enforced wall-clock cap per mode
 * (`MODE_HARD_CAP_SECONDS` in app/infra/budget.py). The agent count is NOT
 * per-mode (MAX_PARALLEL_AGENTS is a fixed hardware cap), so it is not listed.
 * The "Executive" mode this list used to advertise was removed from the
 * backend long ago, so listing it described a mode that cannot be selected. */
/* `desc` quotes the EXPECTED completion time a reader plans around. The
   enforced ceiling behind it is 180 / 360 / 720s (MODE_HARD_CAP_SECONDS) and
   exists to stop a stalled provider — quoting it as the budget made every mode
   look 2-3x slower than it is. Section counts come from _MODE_SECTIONS. */
const MODES = [
  { name: "Quick", desc: "3 sections, one pass with no fact-check round, ~2 min. Stops at 3 min or 21 model calls. For lookups and narrow factual questions where breadth is not the point." },
  { name: "Standard", desc: "5 sections and up to 2 revision loops, ~3.5 min. Stops at 6 min or 60 model calls. Balanced breadth and verification for everyday research questions." },
  { name: "Deep", desc: "8 sections with the same revision ceilings but a wider decomposition (~5 min). Stops at 12 min or 150 model calls, so more of the question gets its own investigation." },
];

/* Guarantees — the non-negotiable behaviours the pipeline enforces. */
const GUARANTEES = [
  { icon: IconCheckCircle, text: "Every factual sentence carries a citation tied to a source it can be traced to." },
  { icon: IconShield, text: "A single-source claim is flagged provisional and demoted; it never leads a field-level conclusion." },
  { icon: IconLayers, text: "Weak or degraded evidence is bounded in confidence — the report cannot overstate it." },
  { icon: IconSpark, text: "The direct answer leads; analysis, mechanisms and caveats support it." },
];

/* The report's date line, in the exact shape the backend stamps:
 * `app/agents/architect.py` builds it with
 * `datetime.now().astimezone().strftime("%d %B %Y, %H:%M %Z")` and
 * `app/agents/report.py` emits it as italic markdown. Reproduced here so the
 * preview cannot show a date format the product does not produce. */
function reportStamp(d = new Date()) {
  const day = new Intl.DateTimeFormat("en-GB", {
    day: "2-digit", month: "long", year: "numeric",
  }).format(d);
  const time = new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(d);
  const offset = -d.getTimezoneOffset();
  const zone = `${offset >= 0 ? "+" : "-"}${String(Math.floor(Math.abs(offset) / 60)).padStart(2, "0")}`;
  return `${day}, ${time} ${zone}`;
}

/* A preview of the console, drawn from the real UI's parts: the sidebar's
 * nav and session list, the thread's question bubble, the answer's report
 * head. Spans only — no buttons or links — because role="img" promises the
 * whole block is one picture, and a focusable control inside it would be a
 * promise the page cannot keep. */
function ConsolePreview() {
  return (
    <div
      className="lp-window"
      role="img"
      aria-label="A DeepScout research run in the console: the session listed and selected in the sidebar, the question and its collapsed pipeline trace above the report, the report head below, and a follow-up box at the bottom."
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
            <span className="lp-rail-item"><IconMissions size={12} /> Research</span>
            <span className="lp-rail-item"><IconCompass size={12} /> Model Controls</span>
            <span className="lp-rail-item"><IconAgents size={12} /> Agents</span>
          </div>
          <div className="lp-recent">
            {PREVIEW_RECENT.map((item) => (
              <span className={`lp-recent-row${item.active ? " active" : ""}`} key={item.title}>
                <span className={`dot ${item.tone}`} />
                <span className="lp-recent-name">{item.title}</span>
              </span>
            ))}
          </div>
        </div>

        <div className="lp-stage">
          {/* The real title bar: the session title is itself the control, with a
              chevron for its menu (rename / pin / delete), and the run-insights
              panel collapses to a lone button pinned top-right. The hamburger is
              desktop-hidden, so it is not drawn. */}
          <div className="lp-stage-top">
            <span className="lp-stage-title">
              <span className="lp-stage-query">{DEMO_QUERY}</span>
              <IconChevronDown size={12} className="lp-stage-chev" />
            </span>
            <span className="lp-collapse"><IconPanel size={13} /></span>
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
            {/* The date is the report's own markdown line — `*17 October 2026,
                18:25 +06*` — stamped by the run itself, not a "Date:" label and
                not a locale-formatted day/month/year. */}
            <div className="lp-report-date">{reportStamp()}</div>
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
            <div className="landing-split-panel" aria-label="The eight-stage pipeline, in execution order">
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
              its own revision ceilings, and an enforced wall-clock and model-call
              ceiling — shown as the time you can expect, with the hard stop set
              well above it.
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
            <span className="landing-footer-meta">8-stage pipeline</span>
            <span className="landing-footer-meta">3 research modes</span>
            <span className="landing-footer-meta">Bring-your-own key or default chain</span>
          </nav>
        </div>

      </footer>
    </div>
  );
}