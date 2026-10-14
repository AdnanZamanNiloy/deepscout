/* DeepScout static-demo fixtures: sessions, runs and traces.
 *
 * These mirror the shapes returned by the real backend so the sidebar and the
 * replay cards render against the demo exactly as they do against a live
 * server:
 *
 *   GET /api/sessions            -> { sessions: [ {id, title, status, ... } ] }
 *   GET /api/sessions/{id}       -> { id, title, messages: [...], runs: [...] }
 *   GET /api/research/{id}/trace -> get_run_trace() shape
 *
 * The trace shape was taken from `get_run_trace` in
 * backend/app/db/store/resume.py and the sessions shape from
 * backend/app/db/store/sessions.py. In particular:
 *
 *   - `critic_reviews[].breakdown` carries the REAL confidence object —
 *     {overall, signals, weights, notes, degraded_capped, engine_version} —
 *     because that is what the Intelligence panel's ConfidenceBreakdown reads
 *     (via the replay path), NOT the flat `confidence_breakdown` on the wire.
 *   - `claims` rows carry the real columns (claim, source_url, confidence,
 *     verified, agent, challenged).
 *   - `frames` holds the SAME authored frames the live conversation streams,
 *     minus the terminal report (the replay card renders the report from
 *     `final_report`), so a replayed run and a fresh run look alike.
 */

import { CONVERSATIONS } from "./conversations";

const RUN_IDS = [
  "demo-run-in-demand-skill",
  "demo-run-demanding-topics",
  "demo-run-what-is-rag",
  "demo-run-llm-cost-accuracy",
  "demo-run-ai-trend",
];

const SESSION_IDS = [
  "demo-session-in-demand-skill",
  "demo-session-demanding-topics",
  "demo-session-what-is-rag",
  "demo-session-llm-cost-accuracy",
  "demo-session-ai-trend",
];

/* Claims per run — real columns, real value ranges (confidence 0.72-0.93,
 * agent "pipeline", verified 1, challenged 0). Kept consistent with each
 * report's citations: the product verifies claims against sources, so the
 * demo must not assert more than its own evidence shows. */
const CLAIMS = [
  [
    { claim: "Replacement demand can keep an occupation's hiring volume high even while its total headcount falls.", source_url: "https://www.cedefop.europa.eu/files/4213_en.pdf", verified: 1, confidence: 0.84, agent: "pipeline", challenged: 0 },
    { claim: "Across ten countries, roughly three quarters to four fifths of AI-related vacancies sit in STEM occupations.", source_url: "http://arxiv.org/abs/2607.28798v1", verified: 1, confidence: 0.79, agent: "pipeline", challenged: 0 },
    { claim: "The AI skills premium sits above the degree premium below the PhD level in a UK vacancy study.", source_url: "http://arxiv.org/abs/2312.11942v2", verified: 1, confidence: 0.81, agent: "pipeline", challenged: 0 },
    { claim: "Training lead times mean a 2026 demand signal cannot become qualified supply by 2027.", source_url: "https://www.gov.uk/government/publications/ai-skills-for-life-and-work-labour-market-and-skills-projections/ai-skills-for-life-and-work-labour-market-and-skills-projections", verified: 1, confidence: 0.75, agent: "pipeline", challenged: 0 },
    { claim: "The OECD describes skills mismatch as a widespread problem.", source_url: "https://www.mdpi.com/2227-7102/16/7/1156", verified: 1, confidence: 0.73, agent: "pipeline", challenged: 0 },
  ],
  [
    { claim: "Serverless cold-start latency is described as an unresolved research problem in a 2023 systematic review.", source_url: "https://arxiv.org/abs/2301.00001", verified: 1, confidence: 0.86, agent: "pipeline", challenged: 0 },
    { claim: "A survey concluded self-adaptive cloud autoscaling systems are not yet mature enough for reliable cloud exploitation.", source_url: "https://arxiv.org/abs/2306.00002", verified: 1, confidence: 0.83, agent: "pipeline", challenged: 0 },
    { claim: "Reasoning benchmark ceilings resist improvement by scaling data alone.", source_url: "https://arxiv.org/abs/2312.00003", verified: 1, confidence: 0.78, agent: "pipeline", challenged: 0 },
    { claim: "Eye-tracking HCI research between 2020 and 2025 is organised around four principal axes.", source_url: "https://arxiv.org/abs/2301.00001", verified: 1, confidence: 0.72, agent: "pipeline", challenged: 1 },
  ],
  [
    { claim: "Lewis et al. (2020) introduced RAG by combining a parametric memory with a non-parametric retrieval corpus.", source_url: "https://arxiv.org/abs/2005.11401", verified: 1, confidence: 0.93, agent: "pipeline", challenged: 0 },
    { claim: "Static RAG retrieves documents once before generation and cannot recover from a bad first retrieval.", source_url: "https://arxiv.org/abs/2305.06983", verified: 1, confidence: 0.88, agent: "pipeline", challenged: 0 },
    { claim: "FLARE retrieves when the model's own confidence drops during generation.", source_url: "https://arxiv.org/abs/2305.06983", verified: 1, confidence: 0.9, agent: "pipeline", challenged: 0 },
    { claim: "DRAGIN decides when and what to retrieve from the model's information needs.", source_url: "https://arxiv.org/abs/2403.10081", verified: 1, confidence: 0.87, agent: "pipeline", challenged: 0 },
  ],
  [
    { claim: "A MongoDB Atlas M50 dedicated cluster was priced at approximately $1,700 per month as of 2026, a list price.", source_url: "https://example.org/atlas-pricing", verified: 1, confidence: 0.74, agent: "pipeline", challenged: 0 },
    { claim: "No primary benchmark in the retrieved pool compares the candidate models on analytics-specific tasks.", source_url: "https://example.org/analytics-bench", verified: 1, confidence: 0.8, agent: "pipeline", challenged: 0 },
    { claim: "Two sources disagree on throughput per GPU by more than 2x.", source_url: "https://example.org/vendor-bench", verified: 1, confidence: 0.66, agent: "pipeline", challenged: 1 },
    { claim: "Smaller models handle high-volume low-complexity queries at a fraction of the cost with little measured accuracy loss.", source_url: "https://example.org/analytics-bench", verified: 1, confidence: 0.77, agent: "pipeline", challenged: 0 },
  ],
  [
    { claim: "On a specific family of reasoning benchmarks, measured progress has stalled.", source_url: "https://arxiv.org/abs/2405.20362v1", verified: 1, confidence: 0.63, agent: "pipeline", challenged: 0 },
    { claim: "The apparent stagnation is partly an artifact of benchmark choice.", source_url: "https://arxiv.org/abs/2405.20362v1", verified: 1, confidence: 0.58, agent: "pipeline", challenged: 0 },
  ],
];

/* The real confidence object shape (confidence.py / critic_reviews.breakdown). */
function confidenceBreakdown(overall, signals, notes, weights) {
  return {
    overall,
    signals,
    weights,
    notes,
    degraded_capped: false,
    engine_version: "confidence-v2",
    caps_applied: [],
    stats: { weights, engine_overall: overall, epistemic_adjustments: {}, adjusted_delta: 0.0 },
  };
}

const BREAKDOWNS = [
  confidenceBreakdown(0.62,
    { source_quality: 0.86, source_diversity: 0.47, citation_coverage: 1.0, claim_verification_strength: 0.0, cross_source_agreement: 0.0, freshness: 0.7, critic_survival: 0.6, claim_evidence_quality: 0.72 },
    ["The pay evidence attaches to AI skills, not AI-adjacent titles; two premium estimates disagree and both are reported."],
    { source_quality: 0.15, source_diversity: 0.1, citation_coverage: 0.2, claim_verification_strength: 0.15, cross_source_agreement: 0.15, critic_survival: 0.1, freshness: 0.05, axis_coverage: 0.05, claim_evidence_quality: 0.05 },
  ),
  confidenceBreakdown(0.62,
    { source_quality: 0.88, source_diversity: 0.4, citation_coverage: 1.0, claim_verification_strength: 0.0, cross_source_agreement: 0.0, freshness: 0.0, critic_survival: 0.6, claim_evidence_quality: 0.75 },
    ["3 of 4 claims meet the evidence standard for their claim type; one single-source taxonomy is unhedged."],
    { source_quality: 0.15, source_diversity: 0.1, citation_coverage: 0.2, claim_verification_strength: 0.15, cross_source_agreement: 0.15, critic_survival: 0.1, freshness: 0.05, axis_coverage: 0.05, claim_evidence_quality: 0.05 },
  ),
  confidenceBreakdown(0.74,
    { source_quality: 0.88, source_diversity: 0.0, citation_coverage: 1.0, claim_verification_strength: 0.0, cross_source_agreement: 0.0, freshness: 0.0, critic_survival: 0.6, claim_evidence_quality: 0.75 },
    ["All claims corroborated against their cited primary source."],
    { source_quality: 0.15, source_diversity: 0.1, citation_coverage: 0.2, claim_verification_strength: 0.15, cross_source_agreement: 0.15, critic_survival: 0.1, freshness: 0.05, axis_coverage: 0.05, claim_evidence_quality: 0.05 },
  ),
  confidenceBreakdown(0.52,
    { source_quality: 0.71, source_diversity: 0.43, citation_coverage: 1.0, claim_verification_strength: 0.0, cross_source_agreement: 0.0, freshness: 0.64, critic_survival: 0.5, claim_evidence_quality: 0.6 },
    ["An unresolved throughput contradiction lowered cross-source agreement; one cost figure is a list price, not a negotiated rate."],
    { source_quality: 0.15, source_diversity: 0.1, citation_coverage: 0.2, claim_verification_strength: 0.15, cross_source_agreement: 0.15, critic_survival: 0.1, freshness: 0.05, axis_coverage: 0.05, claim_evidence_quality: 0.05 },
  ),
  confidenceBreakdown(0.55,
    { source_quality: 0.8, source_diversity: 0.31, citation_coverage: 0.9, claim_verification_strength: 0.0, cross_source_agreement: 0.0, freshness: 0.72, critic_survival: 0.3, claim_evidence_quality: 0.55 },
    ["Summariser degraded on weak evidence; confidence capped per DEGRADED_CAP."],
    { source_quality: 0.15, source_diversity: 0.1, citation_coverage: 0.2, claim_verification_strength: 0.15, cross_source_agreement: 0.15, critic_survival: 0.1, freshness: 0.05, axis_coverage: 0.05, claim_evidence_quality: 0.05 },
  ),
];

/* Sources, ordered by their appearance in the reports. */
const SOURCES = [
  ["https://www.cedefop.europa.eu/files/4213_en.pdf", "Skills forecast methodological framework", "Cedefop"],
  ["http://arxiv.org/abs/2607.28798v1", "AI-related vacancies across ten countries", "arXiv"],
  ["http://arxiv.org/abs/2312.11942v2", "AI skills and the labour market", "arXiv"],
  ["https://www.gov.uk/government/publications/ai-skills-for-life-and-work-labour-market-and-skills-projections/ai-skills-for-life-and-work-labour-market-and-skills-projections", "AI skills for life and work: labour market projections", "GOV.UK"],
  ["https://arxiv.org/abs/2301.00001", "Serverless cold-start latency: a systematic review", "arXiv"],
  ["https://arxiv.org/abs/2306.00002", "Self-aware and self-adaptive cloud autoscaling", "arXiv"],
  ["https://arxiv.org/abs/2312.00003", "Limits of scale in reasoning benchmarks", "arXiv"],
  ["https://arxiv.org/abs/2005.11401", "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks", "arXiv"],
  ["https://arxiv.org/abs/2305.06983", "Active Retrieval Augmented Generation", "arXiv"],
  ["https://arxiv.org/abs/2403.10081", "DRAGIN: Dynamic Retrieval Augmented Generation", "arXiv"],
  ["https://arxiv.org/abs/2405.20362v1", "Does source matter? Benchmark stagnation and trend measurement", "arXiv"],
  ["https://example.org/atlas-pricing", "MongoDB Atlas pricing", "Example Cloud"],
  ["https://example.org/analytics-bench", "Analytics-specific benchmark", "Example Institute"],
  ["https://example.org/vendor-bench", "Vendor throughput benchmark", "Example Vendor"],
];

function sourceRows(i) {
  return SOURCES.map(([url, title, publisher], idx) => ({
    id: i * 100 + idx,
    url,
    title,
    publisher,
    reliability_score: 0.72 + (idx % 4) * 0.05,
    fetched_at: "",
    source_domain: url.replace(/^https?:\/\//, "").split("/")[0],
    retrieval_provider: "demo",
    retrieval_engine: "demo",
  }));
}

const NOTES = [
  "The strongest pay evidence attaches to AI skills rather than AI-adjacent titles, and two premium estimates disagree, so both are reported with the vacancy-corpus estimate marked as more interpretable.",
  "The retrieved literature names open problems but contains no study of which topics master's students actually complete, so the recommendation rests on surveys' own gap statements rather than a ranked difficulty.",
  "The definitional claims were corroborated across three independent primary sources and left unhedged; the variant naming was standardised to the most common form.",
  "Several cost figures were attributed to list prices rather than enterprise rates; the re-draft corrects the attribution and surfaces the throughput disagreement explicitly.",
  "The evidence is heavily skewed toward narrow technical sub-domains and a single firm survey, so the original broad question is only partially answered.",
];

/* Build the trace the replay card reads for one conversation. */
function traceFor(i) {
  const convo = CONVERSATIONS[i];
  const reportText = convo.frames.find((f) => f.type === "final_report")?.report || "";
  const finalFrame = convo.frames.find((f) => f.type === "final_report") || {};
  const reviews = convo.frames.filter((f) => f.type === "critic");
  return {
    run_id: RUN_IDS[i],
    query: convo.query,
    status: "completed",
    confidence: convo.confidence,
    created_at: new Date(Date.now() - (i + 1) * 3600_000).toISOString(),
    completed_at: new Date(Date.now() - (i + 1) * 3600_000 + 90_000).toISOString(),
    claims: CLAIMS[i],
    sources: sourceRows(i),
    evidence: CLAIMS[i].map((c, idx) => ({
      id: i * 1000 + idx, source_id: i * 100 + idx, raw_snippet: c.claim, created_at: "",
    })),
    /* One critic_review per critic frame. Real columns:
     * id, run_id, iteration, is_sufficient, reason, confidence, breakdown,
     * created_at, improved_queries. `breakdown` is the REAL confidence object. */
    critic_reviews: (reviews.length ? reviews : [{ reason: NOTES[i] }]).map((r, idx) => ({
      id: i * 10 + idx,
      iteration: idx + 1,
      is_sufficient: idx === reviews.length - 1 ? 1 : 0,
      reason: r.reason || NOTES[i],
      confidence: convo.confidence,
      breakdown: BREAKDOWNS[i],
      created_at: "",
      improved_queries: [],
    })),
    decisions: [],
    contradictions: finalFrame.contradictions || [],
    verification_results: CLAIMS[i].map((c, idx) => ({
      id: i * 1000 + idx, claim: c.claim, verified: 1, score: c.confidence, reason: "corroborated", created_at: "",
    })),
    citations: [],
    events: [],
    /* The pipeline rail replays these. Strip the authored `delay` (a replay
     * detail) and the terminal frame — the replay card renders the report from
     * `final_report` below, so re-streaming it would double it. */
    frames: convo.frames
      .filter((f) => f.type !== "final_report")
      .map(({ delay, ...rest }) => rest),
    final_report: {
      report_markdown: reportText,
      audit_markdown: "",
      confidence: convo.confidence,
      generated_at: "",
    },
    facts: CLAIMS[i].map((c) => ({ claim: c.claim, source_url: c.source_url, verified: 1 })),
  };
}

/* Sidebar rows — newest activity first, matching list_sessions() ordering. */
export const DEMO_SESSIONS = CONVERSATIONS.map((c, i) => ({
  id: SESSION_IDS[i],
  title: c.query,
  query: c.query,
  status: "completed",
  confidence: c.confidence,
  created_at: new Date(Date.now() - (i + 1) * 3600_000).toISOString(),
  updated_at: new Date(Date.now() - (i + 1) * 3600_000).toISOString(),
  run_ids: [RUN_IDS[i]],
  run_count: 1,
}));

/* Session detail — ordered user + run messages, per chat. */
export function demoSessionDetail(sessionId) {
  const i = SESSION_IDS.indexOf(sessionId);
  if (i === -1) return null;
  const convo = CONVERSATIONS[i];
  const report = convo.frames.find((f) => f.type === "final_report")?.report || "";
  const at = new Date(Date.now() - (i + 1) * 3600_000).toISOString();
  return {
    id: sessionId,
    title: convo.query,
    created_at: at,
    updated_at: at,
    messages: [
      { id: `${RUN_IDS[i]}-user`, role: "user", text: convo.query, created_at: at, run_id: RUN_IDS[i] },
      {
        id: `${RUN_IDS[i]}-run`, role: "run", run_id: RUN_IDS[i], status: "completed",
        confidence: convo.confidence, created_at: at, report, audit: "", query: convo.query,
      },
    ],
    runs: [{ id: RUN_IDS[i], query: convo.query, status: "completed", confidence: convo.confidence, created_at: at }],
    run_ids: [RUN_IDS[i]],
    run_count: 1,
  };
}

export function demoTrace(runId) {
  const i = RUN_IDS.indexOf(runId);
  return i === -1 ? null : traceFor(i);
}

export const DEMO_RUN_IDS = RUN_IDS;
export const DEMO_SESSION_IDS = SESSION_IDS;
