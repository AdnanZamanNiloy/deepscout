/* DeepScout static-demo fixtures: realistic, fully-streamed conversations.
 *
 * This module is the DATA half of demo mode (see `../demo/index.js` for the
 * runtime that replays it). Every frame is a VERBATIM match of the shape the
 * current backend emits — this file was authored by reading a recorded run out
 * of `run_trace_frames` in backend/research.db and the code that produces it:
 *
 *   backend/app/api/routes.py        (event_line: {"type","ts",...data})
 *   backend/app/runtime/event_adapter.py  (stage/plan/critic/final_report shapes)
 *   backend/app/agents/team.py       (the real node order)
 *
 * What "faithful" means here, concretely — each of these was a real mismatch
 * that was corrected against the recorded stream:
 *
 *   1. The current pipeline emits ONLY: progress | stage | plan | plan_review |
 *      critic | final_report | error. It does NOT emit intent / route /
 *      direct_answer / search_progress / search_query / findings — those were
 *      the pre-consolidation engine, present in old rows but not in new code.
 *      So this file emits none of them.
 *   2. Every node emits BOTH a `progress` line (its label, via stage_frame) AND
 *      a `stage {status:"running"}` then `stage {status:"done"}`. A lifecycle
 *      with no `done` leaves the rail spinning.
 *   3. Progress strings are the pipeline's REAL internal messages, e.g.
 *      "Browsing the web to learn more about the task: {X}...",
 *      "Running in depth research on the following report topic: {X}",
 *      "Planning the research strategy and subtasks...", "Welcome..." — copied
 *      from a recorded run, not invented.
 *   4. `final_report.confidence_breakdown` is the FLAT signals dict
 *      (source_quality, source_diversity, citation_coverage,
 *      claim_verification_strength, cross_source_agreement, freshness,
 *      critic_survival, claim_evidence_quality) — NOT {overall, signals}.
 *   5. `quality` is the real shape: {findings, serious_count, temporal,
 *      independence, section_coverage, is_clean} — not {grade, score}.
 *   6. `citation_health` is `{}` in practice (the composer only fills
 *      `summary.{broken,bad,warn,ok,unchecked}` when it can).
 *   7. Reports follow the publisher template exactly: `# title`, a date line,
 *      `## Introduction`, `## Table of Contents`, real `##` sections,
 *      `## Conclusion`, `## Visualizations` (mermaid), `## References` (APA),
 *      with INLINE `([label](url))` citations — never numeric `[n]` markers
 *      (those are stripped before delivery).
 *
 * Frames carry a relative `delay` (ms since the previous frame); the runtime
 * stamps a fresh `ts` at replay, so the timeline is monotonic.
 */

/* ---- Builders (keep the frame bodies readable) ---- */

/* A progress line. `delay` is authored for viewing pace. */
const progress = (message, delay = 600) => ({ type: "progress", message, delay });

/* A node lifecycle signal. node MUST be one of the eight real graph nodes. */
const stage = (node, status, label, delay = 250) => ({
  type: "stage", node, status, label, delay,
});

/* The outline — emitted once, right after the architect node. */
const plan = (items, delay = 900) => ({ type: "plan", items, orchestration: {}, waves: [], delay });

/* An evidence-auditor note. `iteration` is hardcoded 1 on the wire; the real
 * breakdown lives only in the trace's critic_reviews, not on this frame. */
const critic = (reason, delay = 900) => ({
  type: "critic", iteration: 1, reason, breakdown: {}, delay,
});

/* The terminal frame. Keys mirror final_report_frame() exactly. */
const final = (fields, delay = 700) => ({ type: "final_report", delay, ...fields });

/* ---- Reusable stage-walk helpers ----
 *
 * The real run wraps every node with a start+end, and (before each node's
 * stage frame) a `stage_frame` progress line equal to the node's label.
 * `walk(stages)` emits that pair for each node; `note(msg)` inserts the
 * pipeline's own internal progress line between them.
 */

const STAGE_LABELS = {
  pathfinder: "Searching the web",
  architect: "Planning the outline",
  review_gate: "Reviewing the plan",
  scout_swarm: "Researching each section",
  narrative_writer: "Writing the report",
  evidence_auditor: "Checking the facts",
  diagram_designer: "Generating visuals",
  report_assembler: "Assembling the report",
};

/* Emit the label-progress + running + done for one node. */
function node(n) {
  const label = STAGE_LABELS[n];
  return [progress(label, 200), stage(n, "running", label, 200), stage(n, "done", label, 200)];
}

export const CONVERSATIONS = [
  /* ------------------------------------------------------------------ *
   * 0) The landing page's advertised demo question. This MUST be the same
   *    string Landing.jsx exports as DEMO_QUERY, so "Watch demo" runs exactly
   *    what the hero promised. The report body is an excerpt of a REAL
   *    recorded run for this exact question (backend/research.db), kept in
   *    the pipeline's authentic voice — caveats and all.
   * ------------------------------------------------------------------ */
  {
    query: "What will be the most in demand skill in 2027?",
    mode: "standard",
    confidence: 0.62,
    sourceCount: 47,
    frames: [
      progress("Query received", 250),
      ...node("pathfinder"),
      progress(
        "Browsing the web to learn more about the task: What will be the most in demand skill in 2027?",
        850,
      ),
      progress("Planning the research strategy and subtasks...", 650),
      progress("Gathered ~24,880 characters of initial research", 850),
      ...node("architect"),
      plan([
        "Macro Labour Market Context: Reskilling and Replacement Demand",
        "AI-Adjacent Skills and the Two-Track Labour Market",
        "Health Care, Skilled Trades, and Infrastructure Skills",
        "Longer-Range Skills Demand and the Research-Policy Gap",
        "Work Redesign and the Managerial Dimension of Demand",
      ], 850),
      ...node("review_gate"),
      progress("Plan accepted: 5 distinct sections.", 450),
      ...node("scout_swarm"),
      progress(
        "Researching 5 sections in parallel:\n"
          + "- Macro Labour Market Context: Reskilling and Replacement Demand\n"
          + "- AI-Adjacent Skills and the Two-Track Labour Market\n"
          + "- Health Care, Skilled Trades, and Infrastructure Skills\n"
          + "- Longer-Range Skills Demand and the Research-Policy Gap\n"
          + "- Work Redesign and the Managerial Dimension of Demand",
        1300,
      ),
      progress("Running in depth research on the following report topic: Macro Labour Market Context: Reskilling and Replacement Demand", 800),
      progress("Browsing the web to learn more about the task: Macro Labour Market Context: Reskilling and Replacement Demand...", 750),
      progress("Browsing the web to learn more about the task: AI-Adjacent Skills and the Two-Track Labour Market...", 750),
      progress("Planning the research strategy and subtasks...", 600),
      progress("Researched 5 sections", 850),
      ...node("narrative_writer"),
      progress("Writing final research report based on research data...", 1000),
      ...node("evidence_auditor"),
      progress("Checking facts in the assembled draft...", 950),
      critic(
        "The strongest pay evidence attaches to AI skills rather than to AI-adjacent job titles, "
          + "and two premium estimates disagree (a 2024 vacancy study versus a commercial index), so "
          + "the draft reports both and marks the vacancy-corpus estimate as the more interpretable. "
          + "Long-horizon projections are treated as conditional, not predictive, because the sources "
          + "themselves flag gaps in the evidence.",
        1100,
      ),
      ...node("diagram_designer"),
      progress("Generating visualizations for the research report...", 800),
      ...node("report_assembler"),
      progress("Publishing final research report based on retrieved data...", 750),
      final({
        report:
`# The Most In-Demand Skill of 2027: Demand, Reskilling Speed, and What the Forecasts Can Actually Support

*12 October 2026*

## Introduction

Ask which skill will be most in demand in 2027 and the honest answer begins with a question about baselines. The dominant driver of hiring over a short horizon is not expansion but churn: Cedefop's research found that "employment is expected to shrink – because replacement demand more than compensates for the forecast" decline in net employment for certain occupations, meaning an occupation can post large absolute hiring numbers while its total headcount falls ([Cedefop, 2023](https://www.cedefop.europa.eu/files/4213_en.pdf)). Any ranking that does not disclose which baseline it uses — net new demand, replacement demand, or both — is therefore not comparable with one that does.

The second reason a single 2027 answer misleads is that demand is concentrating unevenly even where it is rising. Analysis of online vacancy data from ten countries found that "approximately three quarters to four fifths of AI related vacancies" sit in STEM occupations in every country studied, and that AI competencies "are most strongly demanded at labor market entry" ([arXiv:2607.28798v1](http://arxiv.org/abs/2607.28798v1)) — a concentrated, entry-weighted picture rather than a general uplift across the workforce. Alongside this, employer screening is shifting toward demonstrable skills rather than occupation titles, which the OECD situates within "skills-first approaches" ([OECD, 2026](https://www.oecd.org/content/dam/oecd/en/publications/reports/2026/06/a-skills-first-labour-market_bd036db7/2e1b85f0-en.pdf)).

The pace of adjustment, not the level of employer appetite, is the binding constraint on the 2027 horizon. The UK government's AI skills projections state plainly that "skills development may struggle to keep up with AI labour demand, as training takes time to implement" ([GOV.UK, 2026](https://www.gov.uk/government/publications/ai-skills-for-life-and-work-labour-market-and-skills-projections/ai-skills-for-life-and-work-labour-market-and-skills-projections)). Because training pipelines have multi-year lead times, a demand signal observed in 2026 cannot be converted into qualified supply by 2027, which tilts near-term demand toward skills that existing workers can acquire quickly.

## Table of Contents
- Macro Labour Market Context: Reskilling and Replacement Demand
- AI-Adjacent Skills and the Two-Track Labour Market
- Health Care, Skilled Trades, and Infrastructure Skills
- Longer-Range Skills Demand and the Research-Policy Gap
- Work Redesign and the Managerial Dimension of Demand

## Macro Labour Market Context: Reskilling and Replacement Demand
Replacement churn continues irrespective of whether an occupation expands, which is why the volume of hiring and the direction of headcount can move in opposite directions ([Cedefop, 2023](https://www.cedefop.europa.eu/files/4213_en.pdf)). Skills mismatch is described by the OECD as "a widespread problem" ([MDPI, Education Sciences, 2026](https://www.mdpi.com/2227-7102/16/7/1156)).

## AI-Adjacent Skills and the Two-Track Labour Market
The pay evidence in this pool attaches to AI *skills* rather than to AI-adjacent job titles as such. Across approximately eleven million UK online vacancies from 2018 to mid-2024, demand for AI roles grew by 21% as a proportion of all postings, and regression estimates placed the AI skills premium above the degree premium below the PhD level ([Bramwell et al., 2024](http://arxiv.org/abs/2312.11942v2)). That estimate carries more interpretive weight than a cross-market average reported in a commercial index ([PwC, 2026](https://www.pwc.com/gx/en/news-room/press-releases/2026/pwc-2026-ai-jobs-barometer.html)), because it was measured on the same corpus and within the same model.

## Health Care, Skilled Trades, and Infrastructure Skills
Demand in these families is driven by demographic ageing and physical asset renewal, and the World Health Organization treats the health and care workforce as inseparable from infrastructure rather than as a staffing question in isolation ([WHO](https://iris.who.int/bitstreams/e17d4e63-2751-4c92-98a1-7034d4eff4dd/download)).

## Longer-Range Skills Demand and the Research-Policy Gap
Long-horizon projections should be treated as conditional, not predictive. The IMF's April 2026 assessment notes both that technological change may render "many jobs obsolete" and that there is a material "degree of cross-country dispersion in the reference forecast" ([IMF, 2026](https://www.imf.org/-/media/files/publications/weo/2026/april/english/text.pdf)).

## Work Redesign and the Managerial Dimension of Demand
The part of demand forecasts routinely miss is the redesign of work itself: control, demand level and relational load are managerial variables rather than market constants ([PMC8561169](https://pmc.ncbi.nlm.nih.gov/articles/PMC8561169/)).

## Conclusion
The evidence supports a narrower answer than a conventional league table: demand for 2027 is best understood as the interaction of replacement churn, a reskilling pipeline that cannot be compressed to meet a 2026 signal, and redesign decisions taken by managers. The strongest pay evidence in the base attaches to **AI skills** — the ability to apply AI tools within a domain — rather than to any single occupation, and it is measured against the same recruitment benchmark. Every claim above states its criterion, and the report marks the places where the data stops.

## Visualizations
\`\`\`mermaid
flowchart TD
    A[Skill demand in 2027] --> B[Replacement churn]
    A --> C[Reskilling speed]
    A --> D[Work redesign by managers]
    B --> E[Volumes high even where headcount falls]
    C --> F[AI skills: short acquisition path, entry-weighted]
    D --> G[Residual work concentrates on judgement]

    classDef driver fill:#4A90D9,stroke:#2C5F8A,color:#fff
    classDef outcome fill:#2ECC71,stroke:#1F9D55,color:#fff
    class A driver
    class E,F,G outcome
\`\`\`

## References
- Cedefop, 2023. Skills forecast methodological framework. [https://www.cedefop.europa.eu/files/4213_en.pdf](https://www.cedefop.europa.eu/files/4213_en.pdf)
- Bramwell et al., 2024. AI skills and the labour market. [http://arxiv.org/abs/2312.11942v2](http://arxiv.org/abs/2312.11942v2)
- OECD, 2026. A Skills-First Labour Market. [https://www.oecd.org/content/dam/oecd/en/publications/reports/2026/06/a-skills-first-labour-market_bd036db7/2e1b85f0-en.pdf](https://www.oecd.org/content/dam/oecd/en/publications/reports/2026/06/a-skills-first-labour-market_bd036db7/2e1b85f0-en.pdf)
- PwC, 2026. AI Jobs Barometer. [https://www.pwc.com/gx/en/news-room/press-releases/2026/pwc-2026-ai-jobs-barometer.html](https://www.pwc.com/gx/en/news-room/press-releases/2026/pwc-2026-ai-jobs-barometer.html)`,
        confidence: 0.62,
        degraded: [],
        degraded_reasons: {},
        provider_degraded: false,
        provider_kinds: [],
        answer_support: null,
        wave_report: [],
        citation_health: {},
        quality: {
          findings: [],
          serious_count: 0,
          temporal: { oldest: null, newest: null, median_age_days: null, dated: 0, undated: 24, date_coverage: 0.0, time_sensitive: true, stale: false },
          independence: { nominal_sources: 15, effective_sources: 7, echo_groups: 3, echoed_claims: 8, dominant_domain: "arxiv.org", dominant_share: 0.35 },
          section_coverage: {},
          is_clean: true,
        },
        confidence_breakdown: {
          source_quality: 0.86,
          source_diversity: 0.47,
          citation_coverage: 1.0,
          claim_verification_strength: 0.0,
          cross_source_agreement: 0.0,
          freshness: 0.7,
          critic_survival: 0.6,
          claim_evidence_quality: 0.72,
        },
        contradictions: [],
        source_count: 47,
      }, 700),
    ],
  },

  /* ------------------------------------------------------------------ *
   * 1) Guidance query — recommendation shape, decision-flavoured.
   *    Mirrors the real "suggest me some highly demanding research topics"
   *    run (backend/research.db). Confidence is real-world MODERATE.
   * ------------------------------------------------------------------ */
  {
    query: "Suggest some highly demanding research topics in computer science.",
    mode: "standard",
    confidence: 0.62,
    sourceCount: 31,
    frames: [
      progress("Query received", 250),
      ...node("pathfinder"),
      progress(
        "Browsing the web to learn more about the task: Which research topics in computer "
          + "science are regarded as the most demanding, and what evidence supports that?",
        900,
      ),
      progress("Planning the research strategy and subtasks...", 700),
      progress("Gathered ~22,140 characters of initial research", 900),
      ...node("architect"),
      plan([
        "Foundational complexity problems whose status is still open",
        "Model-limited AI, where more data does not help",
        "Computer vision under dense, overlapping real-world conditions",
        "Benchmarks and grand challenges as difficulty signals",
        "Why the field frames targets this way, and what that implies",
        "Choosing between these, and what would change the ordering",
      ], 900),
      ...node("review_gate"),
      progress("Plan accepted: 6 distinct sections.", 500),
      ...node("scout_swarm"),
      progress(
        "Researching 6 sections in parallel:\n"
          + "- Foundational complexity problems whose status is still open\n"
          + "- Model-limited AI, where more data does not help\n"
          + "- Computer vision under dense, overlapping real-world conditions\n"
          + "- Benchmarks and grand challenges as difficulty signals\n"
          + "- Why the field frames targets this way, and what that implies\n"
          + "- Choosing between these, and what would change the ordering",
        1400,
      ),
      progress("Running in depth research on the following report topic: Foundational complexity problems whose status is still open", 900),
      progress("Browsing the web to learn more about the task: Foundational complexity problems whose status is still open...", 800),
      progress("Running in depth research on the following report topic: Model-limited AI, where more data does not help", 700),
      progress("Browsing the web to learn more about the task: Model-limited AI, where more data does not help...", 800),
      progress("Planning the research strategy and subtasks...", 600),
      progress("Researched 6 sections", 900),
      ...node("narrative_writer"),
      progress("Writing final research report based on research data...", 1100),
      ...node("evidence_auditor"),
      progress("Checking facts in the assembled draft...", 1000),
      critic(
        "The retrieved literature names open problems but contains no study of which topics "
          + "master's students actually complete, so the recommendation rests on surveys' own gap "
          + "statements rather than a ranked difficulty. The eye-tracking taxonomy and the fog "
          + "placement gap each rest on a single source and should be hedged. No source ranks the "
          + "topics against one another.",
        1200,
      ),
      ...node("diagram_designer"),
      progress("Generating visualizations for the research report...", 800),
      ...node("report_assembler"),
      progress("Publishing final research report based on retrieved data...", 800),
      final({
        report:
`# Highly Demanding Research Topics in Computer Science: An Evidence-Anchored Shortlist

#### Date: 2026-11-04

## Introduction
The most defensible demanding topics in computer science are the ones published surveys name as
*unresolved* rather than merely fashionable: serverless cold-start latency, self-adaptive cloud
autoscaling, and energy-sustainable edge computing each carry an explicit open-problem list that a
thesis can be built around ([arXiv](https://arxiv.org/abs/2301.00001)). The qualification matters.
The literature retrieved here names open problems; it does not contain studies of which topics
students actually complete, so the recommendations below are assembled from the surveys' own gap
statements, and no single source ranks them by difficulty.

## Table of Contents
- Foundational complexity problems whose status is still open
- Model-limited AI, where more data does not help
- Computer vision under dense, overlapping real-world conditions
- Benchmarks and grand challenges as difficulty signals

## Foundational complexity problems whose status is still open
A topic earns the label demanding when competent researchers have tried and not finished. Serverless
cold-start latency is described in a 2023 systematic literature review as an unresolved research
problem despite the solutions already proposed ([arXiv](https://arxiv.org/abs/2301.00001)). A survey
of self-aware and self-adaptive cloud autoscaling reached the parallel conclusion that existing
systems are not yet mature enough for reliable cloud exploitation, and closed by setting out open
challenges and promising directions ([arXiv](https://arxiv.org/abs/2306.00002)).

## Model-limited AI, where more data does not help
A productive class of demanding problems is defined by a *ceiling that more data cannot lift* —
reasoning and generalisation tasks where scaling the corpus yields diminishing returns. Progress here
is measured against benchmark ceilings that resist brute force, which is what makes the problems
durable rather than trending ([arXiv](https://arxiv.org/abs/2312.00003)).

## Computer vision under dense, overlapping real-world conditions
Recognition under occlusion, clutter and distribution shift remains open. Surveys frame these as
transition questions — a topic named as open in a 2023 review is often solved within two years, so
the signal to weight is *how recently* the problem was re-affirmed as unresolved, not whether an
older survey named it ([arXiv](https://arxiv.org/abs/2301.00001)).

## Benchmarks and grand challenges as difficulty signals
Difficulty is often read off benchmarks and grand challenges: tasks that the field maintains as
standing targets indicate where consensus believes the hard problems lie. This is a proxy, not a
measure — a benchmark can be hard for reasons of engineering rather than scientific depth.

## Conclusion
Cold-start latency, autoscaling maturity and energy-sustainable edge computing are the
best-corroborated demanding topics in this pool, each named as open across independent sources. The
computer-vision and model-limitation directions are suggestive but rest on fewer sources. What the
evidence cannot tell you is which topic a given researcher will find difficult; it can only show
where the field agrees the ground is unsettled.

## Visualizations
\`\`\`mermaid
flowchart TD
    A[Demanding-topic signal] --> B[Open-problem surveys]
    A --> C[Benchmark ceilings]
    A --> D[Grand challenges]
    B --> E[Best-corroborated: cold start, autoscaling, energy edge]
    C --> F[Model-limited AI]
    D --> G[Computer vision under clutter]

    classDef signal fill:#4A90D9,stroke:#2C5F8A,color:#fff
    classDef best fill:#2ECC71,stroke:#1F9D55,color:#fff
    classDef suggest fill:#F5A623,stroke:#B87A10,color:#fff
    class A signal
    class E best
    class F,G suggest
\`\`\`

## References
- Serverless cold-start latency: a systematic review, 2023. arXiv. [https://arxiv.org/abs/2301.00001](https://arxiv.org/abs/2301.00001)
- Self-aware and self-adaptive cloud autoscaling, 2016. arXiv. [https://arxiv.org/abs/2306.00002](https://arxiv.org/abs/2306.00002)
- Limits of scale in reasoning benchmarks, 2023. arXiv. [https://arxiv.org/abs/2312.00003](https://arxiv.org/abs/2312.00003)`,
        confidence: 0.62,
        degraded: [],
        degraded_reasons: {},
        provider_degraded: false,
        provider_kinds: [],
        answer_support: null,
        wave_report: [],
        // {} in practice — the composer only fills summary.* when resolvable.
        citation_health: {},
        // Real quality shape (quality/metrics.py::to_dict).
        quality: {
          findings: [],
          serious_count: 0,
          temporal: { oldest: null, newest: null, median_age_days: null, dated: 0, undated: 18, date_coverage: 0.0, time_sensitive: false, stale: false },
          independence: { nominal_sources: 12, effective_sources: 5, echo_groups: 3, echoed_claims: 7, dominant_domain: "arxiv.org", dominant_share: 0.42 },
          section_coverage: {},
          is_clean: true,
        },
        // FLAT signals dict, matching compute_audit's confidence_breakdown.
        confidence_breakdown: {
          source_quality: 0.88,
          source_diversity: 0.4,
          citation_coverage: 1.0,
          claim_verification_strength: 0.0,
          cross_source_agreement: 0.0,
          freshness: 0.0,
          critic_survival: 0.6,
          claim_evidence_quality: 0.75,
        },
        contradictions: [],
        source_count: 31,
      }, 700),
    ],
  },

  /* ------------------------------------------------------------------ *
   * 2) Factual/definitional — high confidence, clean, corroborated.
   *    Mirrors the real recorded RAG template (backend/research.db).
   * ------------------------------------------------------------------ */
  {
    query: "What is retrieval augmented generation?",
    mode: "quick",
    confidence: 0.74,
    sourceCount: 44,
    frames: [
      progress("Query received", 250),
      ...node("pathfinder"),
      progress(
        "Browsing the web to learn more about the task: What is retrieval augmented generation?",
        700,
      ),
      progress("Planning the research strategy and subtasks...", 600),
      progress("Gathered ~18,730 characters of initial research", 800),
      ...node("architect"),
      plan([
        "Origins and Foundational Framework of Retrieval-Augmented Generation",
        "Core Architecture and Limitations of Static RAG Systems",
        "Dynamic and Active RAG: FLARE, DRAGIN, and the Evolution of Retrieval Timing",
        "Domain-Specific Applications of RAG in Knowledge-Intensive Fields",
        "Technical Innovations and Future Directions in Retrieval Quality",
      ], 800),
      ...node("review_gate"),
      progress("Plan accepted: 5 distinct sections.", 450),
      ...node("scout_swarm"),
      progress(
        "Researching 5 sections in parallel:\n"
          + "- Origins and Foundational Framework of Retrieval-Augmented Generation\n"
          + "- Core Architecture and Limitations of Static RAG Systems\n"
          + "- Dynamic and Active RAG: FLARE, DRAGIN, and the Evolution of Retrieval Timing\n"
          + "- Domain-Specific Applications of RAG in Knowledge-Intensive Fields\n"
          + "- Technical Innovations and Future Directions in Retrieval Quality",
        1300,
      ),
      progress("Running in depth research on the following report topic: Origins and Foundational Framework of Retrieval-Augmented Generation", 800),
      progress("Browsing the web to learn more about the task: Origins and Foundational Framework of Retrieval-Augmented Generation...", 700),
      progress("Running in depth research on the following report topic: Dynamic and Active RAG: FLARE, DRAGIN, and the Evolution of Retrieval Timing", 700),
      progress("Browsing the web to learn more about the task: Dynamic and Active RAG: FLARE, DRAGIN, and the Evolution of Retrieval Timing...", 700),
      progress("Planning the research strategy and subtasks...", 600),
      progress("Researched 5 sections", 800),
      ...node("narrative_writer"),
      progress("Writing final research report based on research data...", 1000),
      ...node("evidence_auditor"),
      progress("Checking facts in the assembled draft...", 900),
      ...node("diagram_designer"),
      progress("Generating visualizations for the research report...", 700),
      ...node("report_assembler"),
      progress("Publishing final research report based on retrieved data...", 700),
      final({
        report:
`# Retrieval-Augmented Generation: A Comprehensive Analysis of the Paradigm Reshaping Large Language Models

#### Date: 2026-11-10

## Introduction
Large Language Models (LLMs) have demonstrated remarkable capabilities in natural language
understanding and generation, yet they remain fundamentally constrained by their static parametric
knowledge—a limitation that has driven the development of Retrieval-Augmented Generation (RAG) as a
transformative paradigm. The foundational RAG framework, introduced by Lewis et al. in 2020,
established the principle of combining a parametric memory (the LLM) with a non-parametric memory (a
retrieval corpus), enabling models to access external information without retraining
([arXiv](https://arxiv.org/abs/2005.11401)). This hybrid approach mitigates key LLM limitations,
including hallucination, knowledge cutoff, and the inability to cite sources—issues that become
critical in high-stakes domains such as healthcare, law, and scientific research.

Despite its promise, early 'static' RAG systems—which retrieve documents once before generation—
exhibit significant limitations, including rigid retrieval timing and vulnerability to irrelevant or
noisy retrieved content ([arXiv](https://arxiv.org/abs/2305.06983)). Such constraints prompted the
development of dynamic and active RAG paradigms, where retrieval is interleaved with generation.

## Table of Contents
- Origins and Foundational Framework of Retrieval-Augmented Generation
- Core Architecture and Limitations of Static RAG Systems
- Dynamic and Active RAG: FLARE, DRAGIN, and the Evolution of Retrieval Timing
- Domain-Specific Applications of RAG in Knowledge-Intensive Fields
- Technical Innovations and Future Directions in Retrieval Quality

## Origins and Foundational Framework of Retrieval-Augmented Generation
RAG was introduced by Lewis et al. (2020) as a general-purpose fine-tuning recipe that pairs a
pre-trained seq2seq model with a dense vector index of Wikipedia accessed through a neural
retriever. The retriever and generator are trained jointly, so the model learns to condition its
output on retrieved passages rather than memory alone ([arXiv](https://arxiv.org/abs/2005.11401)).

## Core Architecture and Limitations of Static RAG Systems
A static RAG pipeline indexes documents into chunks, retrieves the most relevant chunks for a
question, augments the prompt with them, and generates an answer. Its limitations follow from doing
this once: if the first retrieval misses, generation has no recourse
([arXiv](https://arxiv.org/abs/2305.06983)).

## Dynamic and Active RAG: FLARE, DRAGIN, and the Evolution of Retrieval Timing
Active RAG interleaves retrieval with generation. FLARE retrieves when the model's own confidence
drops, and DRAGIN decides *when* and *what* to retrieve from the model's information needs. Both
improve factual accuracy where a single retrieval is insufficient
([arXiv](https://arxiv.org/abs/2403.10081)).

## Domain-Specific Applications of RAG in Knowledge-Intensive Fields
RAG is used in healthcare, law and scientific research—settings where an answer must be grounded in
a specific, citable corpus and where an unsupported claim is costly
([arXiv](https://arxiv.org/abs/2005.11401)).

## Technical Innovations and Future Directions in Retrieval Quality
Learned retrieval policies and adaptive indexing promise further gains, but raise questions about
scalability, interpretability and ethical use ([arXiv](https://arxiv.org/abs/2403.10081)).

## Conclusion
The evolution from static to dynamic retrieval addresses critical inefficiencies in retrieval timing
and relevance. These innovations signal a shift toward LLMs that actively seek information. However,
challenges persist, including more efficient retrieval, better handling of noisy or conflicting
sources, and multimodal integration ([arXiv](https://arxiv.org/abs/2005.11401)).

## Visualizations
\`\`\`mermaid
flowchart TD
    A[Origins and Foundational Framework of RAG] --> B[Core Architecture and Limitations of Static RAG]
    B --> C[Dynamic and Active RAG: FLARE, DRAGIN & the Evolution of Retrieval Timing]
    C --> D[Domain-Specific Applications of RAG in Knowledge-Intensive Fields]
    C --> E[Technical Innovations and Future Directions in Retrieval Quality]

    classDef foundation fill:#4A90D9,stroke:#2C5F8A,color:#fff
    classDef thestatic fill:#F5A623,stroke:#B87A10,color:#fff
    classDef dynamic fill:#7B61FF,stroke:#5A3FD4,color:#fff
    classDef domain fill:#2ECC71,stroke:#1F9D55,color:#fff
    classDef future fill:#E74C3C,stroke:#B03A2E,color:#fff
    class A foundation
    class B thestatic
    class C dynamic
    class D domain
    class E future
\`\`\`

## References
- Lewis, P., et al. (2020). Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks. arXiv. [https://arxiv.org/abs/2005.11401](https://arxiv.org/abs/2005.11401)
- Jiang, Z., et al. (2023). Active Retrieval Augmented Generation. arXiv. [https://arxiv.org/abs/2305.06983](https://arxiv.org/abs/2305.06983)
- Su, W., et al. (2024). DRAGIN: Dynamic Retrieval Augmented Generation based on the Information Needs of Large Language Models. arXiv. [https://arxiv.org/abs/2403.10081](https://arxiv.org/abs/2403.10081)`,
        confidence: 0.74,
        degraded: [],
        degraded_reasons: {},
        provider_degraded: false,
        provider_kinds: [],
        answer_support: null,
        wave_report: [],
        citation_health: {},
        quality: {
          findings: [],
          serious_count: 0,
          temporal: { oldest: null, newest: null, median_age_days: null, dated: 0, undated: 21, date_coverage: 0.0, time_sensitive: false, stale: false },
          independence: { nominal_sources: 1, effective_sources: 1, echo_groups: 0, echoed_claims: 0, dominant_domain: "arxiv.org", dominant_share: 1.0 },
          section_coverage: {},
          is_clean: true,
        },
        confidence_breakdown: {
          source_quality: 0.88,
          source_diversity: 0.0,
          citation_coverage: 1.0,
          claim_verification_strength: 0.0,
          cross_source_agreement: 0.0,
          freshness: 0.0,
          critic_survival: 0.6,
          claim_evidence_quality: 0.75,
        },
        contradictions: [],
        source_count: 44,
      }, 700),
    ],
  },

  /* ------------------------------------------------------------------ *
   * 3) Comparative/economics — a real revision loop (critic fires, the
   *    narrative_writer re-runs, critic fires again) and a low, honest
   *    confidence. Exercises the contradiction path in the panel.
   * ------------------------------------------------------------------ */
  {
    query: "Compare the cost and accuracy of large language models for enterprise analytics.",
    mode: "deep",
    confidence: 0.52,
    sourceCount: 56,
    frames: [
      progress("Query received", 250),
      ...node("pathfinder"),
      progress(
        "Browsing the web to learn more about the task: How do large language models compare on "
          + "cost and accuracy for enterprise analytics workloads?",
        800,
      ),
      progress("Planning the research strategy and subtasks...", 650),
      progress("Gathered ~26,900 characters of initial research", 850),
      ...node("architect"),
      plan([
        "Cost structure of serving frontier models at enterprise scale",
        "Accuracy on analytics-specific benchmarks",
        "The cost-accuracy frontier and where it is stable",
        "Operational factors: latency, rate limits and attribution",
        "What the cost estimates agree and disagree on",
      ], 850),
      ...node("review_gate"),
      progress("Plan accepted: 5 distinct sections.", 450),
      ...node("scout_swarm"),
      progress(
        "Researching 5 sections in parallel:\n"
          + "- Cost structure of serving frontier models at enterprise scale\n"
          + "- Accuracy on analytics-specific benchmarks\n"
          + "- The cost-accuracy frontier and where it is stable\n"
          + "- Operational factors: latency, rate limits and attribution\n"
          + "- What the cost estimates agree and disagree on",
        1300,
      ),
      progress("Running in depth research on the following report topic: Cost structure of serving frontier models at enterprise scale", 800),
      progress("Browsing the web to learn more about the task: Cost structure of serving frontier models at enterprise scale...", 750),
      progress("Browsing the web to learn more about the task: Accuracy on analytics-specific benchmarks...", 750),
      progress("Planning the research strategy and subtasks...", 600),
      progress("Researched 5 sections", 850),
      ...node("narrative_writer"),
      progress("Writing final research report based on research data...", 1000),
      ...node("evidence_auditor"),
      progress("Checking facts in the assembled draft...", 950),
      // Real revision loop: auditor rejects, writer re-runs, auditor re-checks.
      critic(
        "Several cost figures are attributed to vendor pages that report list prices, not the "
          + "discounted enterprise rates the draft implies; one $1,700/month figure is from a single "
          + "cloud price page and should not be generalised. Two sources disagree on throughput per "
          + "GPU by more than 2x. Please re-attribute or drop the list-price figures and surface the "
          + "throughput disagreement rather than averaging it.",
        1100,
      ),
      stage("narrative_writer", "running", STAGE_LABELS.narrative_writer, 220),
      progress("Writing final research report based on research data...", 900),
      stage("narrative_writer", "done", STAGE_LABELS.narrative_writer, 220),
      stage("evidence_auditor", "running", STAGE_LABELS.evidence_auditor, 220),
      progress("Checking facts in the assembled draft...", 850),
      critic(
        "The re-draft now attributes the price figures to their list-price sources and states the "
          + "throughput disagreement explicitly, so the earlier objections are resolved. Remaining "
          + "gap: no primary benchmark compares the two models on the specific analytics tasks the "
          + "query names, so accuracy claims stay directional.",
        1000,
      ),
      stage("evidence_auditor", "done", STAGE_LABELS.evidence_auditor, 220),
      ...node("diagram_designer"),
      progress("Generating visualizations for the research report...", 800),
      ...node("report_assembler"),
      progress("Publishing final research report based on retrieved data...", 750),
      final({
        report:
`# Cost and Accuracy of Large Language Models for Enterprise Analytics: A Comparative Reading

#### Date: 2026-10-28

## Introduction
Cost and accuracy trade off, but not along one curve: the two are governed by different choices, and
the published estimates disagree on the magnitude of the cost gap. This report compares the
available evidence and flags where it is contested rather than averaging it away
([MongoDB Atlas pricing](https://example.org/atlas-pricing)).

## Table of Contents
- Cost structure of serving frontier models at enterprise scale
- Accuracy on analytics-specific benchmarks
- The cost-accuracy frontier and where it is stable
- Operational factors: latency, rate limits and attribution

## Cost structure of serving frontier models at enterprise scale
Enterprise cost is driven by token throughput, reserved capacity and the discount structure of the
contract, not the public list price. A dedicated cluster tier was priced at approximately $1,700 per
month as of 2026 on a public price page ([MongoDB Atlas pricing](https://example.org/atlas-pricing));
that is a list price and should not be read as a negotiated enterprise rate.

## Accuracy on analytics-specific benchmarks
General reasoning benchmarks do not predict performance on analytics tasks that require reading a
schema and computing over it ([Analytics benchmark](https://example.org/analytics-bench)). No primary
benchmark in the retrieved pool compares the candidate models on those tasks specifically, so the
accuracy comparison here is directional, not measured.

## The cost-accuracy frontier and where it is stable
The frontier is stable in one region: for high-volume, low-complexity queries, smaller models do the
task at a fraction of the cost with little measured accuracy loss. Outside that region the two
sources in the pool disagree on throughput per GPU by more than 2x
([Vendor benchmark](https://example.org/vendor-bench)).

## Operational factors: latency, rate limits and attribution
Rate limits and the ability to attribute an answer to a source often decide adoption more than raw
cost or accuracy. Both are contract terms rather than model properties
([Vendor benchmark](https://example.org/vendor-bench)).

## Conclusion
Cost is governed by contract and throughput; accuracy on analytics tasks is not yet measured by a
primary benchmark in this pool. Read the cost figures as list prices and the accuracy claims as
directional. What the evidence cannot settle is the exact cost gap, which the sources dispute.

## Visualizations
\`\`\`mermaid
flowchart LR
    A[High volume, low complexity] --> B[Small model: low cost, little accuracy loss]
    C[Low volume, high complexity] --> D[Frontier model: higher cost, contested throughput]
    B --> E[Stable region of the frontier]
    D --> F[Contested region: sources disagree 2x+]

    classDef stable fill:#2ECC71,stroke:#1F9D55,color:#fff
    classDef contested fill:#E74C3C,stroke:#B03A2E,color:#fff
    class E stable
    class F contested
\`\`\`

## References
- MongoDB Atlas pricing, 2026. [https://example.org/atlas-pricing](https://example.org/atlas-pricing)
- Analytics-specific benchmark, 2026. [https://example.org/analytics-bench](https://example.org/analytics-bench)
- Vendor throughput benchmark, 2026. [https://example.org/vendor-bench](https://example.org/vendor-bench)`,
        confidence: 0.52,
        degraded: [],
        degraded_reasons: {},
        provider_degraded: false,
        provider_kinds: [],
        answer_support: null,
        wave_report: [],
        citation_health: {},
        quality: {
          findings: [
            {
              kind: "absolute_claim",
              detail: "uses absolute or proof language, which research evidence rarely supports; state the strength of support instead",
              sentence: "This report therefore proves the frontier model is always more accurate.",
            },
          ],
          serious_count: 0,
          temporal: { oldest: null, newest: null, median_age_days: null, dated: 0, undated: 16, date_coverage: 0.0, time_sensitive: false, stale: false },
          independence: { nominal_sources: 14, effective_sources: 6, echo_groups: 4, echoed_claims: 9, dominant_domain: "example.org", dominant_share: 0.5 },
          section_coverage: {},
          is_clean: false,
        },
        confidence_breakdown: {
          source_quality: 0.71,
          source_diversity: 0.43,
          citation_coverage: 1.0,
          claim_verification_strength: 0.0,
          cross_source_agreement: 0.0,
          freshness: 0.64,
          critic_survival: 0.5,
          claim_evidence_quality: 0.6,
        },
        contradictions: [
          {
            claim_a: "Throughput per GPU is X for the frontier model.",
            source_a: "https://example.org/vendor-bench",
            claim_b: "Throughput per GPU is more than 2x lower than the vendor reports.",
            source_b: "https://example.org/analytics-bench",
          },
        ],
        source_count: 56,
      }, 700),
    ],
  },

  /* ------------------------------------------------------------------ *
   * 4) A "current trend" query with a DEGRADED run — exercises the degraded
   *    banner and the confidence cap. Matches a real recorded degraded run
   *    (confidence capped at DEGRADED_CAP = 0.55).
   * ------------------------------------------------------------------ */
  {
    query: "What is the current trend of AI?",
    mode: "standard",
    confidence: 0.55,
    sourceCount: 38,
    frames: [
      progress("Query received", 250),
      ...node("pathfinder"),
      progress(
        "Browsing the web to learn more about the task: What is the current trend of AI?",
        700,
      ),
      progress("Planning the research strategy and subtasks...", 600),
      progress("Gathered ~19,420 characters of initial research", 800),
      ...node("architect"),
      plan([
        "Capability trajectory: where benchmarks have stopped improving",
        "Capital and infrastructure concentration",
        "Deployment outrunning reliability and governance",
        "Labour-market and economic signals",
      ], 800),
      ...node("review_gate"),
      progress("Plan accepted: 4 distinct sections.", 450),
      ...node("scout_swarm"),
      progress(
        "Researching 4 sections in parallel:\n"
          + "- Capability trajectory: where benchmarks have stopped improving\n"
          + "- Capital and infrastructure concentration\n"
          + "- Deployment outrunning reliability and governance\n"
          + "- Labour-market and economic signals",
        1200,
      ),
      progress("Running in depth research on the following report topic: Capability trajectory: where benchmarks have stopped improving", 800),
      progress("Browsing the web to learn more about the task: Capability trajectory: where benchmarks have stopped improving...", 750),
      progress("Planning the research strategy and subtasks...", 600),
      progress("Researched 4 sections", 800),
      ...node("narrative_writer"),
      progress("Writing final research report based on research data...", 950),
      ...node("evidence_auditor"),
      progress("Checking facts in the assembled draft...", 900),
      critic(
        "The evidence is heavily skewed toward narrow technical sub-domains and a single firm survey, "
          + "so the original broad question is only partially answered. Weakest assumption: that "
          + "benchmark and bibliometric findings represent the overall AI trend rather than a "
          + "researcher-biased slice. Missing counter-evidence: no coverage of AI regulation or "
          + "geographic trends beyond one country.",
        1000,
      ),
      ...node("diagram_designer"),
      progress("Generating visualizations for the research report...", 750),
      ...node("report_assembler"),
      progress("Publishing final research report based on retrieved data...", 700),
      final({
        report:
`# The Current Trend of AI: Progress Is Not Tracking the Headlines

#### Date: 2026-11-02

## Introduction
The retrieved evidence does not support a single "trend of AI." It supports three narrower
observations, each on thinner evidence than the question implies, and the summariser degraded on part
of this run — so the findings below should be read as directional
([arXiv](https://arxiv.org/abs/2405.20362v1)).

## Table of Contents
- Capability trajectory: where benchmarks have stopped improving
- Capital and infrastructure concentration
- Deployment outrunning reliability and governance
- Labour-market and economic signals

## Capability trajectory: where benchmarks have stopped improving
On a specific family of reasoning benchmarks, measured progress has stalled, and the stagnation
appears to be an artifact of benchmark choice as much as a genuine macro trend
([arXiv](https://arxiv.org/abs/2405.20362v1)). This is a narrow slice of AI, not AI as a whole.

## Capital and infrastructure concentration
The evidence retrieved for this section is dominated by a small number of large firms, which biases
any "industry trend" claim toward their decisions.

## Deployment outrunning reliability and governance
Deployment is proceeding faster than measured reliability and the governance frameworks meant to
constrain it, on the evidence available here.

## Labour-market and economic signals
Only one firm survey in the pool speaks to labour-market effects, so this section rests on a single
source.

## Conclusion
The defensible claim is narrow: on some benchmarks progress has slowed, an observation that is
partly a measurement artifact. The broader question the query asks is not answered by this evidence,
and the run degraded, so confidence is capped accordingly.

## Visualizations
\`\`\`mermaid
flowchart TD
    A[Query: current trend of AI] --> B[Capability trajectory]
    A --> C[Capital concentration]
    A --> D[Deployment vs reliability]
    A --> E[Labour market]
    B -->|narrow, benchmark-dependent| F[Findings]
    C -->|firm-biased| F
    D -->|directional| F
    E -->|single source| F
    classDef weak fill:#E74C3C,stroke:#B03A2E,color:#fff
    class E weak
\`\`\`

## References
- Does source matter? Benchmark stagnation and trend measurement, 2026. arXiv. [https://arxiv.org/abs/2405.20362v1](https://arxiv.org/abs/2405.20362v1)`,
        confidence: 0.55,
        degraded: ["summarizer"],
        degraded_reasons: { summarizer: "weak-evidence" },
        provider_degraded: false,
        provider_kinds: [],
        answer_support: null,
        wave_report: [],
        citation_health: {},
        quality: {
          findings: [],
          serious_count: 0,
          temporal: { oldest: null, newest: null, median_age_days: null, dated: 0, undated: 22, date_coverage: 0.0, time_sensitive: true, stale: false },
          independence: { nominal_sources: 9, effective_sources: 4, echo_groups: 3, echoed_claims: 6, dominant_domain: "arxiv.org", dominant_share: 0.56 },
          section_coverage: {},
          is_clean: true,
        },
        confidence_breakdown: {
          source_quality: 0.8,
          source_diversity: 0.31,
          citation_coverage: 0.9,
          claim_verification_strength: 0.0,
          cross_source_agreement: 0.0,
          freshness: 0.72,
          critic_survival: 0.3,
          claim_evidence_quality: 0.55,
        },
        contradictions: [],
        source_count: 38,
      }, 700),
    ],
  },
];

/* Default question offered when the demo opens with no stored chat. */
export const DEFAULT_QUERY = CONVERSATIONS[0].query;

/* Starter questions the composer/landing can advertise. */
export const DEMO_STARTERS = CONVERSATIONS.map((c) => c.query);

/* Find the fixture matching a query, or null when the demo has no script. */
export function findConversation(query) {
  const q = String(query || "").trim().toLowerCase();
  return CONVERSATIONS.find((c) => c.query.trim().toLowerCase() === q) || null;
}
