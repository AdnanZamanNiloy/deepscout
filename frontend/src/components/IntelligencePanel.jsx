import {
  IconAlert, IconChart, IconCheckCircle,
  IconDoc, IconPanel, IconRefresh, IconSearch, IconShield, IconShieldCheck, IconTarget,
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
 * (`app/agents/team.py`), in execution order. Same roster as AgentsView, so
 * "stage 3" means the same thing everywhere.
 *
 *   pathfinder → architect → review_gate → scout_swarm → narrative_writer →
 *   evidence_auditor → diagram_designer → report_assembler
 *
 * (`node` is the graph node name the wire reports, used to match live stages;
 * `desc` is filled from real counts in deriveAgents.) */
const PIPELINE = [
  { key: "pathfinder", node: "pathfinder", icon: IconSearch, name: "Pathfinder" },
  { key: "architect", node: "architect", icon: IconTarget, name: "Research Architect" },
  { key: "review_gate", node: "review_gate", icon: IconShieldCheck, name: "Review Gate" },
  { key: "scout_swarm", node: "scout_swarm", icon: IconSearch, name: "Scout Swarm" },
  { key: "narrative_writer", node: "narrative_writer", icon: IconDoc, name: "Narrative Writer" },
  { key: "evidence_auditor", node: "evidence_auditor", icon: IconShieldCheck, name: "Evidence Auditor" },
  { key: "diagram_designer", node: "diagram_designer", icon: IconChart, name: "Diagram Designer" },
  { key: "report_assembler", node: "report_assembler", icon: IconChart, name: "Report Assembler" },
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
              ) : run.planReview ? (
                /* The run is blocked on the user, not working. Saying
                   "streaming live" here is the same class of lie as the
                   all-Queued rail: the stream is open, but no agent is running. */
                <><strong>Waiting for your plan approval</strong> · run paused</>
              ) : (
                <><strong>{working} agent{working === 1 ? "" : "s"} working</strong> · streaming live</>
              )
            ) : (
              "No active run"
            )}
          </p>
        </div>
        <button
          className="icon-btn"
          onClick={onCollapse}
          aria-expanded={true}
          title="Hide run insights"
          aria-label="Hide the run insights panel"
        >
          <IconPanel size={15} />
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
 * Status comes from the LIVE stage lifecycle (`run.stages`), which the backend
 * emits as each node starts and finishes. That is the same signal the trace
 * below is drawn from, so the rail and the trace cannot disagree.
 *
 * It used to be inferred from finished artifacts instead (sources → plan →
 * claims → reviews → done). That ladder has no way to say "working": before the
 * first artifact landed it read `reached = 0`, so a run that had been streaming
 * for a minute showed all eight agents "Queued" and the header read "0 agents
 * working" while the trace was visibly working. It also could not tell
 * Pathfinder from Scout Swarm, and treated the plan-review gate as instantly
 * passed — untrue while the user is being asked to approve.
 *
 * The artifact ladder is still used as a FLOOR for runs with no stage frames
 * (a restored session whose lifecycle predates them), so a replayed run still
 * shows its work instead of an empty rail. */
export function deriveAgents(run) {
  const planned = run.plan.length;
  const sources = run.snippets || 0;
  const claims = run.findings.length;
  const reviews = run.critiques.length;
  const done = !!run.done;
  const live = !done && !run.error;
  const stages = run.stages || {};
  const stageKeys = Object.keys(stages);

  // Fast mode produces no figures at all (the backend skips DiagramDesigner
  // by design), so the rail must not show a stage that never runs — a
  // permanently "Queued" box is a lie about the pipeline for that mode.
  const visible = run.mode === "quick"
    ? PIPELINE.filter((stage) => stage.key !== "diagram_designer")
    : PIPELINE;

  // The stage-review gate is genuinely running whenever the user is being asked
  // to approve, which is a state the old ladder had no way to express.
  const awaitingReview = !!run.planReview;

  // Fallback ladder, only consulted when no lifecycle frames exist at all.
  // Expressed as the stage currently being worked on (1-based, 0 = none), the
  // same meaning the integer ladder had: everything before it is done, it is
  // active, everything after is queued. Keyed by stage name rather than a row
  // index so a mode that HIDES a stage cannot shift the meaning of the rest.
  let activeKey = "";
  if (sources) activeKey = "pathfinder";
  if (planned) activeKey = "review_gate";
  if (claims) activeKey = "scout_swarm";
  if (reviews) activeKey = "evidence_auditor";
  const activeIndex = activeKey
    ? visible.findIndex((stage) => stage.key === activeKey) + 1
    : 0;

  const hasLifecycle = stageKeys.length > 0 || awaitingReview;

  const desc = {
    pathfinder: sources ? `${sources} source${sources === 1 ? "" : "s"} scoped` : "running the first research pass",
    architect: planned ? `${planned} section${planned === 1 ? "" : "s"} planned` : "deriving the outline",
    review_gate: awaitingReview
      ? "awaiting plan review"
      : (stages.review_gate?.status === "done" || activeIndex >= 3 || done
        ? "outline accepted"
        : "checking the outline"),
    scout_swarm: claims ? `${claims} claim${claims === 1 ? "" : "s"} extracted` : "researching each section",
    narrative_writer: verified(run) ? "intro and conclusion written" : "writing the report body",
    evidence_auditor: reviews ? `${reviews} review pass${reviews === 1 ? "" : "es"}` : "checking the draft",
    diagram_designer: done ? "diagram generated" : "generating visuals",
    report_assembler: done ? "report delivered" : "assembling the report",
  };

  return visible.map((stage, i) => {
    const index = i + 1;
    const live_status = stages[stage.key]?.status;

    let status;
    if (live_status === "running") {
      status = live ? "active" : "done"; // a stream that died mid-stage finished nothing
    } else if (live_status === "done") {
      status = "done";
    } else if (awaitingReview && stage.key === "review_gate") {
      status = "active";
    } else if (hasLifecycle) {
      // The lifecycle is authoritative once present: a stage with no frame has
      // not started, so it is queued — not "done because a later artifact exists".
      status = "waiting";
    } else if (done) {
      // A finished run marks every remaining stage done (the old ladder used
      // "reached = one past the end"). Quick mode must still show a complete
      // rail even though it has no diagram stage.
      status = "done";
    } else {
      const isDone = activeIndex > index;
      status = isDone ? "done" : activeIndex === index && live ? "active" : "waiting";
    }

    return {
      icon: stage.icon,
      name: stage.name,
      status,
      desc: desc[stage.key],
    };
  });
}

const verified = (run) => (run.verifiedCount || 0) > 0;

function deriveHealth(run) {
  const verified = run.findings.filter((f) => f.verified === true).length;
  const rows = [
    { icon: IconDoc, label: "Sources analyzed", value: run.done ? String(run.snippets) : (run.snippets ? String(run.snippets) : "—"), tone: "muted" },
    { icon: IconCheckCircle, label: "Total claims", value: String(run.findings.length), tone: "muted" },
    { icon: IconCheckCircle, label: "Verified claims", value: String(verified), tone: "good" },
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
