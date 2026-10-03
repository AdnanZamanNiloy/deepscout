/* MARS landing — a working front door.
 *
 * Design direction: the landing does the same job as the console's welcome
 * screen, at full width. Instead of a stock AI-marketing composition (three
 * blurred orbs, eight twinkling stars, a giant letter-spaced wordmark), the
 * right column shows the product's ACTUAL shape: the 7-agent pipeline as a
 * numbered spine, and a real evidence ledger with verification outcomes.
 * That pipeline is the product; showing it beats describing it.
 *
 * Pure presentational — no data fetching, no fabricated run state. The
 * pipeline order and the claim ledger are the same facts the Agents view and
 * the intelligence rail render from code. */

import { Planet } from "./Sidebar";
import ThemeToggle from "./ThemeToggle";
import {
  IconAgents, IconChart, IconCheckCircle, IconCompass, IconDoc,
  IconFlask, IconGithub, IconLayers, IconRoute, IconSearch, IconShield,
  IconShieldCheck, IconSpark, IconTarget,
} from "./icons";

const GITHUB_URL = "https://github.com/AdnanZamanNiloy/mars-ai";

const PIPELINE = [
  { name: "Orchestrator", desc: "Scores complexity, sets agent count and iteration caps" },
  { name: "Planner", desc: "Decomposes the question into delegation contracts" },
  { name: "Search", desc: "Retrieves, ranks and fetches sources per sub-question" },
  { name: "Summarizer", desc: "Extracts standalone factual claims with domain overlays" },
  { name: "Verifier", desc: "Checks each claim against its own cited source" },
  { name: "Critic", desc: "Red-teams assumptions and forces expansion on gaps" },
  { name: "Synthesizer", desc: "Writes the cited report with a numbered source legend" },
];

const LEDGER = [
  { claim: "Claim checked against its cited source", tone: "good", label: "verified" },
  { claim: "Two sources disagree on the figure", tone: "warn", label: "contradiction" },
  { claim: "Claim has no retrievable source", tone: "bad", label: "unsupported" },
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
    desc: "When credible sources disagree, MARS surfaces the conflict and what it turns on — scope, timeframe, method — instead of silently averaging it into a false single figure.",
  },
  {
    icon: IconCompass,
    title: "Calibrated confidence",
    desc: "A deterministic confidence engine scores source quality, diversity, citation coverage and cross-source agreement — and caps the result when the run degraded, so a weak pool can never read as a confident one.",
  },
  {
    icon: IconTarget,
    title: "Adaptive depth",
    desc: "The depth controller decides when the evidence is sufficient and stops. Quick, standard, deep, executive, audit and red-team modes set research breadth and iteration budgets to the stakes.",
  },
  {
    icon: IconChart,
    title: "Auditable output",
    desc: "Every sentence that states a fact carries a citation marker tied to a numbered source legend. The audit layer carries confidence, conflicts, decisions and the evidence ledger — separate from the answer.",
  },
];

/* Research modes — mirrors the orchestrator's real mode presets. */
const MODES = [
  { name: "Quick", desc: "A fast, tight answer with a single research pass. For lookups and simple questions.", tone: "fast" },
  { name: "Standard", desc: "Balanced breadth and verification for everyday research questions.", tone: "std" },
  { name: "Deep", desc: "Wider decomposition, more iterations and stricter evidence requirements.", tone: "deep" },
  { name: "Executive", desc: "Decision-grade briefs: prioritised findings, trade-offs and a clear bottom line.", tone: "exec" },
  { name: "Audit", desc: "Full provenance: source ledger, evidence grades and every measured score.", tone: "audit" },
  { name: "Red team", desc: "Adversarial pass that attacks the conclusion and hunts missing counter-evidence.", tone: "red" },
];

/* Guarantees — the non-negotiable behaviours the pipeline enforces. */
const GUARANTEES = [
  { icon: IconCheckCircle, text: "Every factual sentence carries a citation tied to a source it can be traced to." },
  { icon: IconShield, text: "A single-source claim is flagged provisional and demoted; it never leads a field-level conclusion." },
  { icon: IconLayers, text: "Weak or degraded evidence is bounded in confidence — the report cannot overstate it." },
  { icon: IconSpark, text: "The direct answer leads; analysis, mechanisms and caveats support it." },
];

export default function Landing({ onStart, onDocs }) {
  return (
    <div className="landing">
      <header className="landing-nav">
        <a
          className="landing-brand"
          href="#/"
          onClick={(e) => e.preventDefault()}
          aria-label="MARS — Multi-Agent Research System"
        >
          <Planet size={36} />
          <span className="brand-copy">
            <span className="brand-name">MARS</span>
            <span className="brand-sub">Multi-Agent Research System</span>
          </span>
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
      </header>

      <main className="landing-main" id="landing-about">
        <div className="landing-copy">
          <span className="landing-eyebrow">
            <span className="dot live" aria-hidden="true" />
            7 coordinated agents
          </span>
          <h1 className="landing-title">
            Answers you can <em>audit</em>, not just answers you can read.
          </h1>
          <p className="landing-desc">
            MARS decomposes a hard question, dispatches it across a coordinated team of
            specialist agents, verifies every claim against the source that produced it, and
            red-teams its own conclusions before writing a cited report — live, to your console.
          </p>

          <button type="button" className="landing-cta" onClick={onStart}>
            Start research
          </button>
          <span className="landing-cta-note">
            No signup. Bring your own OpenAI-compatible key, or use the default chain.
          </span>

          <div className="landing-metrics">
            <div className="landing-metric">
              <div className="m-val">7</div>
              <div className="m-lab">pipeline agents</div>
            </div>
            <div className="landing-metric">
              <div className="m-val">6</div>
              <div className="m-lab">research modes</div>
            </div>
            <div className="landing-metric">
              <div className="m-val">1</div>
              <div className="m-lab">claim, 1 cited source</div>
            </div>
          </div>
        </div>

        {/* The evidence panel: the real pipeline, then what verification
            actually outputs. This is the product, shown. */}
        <div className="landing-panel" aria-label="How a MARS run is structured">
          <div className="landing-panel-head">
            <div>
              <div className="t">Run structure</div>
              <p className="q">Every question follows this order, and the console streams each step as it happens.</p>
            </div>
          </div>

          <ol className="spine" aria-label="Pipeline execution order">
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

          <div className="landing-ledger">
            <div className="eyebrow" style={{ marginBottom: 9 }}>What verification produces</div>
            {LEDGER.map((row) => (
              <div className="landing-ledger-row" key={row.label}>
                <span className={`dot ${row.tone === "good" ? "done" : row.tone === "warn" ? "warn" : "bad"}`} />
                <span>{row.claim}</span>
                <span className={`tag tone-${row.tone}`}>{row.label}</span>
              </div>
            ))}
          </div>
        </div>
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
              confident answer. MARS adds the machinery that makes an answer checkable.
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

      {/* How a run works + trust guarantees. */}
      <section className="landing-section landing-section-alt" id="landing-trust" aria-labelledby="landing-trust-head">
        <div className="landing-section-inner">
          <div className="landing-split">
            <div className="landing-split-copy">
              <span className="landing-section-eyebrow">Trust model</span>
              <h2 id="landing-trust-head" className="landing-section-title">
                Answers you can audit
              </h2>
              <p className="landing-section-desc">
                MARS separates the answer a reader wants from the provenance a reviewer
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
            <div className="landing-split-panel" aria-label="Evidence pipeline">
              <div className="landing-split-panel-head">
                <IconAgents size={16} aria-hidden="true" />
                <span>From question to cited report</span>
              </div>
              <ol className="landing-flow">
                <li><span className="landing-flow-n">1</span> Decompose the question into research dimensions</li>
                <li><span className="landing-flow-n">2</span> Retrieve and tier sources per dimension</li>
                <li><span className="landing-flow-n">3</span> Extract standalone claims from each source</li>
                <li><span className="landing-flow-n">4</span> Verify every claim against the source that produced it</li>
                <li><span className="landing-flow-n">5</span> Grade evidence and resolve contradictions</li>
                <li><span className="landing-flow-n">6</span> Synthesize a cited report and an audit trail</li>
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
              The same pipeline runs at six depths. Each sets its own research breadth,
              evidence requirements and iteration budget.
            </p>
          </header>
          <div className="landing-modes">
            {MODES.map((m) => (
              <article className={`landing-mode tone-${m.tone}`} key={m.name}>
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
            <span className="landing-footer-mark">
              <Planet size={28} />
              <span className="landing-footer-name">MARS</span>
            </span>
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
            <span className="landing-footer-meta">6 research modes</span>
            <span className="landing-footer-meta">Bring-your-own key or default chain</span>
          </nav>
        </div>

      </footer>
    </div>
  );
}
