import {
  IconAlert, IconChart, IconCheckCircle, IconChevronRight,
  IconDoc, IconRefresh, IconSearch, IconShield, IconShieldCheck, IconTarget,
} from "./icons";

/* Mission Intelligence — the run's instrument rail.
 *
 * PRINCIPLE: every number here is a value the stream actually delivered.
 * There are no invented progress percentages and no per-agent "estimated"
 * bars. An agent is done, working, or queued — decided by which artifacts
 * exist — and that is all we claim to know. When a section has no data we
 * hide it; we never pad the rail with placeholders that fill the column but
 * carry no signal.
 *
 * Props: run (live run object, replay-shaped run, or null),
 *        collapsed, onCollapse, onResume.
 */

const STATE_LABEL = { done: "Complete", active: "Working", waiting: "Queued" };

/* Canonical pipeline — the real eight stages from the shipped research graph
 * (`app/research/agents/orchestrator.py`), in execution order. Same roster as
 * AgentsView, so "stage 3" means the same thing everywhere.
 *
 *   browser → planner → human → researcher → writer → fact_checker →
 *   visualizer → publisher
 *
 * (`node` is the graph node name the wire reports, used to match live stages;
 * `desc` is filled from real counts in deriveAgents.) */
const PIPELINE = [
  { key: "browser", node: "browser", icon: IconSearch, name: "Pathfinder" },
  { key: "planner", node: "planner", icon: IconTarget, name: "Research Architect" },
  { key: "human", node: "human", icon: IconShieldCheck, name: "Review Gate" },
  { key: "researcher", node: "researcher", icon: IconSearch, name: "Scout Swarm" },
  { key: "writer", node: "writer", icon: IconDoc, name: "Narrative Writer" },
  { key: "fact_checker", node: "fact_checker", icon: IconShieldCheck, name: "Evidence Auditor" },
  { key: "visualizer", node: "visualizer", icon: IconChart, name: "Diagram Designer" },
  { key: "publisher", node: "publisher", icon: IconChart, name: "Report Assembler" },
];

function AgentRow({ icon: Ic, name, desc, status, index }) {
  const label = STATE_LABEL[status] || "Queued";
  return (
    <li className={`spine-node s-${status}`}>
      <span className="spine-idx">{status === "done" ? "\u2713" : (index ?? "")}</span>
      <span className="spine-body">
        <span className="spine-title">
          <Ic size={13} className="spine-ic" />
          {name}
          <span className="spine-state">{label}</span>
        </span>
        <span className="spine-desc">{desc}</span>
      </span>
    </li>
  );
}

export default function IntelligencePanel({
  run, collapsed, onCollapse, onResume,
}) {
  if (collapsed) return null;

  const agents = run ? deriveAgents(run) : [];
  const health = run ? deriveHealth(run) : [];
  const working = agents.filter((a) => a.status === "active").length;

  return (
    <aside className="intel" aria-label="Run insights">
      <div className="intel-head">
        <div>
          <h2>Run Insights</h2>
          <p aria-live="polite" aria-atomic="true">
            {run ? (
              run.replay ? (
                <><strong>Session replay</strong> · figures from the stored trace</>
              ) : run.done ? (
                <><strong>Run complete</strong> · figures from the finished trace</>
              ) : (
                <><strong>{working} agent{working === 1 ? "" : "s"} working</strong> · streaming live</>
              )
            ) : (
              "No active run"
            )}
          </p>
        </div>
        <button className="icon-btn" onClick={onCollapse} title="Collapse panel" aria-label="Collapse the intelligence panel">
          <IconChevronRight size={15} />
        </button>
      </div>

      {!run ? (
        <IdleState />
      ) : (
        <>
          <section className="intel-section">
            <h3>Research Workflow</h3>
            <ol className="spine">
              {agents.map((a, i) => <AgentRow key={a.name} index={i + 1} {...a} />)}
            </ol>
          </section>

          <section className="intel-section">
            <h3>Research health</h3>
            {health.map((r) => (
              <div className="health-row" key={r.label}>
                <r.icon size={15} className={`tone-${r.tone}`} />
                <span className="k">{r.label}</span>
                <span className="v" style={r.hot ? { color: "var(--deepscout-soft)" } : undefined}>{r.value}</span>
              </div>
            ))}
            <CitationHealthRow health={run.citationHealth} />
          </section>

          <ConfidenceBreakdown breakdown={run.breakdown} />

          {run.error && run.resumable ? (
            <section className="intel-section">
              <button className="btn" onClick={onResume} disabled={run.resuming} style={{ width: "100%" }}>
                <IconRefresh size={14} /> {run.resuming ? "Resuming…" : "Resume from checkpoint"}
              </button>
            </section>
          ) : null}
        </>
      )}
    </aside>
  );
}

/* The idle rail is orientation, not a column of "nothing yet". It explains
 * what this panel becomes once a run starts, in one compact block. */
function IdleState() {
  return (
    <section className="intel-section intel-idle">
      <p className="intel-lede">
        Ask a question and this rail becomes the run's instrument panel:
      </p>
      <ul className="intel-idle-list">
        <li><IconTarget size={14} /> The eight-stage pipeline as it advances</li>
        <li><IconChart size={14} /> A confidence breakdown from measured signals</li>
        <li><IconShieldCheck size={14} /> Claim verification and citation health</li>
        <li><IconAlert size={14} /> Adversarial review and dispute tracking</li>
      </ul>
    </section>
  );
}

/* ---------- derivation: stream state → panel widgets ---------- */

const SIGNAL_LABELS = {
  source_quality: "Source quality",
  source_diversity: "Source diversity",
  citation_coverage: "Claims verified",
  citation_support: "Citation support",
  axis_coverage: "Axis coverage",
  claim_verification_strength: "Source-term overlap",
  claim_evidence_quality: "Claim evidence quality",
  cross_source_agreement: "Cross-source agreement",
  critic_survival: "Critic survival",
  freshness: "Freshness",
};

/* Unknown keys are humanised rather than printed raw, so a new backend
 * signal never shows up as snake_case in the console. */
function signalLabel(key) {
  if (SIGNAL_LABELS[key]) return SIGNAL_LABELS[key];
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function ConfidenceBreakdown({ breakdown }) {
  const signals = breakdown?.signals;
  if (!signals || typeof signals !== "object") return null;
  const overall = typeof breakdown.overall === "number" ? Math.round(breakdown.overall * 100) : null;
  return (
    <section className="intel-section confidence-breakdown">
      <h3>Confidence breakdown</h3>
      {overall !== null ? (
        <div className="health-row">
          <IconChart size={15} className="tone-muted" />
          <span className="k"><strong>Overall</strong></span>
          <span className="v">{overall}%</span>
        </div>
      ) : null}
      {Object.entries(signals).map(([key, value]) => {
        const pct = typeof value === "number" ? Math.round(value * 100) : null;
        return (
          <div key={key}>
            <div className="health-row" style={{ paddingBottom: 2 }}>
              <span className="k">{signalLabel(key)}</span>
              <span className="v">{pct !== null ? `${pct}%` : "—"}</span>
            </div>
            <div className="bar" style={{ marginBottom: 8 }}>
              <div style={{ width: `${pct ?? 0}%` }} />
            </div>
            {key === "freshness" && pct === 0 ? (
              <div className="budget-sub note" style={{ marginBottom: 8 }}>
                publish dates not captured yet
              </div>
            ) : null}
          </div>
        );
      })}
    </section>
  );
}

function CitationHealthRow({ health }) {
  const summary = health && typeof health.summary === "object" ? health.summary : null;
  if (!summary) return null;
  const broken = (summary.broken || 0) + (summary.bad || 0);
  const warn = summary.warn || 0;
  const ok = summary.ok || 0;
  const total = ok + warn + broken + (summary.unchecked || 0);
  if (!total) return null;
  const tone = broken ? "warn" : warn ? "muted" : "good";
  const label = broken
    ? `${broken} broken of ${total}`
    : warn ? `${warn} flagged of ${total}`
    : `${ok}/${total} verified live`;
  return (
    <div className="health-row">
      <IconShield size={15} className={`tone-${tone}`} />
      <span className="k">Citation health</span>
      <span className="v" style={broken ? { color: "var(--deepscout-soft)" } : undefined}>{label}</span>
    </div>
  );
}

/* ---------- agent state ----------
 * Status is decided by which artifacts exist — the same monotonic ladder the
 * live pipeline strip uses — and each stage gets ONE honest descriptor drawn
 * from real counts. No estimated per-agent percentages.
 *
 * The ladder is remapped to the REAL eight-stage pipeline:
 *   browser(1) → planner(2) → human(3) → researcher(4) → writer(5) →
 *   fact_checker(6) → visualizer(7) → publisher(8)
 * `reached` is how far the run has actually got, so stage i is done when
 * reached > i and active when reached === i. */
export function deriveAgents(run) {
  const planned = run.plan.length;
  const sources = run.snippets || 0;
  const claims = run.findings.length;
  const verified = run.verifiedCount || 0;
  const reviews = run.critiques.length;
  const done = !!run.done;
  const live = !done && !run.error;

  // How far along the pipeline the run has actually reached (0..9).
  // Stage i (1-based) is done when reached > i, so a FULLY delivered run must
  // reach 9 to mark the eighth stage (publisher) done — reaching 8 left the
  // last stage stuck on "Queued" after the report shipped.
  let reached = 0;
  if (sources) reached = 1;
  if (planned) reached = 2;
  if (planned) reached = 3; // the plan-review gate auto-accepts in API mode
  if (claims) reached = 4;
  // A critic/review pass means the fact checker ran; a finished run has
  // published, which also implies the writer and visualizer completed.
  if (reviews) reached = 6;
  if (done) reached = 9;

  const desc = {
    browser: sources ? `${sources} source${sources === 1 ? "" : "s"} scoped` : "running the first research pass",
    planner: planned ? `${planned} section${planned === 1 ? "" : "s"} planned` : "deriving the outline",
    human: reached >= 3 ? "outline accepted" : "awaiting plan review",
    researcher: claims ? `${claims} claim${claims === 1 ? "" : "s"} extracted` : "researching each section",
    writer: verified ? "intro and conclusion written" : "writing the report body",
    fact_checker: reviews ? `${reviews} review pass${reviews === 1 ? "" : "es"}` : "checking the draft",
    visualizer: done ? "diagram generated" : "generating visuals",
    publisher: done ? "report delivered" : "assembling the report",
  };

  return PIPELINE.map((stage, i) => {
    const index = i + 1;
    const isDone = reached > index;
    const isActive = reached === index && live;
    return {
      icon: stage.icon,
      name: stage.name,
      status: isDone ? "done" : isActive ? "active" : "waiting",
      desc: desc[stage.key],
    };
  });
}

function deriveHealth(run) {
  const verified = run.findings.filter((f) => f.verified === true).length;
  const rows = [
    { icon: IconDoc, label: "Sources analyzed", value: run.done ? String(run.snippets) : (run.snippets ? String(run.snippets) : "—"), tone: "muted" },
    { icon: IconCheckCircle, label: "Total claims", value: String(run.findings.length), tone: "muted" },
    { icon: IconCheckCircle, label: "Verified claims", value: String(verified), tone: "good" },
    { icon: IconAlert, label: "Critic passes", value: String(run.critiques.length), tone: run.critiques.length > 1 ? "warn" : "muted", hot: run.critiques.length > 1 },
    { icon: IconTarget, label: "Research confidence", value: typeof run.confidence === "number" ? `${Math.round(run.confidence * 100)}%` : "—", tone: "muted" },
  ];
  if (typeof run.answerSupport === "number") {
    rows.push({
      icon: IconCheckCircle, label: "Answer support", tone: run.answerSupport >= 0.8 ? "good" : "warn",
      value: `${Math.round(run.answerSupport * 100)}%`, hot: run.answerSupport < 0.8,
    });
  }
  if (run.quality && typeof run.quality.overall === "number") {
    rows.push({
      icon: IconChart, label: "Answer quality",
      tone: run.quality.passed ? "good" : "warn",
      value: `${run.quality.overall}/100`, hot: !run.quality.passed,
    });
  }
  if (run.error) {
    rows.push({ icon: IconAlert, label: "Run status", value: "interrupted", tone: "bad", hot: true });
  }
  return rows;
}
