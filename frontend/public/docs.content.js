/* ============================================================================
   DeepScout Documentation — content model.
   Every claim below is grounded in this repository's source, README.md,
   ARCHITECTURE.md, BENCHMARK_RESULTS.md and CHANGELOG.md.
   ========================================================================== */
(function () {
  "use strict";

  function icon(path, extra) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"' + (extra || "") + ">" + path + "</svg>";
  }

  var I = {
    overview: icon('<path d="M4 5h16M4 12h16M4 19h10"/>'),
    brain: icon('<path d="M12 5a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V8a3 3 0 0 0-3-3Z"/><path d="M9 9a3 3 0 0 1-3-3M15 15a3 3 0 0 0 3 3"/>'),
    flow: icon('<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.5 6H16a2 2 0 0 1 2 2v7.5M6 8.5V16a2 2 0 0 0 2 2h7.5"/>'),
    agents: icon('<circle cx="12" cy="8" r="3"/><path d="M5 20a7 7 0 0 1 14 0"/><path d="M19 8h2M3 8h2"/>'),
    shield: icon('<path d="M12 3 5 6v6c0 4.2 2.9 7.5 7 9 4.1-1.5 7-4.8 7-9V6l-7-3Z"/><path d="m9 12 2 2 4-4"/>'),
    gauge: icon('<path d="M12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z"/><path d="M12 12 15 8"/><path d="M4.5 19a9 9 0 1 1 15 0"/>'),
    quote: icon('<path d="M7 7h4v4H9a3 3 0 0 0-3 3V7ZM13 7h4v4h-2a3 3 0 0 0-3 3V7Z"/>'),
    layers: icon('<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/>'),
    control: icon('<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 9h8M8 13h5M8 17h3"/>'),
    api: icon('<path d="M8 8 4 12l4 4M16 8l4 4-4 4M13 5l-2 14"/>'),
    bolt: icon('<path d="M13 3 5 13h6l-1 8 8-10h-6l1-8Z"/>'),
    db: icon('<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/><path d="M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/>'),
    check: icon('<path d="m5 12 4.5 4.5L19 7"/>'),
    warn: icon('<path d="M12 4 3 19h18L12 4Z"/><path d="M12 10v4M12 17h.01"/>'),
    info: icon('<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>'),
    copy: icon('<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>'),
    target: icon('<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3.4"/>'),
    search: icon('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.2-3.2"/>'),
    book: icon('<path d="M5 4h11a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2V4Z"/><path d="M5 18a2 2 0 0 1 2-2h11"/>'),
    scale: icon('<path d="M12 4v16M7 8l-3 6h6L7 8ZM17 8l-3 6h6l-3-6Z"/><path d="M6 4h12"/>'),
    arrow: icon('<path d="M12 5v14M6 13l6 6 6-6"/>')
  };

  var NAV = [
    { group: "Getting Started", items: [
      { id: "what-is-deepscout", title: "Introduction", icon: I.overview },
      { id: "capabilities", title: "Capabilities", icon: I.layers },
      { id: "quickstart", title: "Quick Start", icon: I.bolt },
      { id: "modes", title: "Research Modes", icon: I.control }
    ]},
    { group: "Architecture", items: [
      { id: "architecture", title: "System Architecture", icon: I.flow },
      { id: "workflow", title: "Research Workflow", icon: I.target },
      { id: "orchestration", title: "Orchestration & Waves", icon: I.brain },
      { id: "streaming", title: "Streaming Events", icon: I.api }
    ]},
    { group: "Agents", items: [
      { id: "agents", title: "Agent Roster", icon: I.agents },
      { id: "search", title: "Search & Retrieval", icon: I.search },
      { id: "section-research", title: "Section Research", icon: I.book }
    ]},
    { group: "Evidence & Verification", items: [
      { id: "verification", title: "Claim Verification", icon: I.shield },
      { id: "contradictions", title: "Contradictions", icon: I.scale },
      { id: "citations", title: "Citations & Health", icon: I.quote },
      { id: "confidence", title: "Confidence Engine", icon: I.gauge }
    ]},
    { group: "Operations", items: [
      { id: "budget", title: "Run Limits", icon: I.gauge },
      { id: "stopping", title: "Depth & Stopping", icon: I.target },
      { id: "reliability", title: "Reliability", icon: I.warn },
      { id: "persistence", title: "Persistence & Trace", icon: I.db },
      { id: "api", title: "API Reference", icon: I.api },
      { id: "benchmarks", title: "Benchmarks", icon: I.check }
    ]}
  ];

  var C = {};
  var ARROW = '<div class="pipe-arrow">' + I.arrow + "</div>";

  /* Code-block helpers: keep shell content readable without quote escaping. */
  var COPY_BTN = '<button class="copy-btn" type="button">' + I.copy + " Copy</button>";
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function shBlock(rows) {
    var body = rows.join("\n");
    return (
      '<div class="code-block term" data-lang="bash">' +
      '<div class="code-head"><span class="lang">' +
      '<span class="term-dots"><i></i><i></i><i></i></span> bash</span>' +
      COPY_BTN +
      "</div><pre><code>" + body + "</code></pre></div>"
    );
  }
  /* t() = plain shell text (HTML-escaped); c()/g()/y() = coloured tokens. */
  function t(text) { return esc(text); }
  function c(comment) { return '<span class="tok-c">' + esc(comment) + "</span>"; }
  function g(text) { return '<span class="tok-g">' + esc(text) + "</span>"; }
  function y(text) { return '<span class="tok-y">' + esc(text) + "</span>"; }

  /* ============================ Introduction ============================ */
  C["what-is-deepscout"] = [
    '<div class="hero">',
      '<p class="eyebrow">Multi-Agent Research System · v2.1</p>',
      '<h1>Research beyond the obvious.</h1>',
      '<p class="lede">DeepScout is an autonomous research intelligence platform. It decomposes a complex question, plans a research strategy, dispatches specialized agents in parallel, verifies every claim against its cited source, challenges its own conclusions, and delivers a cited report with an honest, evidence-derived confidence score.</p>',
      '<div class="hero-cta">',
        '<a class="btn primary" href="#quickstart">', I.bolt, " Quick start</a>",
        '<a class="btn" href="#architecture">', I.flow, " Architecture</a>",
        '<a class="btn" href="#benchmarks">', I.check, " Benchmarks</a>",
      "</div>",
      '<div class="hero-stats">',
        '<div class="hero-stat"><div class="v">1.00</div><div class="k">Verification F1 · 16 labeled cases</div></div>',
        '<div class="hero-stat"><div class="v">0%</div><div class="k">Hallucination leak · 6/6 rejected</div></div>',
        '<div class="hero-stat"><div class="v">1.00</div><div class="k">Contradiction detection F1</div></div>',
        '<div class="hero-stat"><div class="v">577</div><div class="k">Offline tests passing</div></div>',
      "</div>",
    "</div>",

    "<h2 id=\"what-is-deepscout\">What DeepScout is</h2>",
    "<p>DeepScout is not a chatbot wrapped around a search API. It behaves like a small research team assembled for one question: it researches the question first, plans a report outline from what it found, then researches each section of that outline — searching, reading, and citing real sources — before assembling and delivering a written report.</p>",
    "<p>The pipeline is a <strong>LangGraph state machine</strong> driving bounded, asynchronous agents. Generation runs on remote LLM APIs — Groq primary, HuggingFace fallback, plus any OpenAI-compatible endpoint you add. Every run persists its full timeline to SQLite, so it can be replayed and audited.</p>",

    '<div class="callout">',
      '<span class="c-ico">', I.info, "</span>",
      '<div class="c-body"><div class="c-title">Built for modest hardware</div>',
      "<p>DeepScout targets an 8&nbsp;GB RAM host with no GPU and no local model inference. Concurrency is bounded everywhere — <code>MAX_PARALLEL_SEARCH=3</code>, <code>MAX_PARALLEL_LLM=4</code>, <code>MAX_PARALLEL_AGENTS=3</code> — and raw page content is released from memory after verification. These are real constraints, not aspirations.</p></div>",
    "</div>",

    "<h2 id=\"design-philosophy\">Design philosophy</h2>",
    "<p>Four principles run through every module:</p>",
    '<div class="grid cols-2">',
      '<div class="feature"><div class="f-ico">', I.shield, "</div><h4>Verification is deterministic</h4><p>A verifier that costs a model call is a verifier you consult less often. Lexical, numeric and polarity checks run in roughly 153&nbsp;µs per claim and catch fabricated numbers, negation inversions and topical mismatches.</p></div>",
      '<div class="feature"><div class="f-ico">', I.scale, "</div><h4>Disagreement is intelligence</h4><p>Contradictions are surfaced and penalized, not blended into an average. A weighted mean lets three strong sources mask one direct lie.</p></div>",
      '<div class="feature"><div class="f-ico">', I.gauge, "</div><h4>Uncertainty stays visible</h4><p>Confidence is computed from measured evidence signals — source quality, diversity, citation coverage, verification strength, critic survival. Degraded runs are capped below the sufficiency threshold so fallbacks can never read as high confidence.</p></div>",
      '<div class="feature"><div class="f-ico">', I.warn, "</div><h4>Every failure degrades gracefully</h4><p>Each LLM-calling agent has a deterministic fallback. Provider outages, quota walls and timeouts produce disclosed degradation, not silent wrong answers.</p></div>",
    "</div>"
  ];

  /* ============================ Capabilities ============================ */
  C.capabilities = [
    '<p class="eyebrow">Capabilities</p>',
    '<h2 id="capabilities">What separates DeepScout</h2>',
    '<p class="lede">Each capability below maps to a specific module, threshold and measured result — not a marketing bullet.</p>',

    '<h3 id="cap-intent">Understand before searching</h3>',
    "<p>Before any research is shaped, an <strong>intent classifier</strong> resolves what the question actually means. It ranks competing senses for ambiguous terms, sets the research domain and explanation level, and never silently picks the wrong meaning. The canonical failure it prevents: a question about the ML <em>transformer</em> dragging in electrical-transformer statistics.</p>",
    '<div class="code-block" data-lang="intent.json"><div class="code-head"><span class="lang">intent · resolved senses</span><button class="copy-btn" type="button">', I.copy, " Copy</button></div><pre><code>{\n  \"query\": \"What is a transformer?\",\n  \"domain\": \"machine_learning\",\n  \"explanation_level\": \"practical\",\n  \"ambiguity\": true,\n  \"senses\": [\n    { \"label\": \"ML architecture\",   \"probability\": 0.75 },\n    { \"label\": \"electrical device\", \"probability\": 0.20 }\n  ],\n  \"recommended_action\": \"research_both\"\n}</code></pre></div>",
    '<p class="small muted">When the top-two sense gap falls below <code>RESEARCH_BOTH_GAP = 0.30</code>, both senses are researched and kept separate end-to-end. Definitional queries on ambiguous terms force both. A curated homonym table — transformer, python, apple, amazon, jaguar, eclipse, windows, shell — provides a deterministic fallback when the model is unavailable.</p>',

    '<h3 id="cap-verification">Verify every claim</h3>',
    "<p>Each extracted claim is checked against the source text it cites by a deterministic verifier — no LLM, so it can run over every claim on every pass. Hard checks include weighted lexical overlap, source authority, unit-aware numeric grounding with 2% tolerance, polarity consistency, direct-quote location and freshness.</p>",

    '<h3 id="cap-citations">Re-check citations live</h3>',
    "<p>At delivery the answer’s citation legend is re-validated: each cited URL is probed live (<code>HEAD</code>, then a 2&nbsp;KB ranged <code>GET</code>), and that health signal is fused with sentence-support evidence into per-source verdicts.</p>",

    '<h3 id="cap-contradictions">Surface contradictions</h3>',
    "<p>Three detectors — polarity, temporal and unit-aware numeric — find conflicting evidence, rank it by severity, and feed resolution follow-ups back into the research loop.</p>",

    '<h3 id="cap-confidence">Compute confidence from evidence</h3>',
    "<p>The confidence engine blends nine evidence signals and subtracts a pool-size-scaled contradiction penalty. It is not a model’s self-report.</p>",

    '<h3 id="cap-cost">Account for every token</h3>',
    "<p>A per-run ledger records tokens, USD, LLM calls, searches and cache hits. A budget governor refuses research passes that cannot be paid for, and the live meter streams to the UI.</p>",

    '<div class="grid cols-3" style="margin-top:26px">',
      '<div class="metric-card"><div class="m-val acc">F1 1.00</div><div class="m-lab">Verification accuracy</div><div class="m-sub">16 labeled claim/source cases</div></div>',
      '<div class="metric-card"><div class="m-val acc">0.000</div><div class="m-lab">Hallucination leak rate</div><div class="m-sub">6/6 fabricated claims rejected</div></div>',
      '<div class="metric-card"><div class="m-val acc">100%</div><div class="m-lab">Citation sentence accuracy</div><div class="m-sub">numeric grounding 1.000</div></div>',
    "</div>"
  ];

  /* ============================ Quickstart ============================ */
  C.quickstart = [
    '<p class="eyebrow">Getting started</p>',
    '<h2 id="quickstart">Quick start</h2>',
    '<p class="lede">Two processes: a FastAPI backend and a Vite/React console. No API keys go in <code>.env</code> — you add your LLM provider in the app.</p>',

    '<h3 id="qs-setup">1 · Backend</h3>',
    shBlock([
      c("# from the repository root"),
      t("python3 -m venv .venv && source .venv/bin/activate"),
      t("pip install -r backend/requirements.txt"),
      t("cd backend"),
      g("../.venv/bin/python -m uvicorn") + t(" main:app --host 127.0.0.1 --port 8000")
    ]),

    '<h3 id="qs-frontend">2 · Frontend</h3>',
    shBlock([
      c("# in a second terminal"),
      t("cd frontend && npm install && npm run dev"),
      c("# open http://127.0.0.1:5173")
    ]),
    "<p>No provider configuration is required to start. Open <strong>Model Controls</strong> (<code>/#/model-controls</code>) and add any OpenAI-compatible provider — name, base URL, API key, model id — then select it. Keys are Fernet-encrypted at rest, a selected provider is exclusive, and it applies on the next call with no restart. Until then a run returns a clear \"no provider configured\" error instead of a degraded answer.</p>",
    "<p>Web search uses a local <a href=\"https://github.com/searxng/searxng\" target=\"_blank\" rel=\"noreferrer\">SearXNG</a> instance on <code>http://localhost:8080</code> — see <code>SETUP.md</code>. No search API key is needed; Wikipedia, arXiv and Crossref work even if SearXNG is down.</p>",

    '<h3 id="qs-health">3 · Smoke test</h3>',
    shBlock([
      g("curl") + t(" -s http://127.0.0.1:8000/api/health"),
      c('# {"status":"ok"}'),
      t(""),
      g("curl") + t(" -N -X POST http://127.0.0.1:8000/api/research/stream \\"),
      t("  -H ") + y('"Content-Type: application/json"') + t(" \\"),
      t("  -d ") + y("'{\"query\": \"What is retrieval augmented generation?\"}'")
    ]),

    '<h3 id="qs-tests">4 · Tests &amp; benchmarks</h3>',
    shBlock([
      g("pytest") + t(" tests/ -v                 ") + c("# 577 tests, fully offline"),
      g("python") + t(" bench/run_offline.py      ") + c("# deterministic, no network, no keys"),
      g("python") + t(" bench/live_retrieval_check.py") + c("# live-provider retrieval probe"),
      g("ruff") + t(" check app tests bench scripts"),
      g("mypy") + t(" app")
    ]),

    '<div class="callout info">',
      '<span class="c-ico">', I.info, "</span>",
      '<div class="c-body"><div class="c-title">Configuration is centralised</div>',
      "<p>Every setting lives in <code>app/core/config.py</code> and gets a placeholder in <code>backend/.env.example</code>. The full reference is in <code>CONFIG.md</code>.</p></div>",
    "</div>"
  ];

  /* ============================ Modes ============================ */
  C.modes = [
    '<p class="eyebrow">Getting started</p>',
    '<h2 id="modes">Research modes</h2>',
    "<p class=\"lede\">A mode is an explicit contract for how much research a run does: how many report sections the planner may propose, and how many times a review stage may send work back.</p>",
    '<div class="table-wrap"><table>',
      "<thead><tr><th>Mode</th><th class=\"num\">Report sections</th><th class=\"num\">Plan revisions</th><th class=\"num\">Draft revisions</th><th class=\"num\">Fact-check revisions</th></tr></thead>",
      "<tbody>",
        '<tr><td><strong>quick</strong></td><td class="num">3</td><td class="num">1</td><td class="num">1</td><td class="num">1</td></tr>',
        '<tr><td><strong>standard</strong> <span class="pill mint">default</span></td><td class="num">5</td><td class="num">2</td><td class="num">2</td><td class="num">1</td></tr>',
        '<tr><td><strong>deep</strong></td><td class="num">8</td><td class="num">2</td><td class="num">2</td><td class="num">1</td></tr>',
      "</tbody>",
    "</table></div>",
    '<p class="small muted">The mode sets <em>how much research happens</em>, not what the report looks like — every mode produces the same report shape (introduction, sections, conclusion, references). The section outline itself is derived from the question by the planner, so a mode with more sections researches the same subject more widely rather than changing the format. An unrecognised mode is not rejected; it falls back to <code>standard</code>.</p>',

    '<h3 id="modes-waves">How the sections are researched</h3>',
    "<p>Sections are researched in bounded waves of <code>MAX_PARALLEL_AGENTS</code> (default 3). Sections in the same wave cannot see each other's drafts, so each later wave is given the titles <em>and</em> the written drafts of the sections already completed. That is what lets a section be told not to restate a statistic or argument a sibling already made.</p>",
    '<p class="small muted">A smaller wave means tighter de-duplication and longer wall-clock time; a larger wave is faster but the first wave has more independently-written sections.</p>'
  ];

  /* ============================ Architecture ============================ */
  C.architecture = [
    '<p class="eyebrow">Architecture</p>',
    '<h2 id="architecture">System architecture</h2>',
    '<p class="lede">One LangGraph instance drives the research loop. Each node is a bounded, independently testable agent; shared intelligence lives in deterministic core modules.</p>',

    '<div class="pipeline">',
      '<div class="pipe-node"><div class="pn-ico">01</div><div class="pn-body"><div class="pn-title">Pathfinder</div><div class="pn-desc">Discovers relevant sources and maps the research landscape, seeding everything that follows.</div></div></div>', ARROW,
      '<div class="pipe-node"><div class="pn-ico">02</div><div class="pn-body"><div class="pn-title">Research Architect <code>app/research/agents/editor.py</code></div><div class="pn-desc">Designs the report outline from that first pass. The mode caps how many sections it may propose.</div></div></div>', ARROW,
      '<div class="pipe-node"><div class="pn-ico">03</div><div class="pn-body"><div class="pn-title">Scout Swarm <code>app/research/agents/research_unit.py</code></div><div class="pn-desc">Investigates each section in bounded waves. A wave is given the sections already written.</div></div></div>', ARROW,
      '<div class="pipe-node"><div class="pn-ico">04</div><div class="pn-body"><div class="pn-title">Search + context <code>app/research/retrieval/</code>, <code>app/research/context/</code></div><div class="pn-desc">SearXNG with fallbacks, then pages are relevance-gated and filtered to what the section actually needs.</div></div></div>', ARROW,
      '<div class="pipe-node"><div class="pn-ico">05</div><div class="pn-body"><div class="pn-title">Narrative Writer <code>app/research/skills/writer.py</code></div><div class="pn-desc">Each section is written with its citations, scoped to its own subtopic.</div></div></div>', ARROW,
      '<div class="pipe-node"><div class="pn-ico">06</div><div class="pn-body"><div class="pn-title">Review + revise</div><div class="pn-desc">A section can be sent back for revision against the task guidelines before it is accepted.</div></div></div>', ARROW,
      '<div class="pipe-node"><div class="pn-ico">07</div><div class="pn-body"><div class="pn-title">Evidence Auditor</div><div class="pn-desc">Checks factual claims, source support and inconsistencies, and may send the draft back to the writer once.</div></div></div>', ARROW,
      '<div class="pipe-node"><div class="pn-ico">08</div><div class="pn-body"><div class="pn-title">Report Assembler <code>app/research/agents/report_agents.py</code></div><div class="pn-desc">Assembles the markdown report: title, date, introduction, sections, conclusion, references.</div></div></div>', ARROW,
      '<div class="pipe-node"><div class="pn-ico">09</div><div class="pn-body"><div class="pn-title">Audit <code>app/engine/audit_bridge.py</code></div><div class="pn-desc">The fact pool is reconstructed from the report and its sources, then scored: confidence, contradictions, evidence grades, quality.</div></div></div>',
    "</div>",

    '<div class="callout info">',
      '<span class="c-ico">', I.flow, "</span>",
      '<div class="c-body"><div class="c-title">The answer and its audit are built separately</div>',
      "<p>The report a reader sees is assembled from the researched sections and carries no score, no confidence number and no process metadata. The audit runs afterwards, deterministically and without a model call, and ships beside it in the same stream frame. That is why an ungrounded answer cannot be dressed up as a researched one.</p></div>",
    "</div>",

    '<h3 id="arch-modules">Core intelligence modules</h3>',
    '<div class="table-wrap"><table>',
      "<thead><tr><th>Module</th><th>Responsibility</th></tr></thead><tbody>",
        "<tr><td><code>app/research/runner.py</code></td><td>Builds the run, compiles the graph, streams stage updates, assembles the final state.</td></tr>",
        "<tr><td><code>app/research/llm.py</code></td><td>The generation funnel. Every model call goes through the Model Control Center, so its provider, breakers, cache and ledger all apply.</td></tr>",
        "<tr><td><code>app/research/retrieval/base.py</code></td><td>Every retriever name resolves to one retriever backed by <code>SearchClient</code>.</td></tr>",
        "<tr><td><code>app/research/context/lexical.py</code></td><td>BM25 chunk selection \u2014 the dependency-free context filter.</td></tr>",
        "<tr><td><code>app/agents/search.py</code> + <code>searchkit/</code></td><td>Providers, circuit breakers, canonical-URL dedup, domain caps, freshness and page fetch.</td></tr>",
        "<tr><td><code>app/core/contradictions.py</code></td><td>Polarity, temporal and unit-aware numeric detectors; severity ordering.</td></tr>",
        "<tr><td><code>app/core/confidence.py</code></td><td>Multi-signal confidence with a contradiction penalty and a degraded-run cap.</td></tr>",
        "<tr><td><code>app/core/evidence_grade.py</code></td><td>Per-claim evidence grading into a distribution.</td></tr>",
        "<tr><td><code>app/core/semantic.py</code></td><td>CPU-light TF-IDF hybrid: stemming, synonym canonicalization, negation weighting.</td></tr>",
      "</tbody>",
    "</table></div>",

    '<h3 id="arch-lifecycles">Data lifecycles</h3>',
    "<ul>",
      "<li><strong>Raw page content</strong> is blanked by the verifier after each pass — verification is the last consumer of full text. Snippets persist for transparency.</li>",
      "<li><strong>Facts</strong> accumulate across passes with append-only plan growth; semantic dedup merges restatements and records corroboration from distinct domains only.</li>",
      "<li><strong>Caches:</strong> search results 1&nbsp;h, LLM responses 6&nbsp;h, both size-capped at 250&nbsp;MB and LRU-evicted.</li>",
      "<li><strong>SQLite:</strong> every run persists agent events, sources, claims, verification results, contradictions, citations, critic reviews and decisions — the trace endpoint reconstructs the full timeline.</li>",
    "</ul>"
  ];

  /* ============================ Workflow ============================ */
  C.workflow = [
    '<p class="eyebrow">Architecture</p>',
    '<h2 id="workflow">Research workflow</h2>',
    '<p class="lede">The lifecycle, from question to auditable answer. The stages are bounded: a run researches the outline it planned, once, and stops.</p>',
    '<div class="table-wrap"><table>',
      '<thead><tr><th class="num">#</th><th>Stage</th><th>What happens</th></tr></thead><tbody>',
        '<tr><td class="num">01</td><td><strong>Receive</strong></td><td>User submits a query and mode.</td></tr>',
        '<tr><td class="num">02</td><td><strong>Pathfinder</strong></td><td>Discovers sources and maps the landscape, grounding the outline.</td></tr>',
        '<tr><td class="num">03</td><td><strong>Research Architect</strong></td><td>Derives the section outline. The mode caps how many sections.</td></tr>',
        '<tr><td class="num">04</td><td><strong>Scout Swarm</strong></td><td>Per section: sub-queries are planned from the section title, then providers run under a bounded semaphore.</td></tr>',
        '<tr><td class="num">05</td><td><strong>Fetch + filter</strong></td><td>Pages are scraped, then relevance-gated against the section and reduced to the chunks that matter.</td></tr>',
        '<tr><td class="num">06</td><td><strong>Narrative Writer</strong></td><td>Cited prose, scoped to the section, with the already-written sections supplied to avoid overlap.</td></tr>',
        '<tr><td class="num">07</td><td><strong>Review + revise</strong></td><td>A bounded review loop can send the section back for revision.</td></tr>',
        '<tr><td class="num">08</td><td><strong>Assemble</strong></td><td>The Narrative Writer produces the introduction and conclusion around the researched sections.</td></tr>',
        '<tr><td class="num">09</td><td><strong>Evidence Auditor</strong></td><td>The assembled draft is checked; it may be sent back once.</td></tr>',
        '<tr><td class="num">10</td><td><strong>Report Assembler</strong></td><td>The report is assembled as markdown with references.</td></tr>',
        '<tr><td class="num">11</td><td><strong>Audit</strong></td><td>The fact pool is reconstructed from the report and its sources, then scored.</td></tr>',
        '<tr><td class="num">12</td><td><strong>Deliver</strong></td><td>Report, sources, claims, contradictions and confidence are persisted and streamed.</td></tr>',
      "</tbody>",
    "</table></div>",

    '<div class="callout">',
      '<span class="c-ico">', I.search, "</span>",
      '<div class="c-body"><div class="c-title">Relevance is a gate, not a ranker</div>',
      "<p>Each sub-query can surface papers about something else entirely \u2014 a weather-forecasting benchmark in a report about hiring, say. Pages that engage nothing in the section\u2019s subject are dropped before the writer sees them, so a stray result cannot become a paragraph. The gate is deliberately a floor: it never empties a section\u2019s evidence, because a thin report is better than no report.</p></div>",
    "</div>"
  ];

  /* ============================ Orchestration ============================ */
  C.orchestration = [
    '<p class="eyebrow">Architecture</p>',
    '<h2 id="orchestration">Orchestration &amp; waves</h2>',
    '<p class="lede">The report is not written in one pass. Sections are researched independently, in waves, and the waves are ordered so that each can build on what earlier ones established rather than repeat them.</p>',

    '<h3 id="orch-waves">How waves work</h3>',
    "<p>At most <code>MAX_PARALLEL_AGENTS</code> sections are researched at a time. When a wave completes, its section titles <em>and</em> its written drafts become input to the next wave\u2019s writers:</p>",
    '<div class="code-block" data-lang="python"><div class="code-head"><span class="lang">what a later section is given</span><button class="copy-btn" type="button">', I.copy, " Copy</button></div><pre><code>existing_headers = [{&quot;subtopic task&quot;: &quot;Section A&quot;, &quot;note&quot;: &quot;covered by another section&quot;}]\nrelevant_written_contents = [{&quot;section_title&quot;: &quot;Section A&quot;, &quot;written_content&quot;: &quot;...the draft another section already wrote...&quot;}]</code></pre></div>",
    "<p>Those two inputs are what the section prompt\u2019s anti-duplication instructions were written against. Without them the instruction is a request the writer cannot satisfy; with them it is something it can check.</p>",

    '<div class="callout">',
      '<span class="c-ico">', I.brain, "</span>",
      '<div class="c-body"><div class="c-title">The tradeoff is deliberate</div>',
      "<p>A smaller wave means more overlap prevented and a longer run; a larger one is faster but the first wave always contains sections that cannot see each other. The default of 3 is a balance, not a guarantee \u2014 the first wave is the irreducible case.</p></div>",
    "</div>",

    '<h3 id="orch-review">Bounded review loops</h3>',
    "<p>Plan review and draft review may each send work back a fixed number of times, and the fact checker may revise once. Every loop has a ceiling: past it the graph force-accepts and moves on, so a stage that keeps finding fault cannot hold the request open. Each ceiling is reported in the stream rather than being invisible.</p>"
  ];

  /* ============================ Streaming ============================ */
  C.streaming = [
    '<p class="eyebrow">Architecture</p>',
    '<h2 id="streaming">Streaming events</h2>',
    '<p class="lede"><code>POST /api/research/stream</code> returns newline-delimited JSON (NDJSON). Each line is one event; the console renders them as a live research timeline.</p>',
    '<div class="code-block" data-lang="http"><div class="code-head"><span class="lang">http · request</span><button class="copy-btn" type="button">', I.copy, " Copy</button></div><pre><code>POST /api/research/stream\nContent-Type: application/json\n\n{ &quot;query&quot;: &quot;Should Bangladesh expand nuclear energy?&quot;, &quot;mode&quot;: &quot;deep&quot; }</code></pre></div>",

    '<h3 id="stream-schema">Event schema</h3>',
    '<div class="card" style="padding:8px 0">',
      '<div class="event-row"><div class="e-name">progress</div><div class="e-desc"><code>request_id</code>, <code>session_id</code>, <code>message</code> — run accepted, then a label per stage as the pipeline works.</div></div>',
      '<div class="event-row"><div class="e-name">plan</div><div class="e-desc"><code>items</code> — the planned report sections.</div></div>',
      '<div class="event-row"><div class="e-name">critic</div><div class="e-desc"><code>iteration</code>, <code>reason</code> — the fact checker outcome, and what it wants revised.</div></div>',
      '<div class="event-row"><div class="e-name">final_report</div><div class="e-desc"><code>report</code>, <code>confidence</code>, <code>confidence_breakdown</code>, <code>quality</code>, <code>contradictions</code>, <code>source_count</code>.</div></div>',
      '<div class="event-row"><div class="e-name">error</div><div class="e-desc"><code>message</code> — actionable cause (key, quota, timeout).</div></div>',
    "</div>",
    '<p class="small muted">Those five are the only types emitted. A stage the pipeline has no honest signal for is not invented; the frontend treats every event type as optional, so a run renders cleanly without one. A backend event type with no matching frontend case would be silently dropped, so the NDJSON contract and the UI are changed together.</p>'
  ];

  /* ============================ Agents ============================ */
  C.agents = [
    '<p class="eyebrow">Agents</p>',
    '<h2 id="agents">Agent roster</h2>',
    '<p class="lede">DeepScout operates as a temporary research organization. Each agent has one job, a deterministic fallback, and a specific module.</p>',
    '<div class="grid cols-2">',
      '<div class="feature"><div class="f-ico">', I.search, "</div><h4>Pathfinder</h4><p><code>research/researcher.py</code> — discovers relevant sources and maps the research landscape: choose a role for the query, gather evidence, write its report. Skills: research conductor, report generator, context manager, browser manager, source curator.</p></div>",
      '<div class="feature"><div class="f-ico">', I.flow, "</div><h4>Research Architect</h4><p><code>research/agents/editor.py</code> — designs the section outline from the initial research, then runs the sections in bounded waves so each can avoid restating earlier ones.</p></div>",
      '<div class="feature"><div class="f-ico">', I.book, "</div><h4>Scout Swarm</h4><p><code>research/agents/research_unit.py</code> — investigates one section, with a reviewer and reviser loop that can send it back for revision.</p></div>",
      '<div class="feature"><div class="f-ico">', I.layers, "</div><h4>Narrative Writer</h4><p><code>research/agents/report_agents.py</code> — crafts the report introduction and conclusion from the researched sections.</p></div>",
      '<div class="feature"><div class="f-ico">', I.shield, "</div><h4>Evidence Auditor</h4><p><code>research/agents/report_agents.py</code> — checks factual claims and source support, and may send the draft back to the writer once.</p></div>",
      '<div class="feature"><div class="f-ico">', I.bolt, "</div><h4>Diagram Designer</h4><p><code>research/agents/report_agents.py</code> — creates a single Mermaid diagram from the research data, which the UI renders as a figure.</p></div>",
      '<div class="feature"><div class="f-ico">', I.check, "</div><h4>Report Assembler</h4><p><code>research/agents/report_agents.py</code> — combines the researched content into the final markdown: title, date, introduction, sections, conclusion, references.</p></div>",
      '<div class="feature"><div class="f-ico">', I.target, "</div><h4>Retrieval</h4><p><code>research/retrieval/</code> + <code>agents/search.py</code> — SearXNG with Wikipedia / arXiv / Crossref fallbacks, circuit breakers, dedup, domain diversity and page fetch.</p></div>",
      '<div class="feature"><div class="f-ico">', I.scale, "</div><h4>Audit layer</h4><p><code>engine/audit_bridge.py</code> + <code>core/</code> — reconstructs the fact pool from the report and its sources, then runs confidence, contradiction and evidence-grade analysis.</p></div>",
    "</div>",

    '<h3 id="agents-fallback">The fallback contract</h3>',
    "<p>Every LLM-calling step follows the same shape: try the model, validate the output, and on failure fall back to a deterministic default while logging the degradation with its context. A new step without a non-LLM fallback is a regression relative to the rest of the system.</p>",
    '<div class="code-block" data-lang="python"><div class="code-head"><span class="lang">python · the required shape</span><button class="copy-btn" type="button">', I.copy, " Copy</button></div><pre><code><span class=\"tok-g\">async def</span> name_step(llm: LLMFunnel, ...) -&gt; ...:\n    <span class=\"tok-g\">try</span>:\n        payload = <span class=\"tok-g\">await</span> llm.generate_json(SYSTEM_PROMPT, user_prompt)\n    <span class=\"tok-g\">except</span> Exception <span class=\"tok-g\">as</span> exc:\n        logger.warning(<span class=\"tok-y\">\"[Name] LLM failed, using fallback\"</span>, exc_info=exc)\n        <span class=\"tok-g\">return</span> deterministic_fallback(...)\n\n    result = extract_and_validate(payload)\n    <span class=\"tok-g\">if not</span> result:\n        logger.warning(<span class=\"tok-y\">\"[Name] empty/invalid LLM output, using fallback\"</span>)\n        <span class=\"tok-g\">return</span> deterministic_fallback(...)\n    <span class=\"tok-g\">return</span> result</code></pre></div>",
    '<p class="small muted">A fallback that empties the result is worse than an error the caller can see: a report that silently contains nothing reads as a finding rather than a failure.</p>'
  ];

  /* ============================ Search ============================ */
  C.search = [
    '<p class="eyebrow">Agents</p>',
    '<h2 id="search">Search &amp; retrieval</h2>',
    '<p class="lede">Retrieval is type-driven: the contract’s <code>search_type</code> chooses which providers run, and every provider sits behind a circuit breaker.</p>',
    '<div class="table-wrap"><table>',
      "<thead><tr><th>Provider</th><th>Type</th><th>Used for</th><th>Breaker</th></tr></thead><tbody>",
        "<tr><td><strong>SearXNG</strong></td><td>Web</td><td>Primary web search — self-hosted, no API key</td><td>3 fails · 60&nbsp;s</td></tr>",
        "<tr><td><strong>Wikipedia</strong></td><td>Reference</td><td>Encyclopedia and statistical contracts</td><td>4 fails · 45&nbsp;s</td></tr>",
        "<tr><td><strong>arXiv</strong></td><td>Preprint</td><td>Academic contracts</td><td>3 fails · 90&nbsp;s</td></tr>",
        "<tr><td><strong>Crossref</strong></td><td>Peer-reviewed</td><td>Academic contracts</td><td>3 fails · 90&nbsp;s</td></tr>",
      "</tbody>",
    "</table></div>",

    '<h3 id="search-dedup">Dedup and diversity</h3>',
    "<ul>",
      "<li><strong>Canonical-URL dedup</strong> plus a host blocklist (social media, marketplaces, content farms).</li>",
      "<li><strong>Near-duplicate snippets</strong> removed at 0.6 same-host / 0.85 cross-host overlap.</li>",
      "<li><strong>Domain diversity caps:</strong> Wikipedia contributes at most one result per domain, all other domains at most two.</li>",
      "<li><strong>Ranking</strong> blends authority, snippet richness, query relevance, primary-source bonus and recency — with a Wikipedia penalty.</li>",
    "</ul>",

    '<h3 id="search-freshness">Freshness half-lives</h3>',
    "<p>Freshness decays by search type, so a two-year-old market figure is not treated like a two-year-old encyclopedia entry.</p>",
    '<div class="table-wrap"><table>',
      '<thead><tr><th>Search type</th><th class="num">Half-life (days)</th></tr></thead><tbody>',
        '<tr><td>News</td><td class="num">120</td></tr>',
        '<tr><td>Statistical</td><td class="num">550</td></tr>',
        '<tr><td>Comparison / default</td><td class="num">730</td></tr>',
        '<tr><td>Academic</td><td class="num">1460</td></tr>',
        '<tr><td>Encyclopedia</td><td class="num">2200</td></tr>',
      "</tbody>",
    "</table></div>",
    "<p>Undated sources receive a mid-scale score of <code>0.45</code> rather than being treated as stale. Fetching is bulwarked — <code>MAX_PARALLEL_SEARCH=3</code> for searches, <code>MAX_PARALLEL_FETCH=4</code> for content — capped at 3&nbsp;MB per page, with clear block-page detection.</p>"
  ];

  /* ========================== Section research ========================== */
  C["section-research"] = [
    '<p class="eyebrow">Agents</p>',
    '<h2 id="section-research">Section research</h2>',
    '<p class="lede">Each report section is researched on its own, by a full research pass scoped to that one section\'s title — then reviewed and revised before it joins the report.</p>',
    '<h3 id="sum-roles">How a section is researched</h3>',
    "<ol>",
      "<li>The section is researched: sub-queries are planned from the section title, sources are retrieved, and pages are fetched and filtered against that subtopic.</li>",
      "<li>The evidence is relevance-gated: pages that engage nothing in the section\'s subject are dropped, so a sub-query cannot smuggle in an unrelated study.</li>",
      "<li>The draft is written with the sections already completed supplied to the writer, which is what keeps two sections from making the same point twice.</li>",
      "<li>A reviewer and reviser loop may send the draft back against the task guidelines before it is accepted.</li>",
    "</ol>",

    '<h3 id="sum-rules">Section rules</h3>',
    "<ul>",
      "<li>Write only about this section. A loosely related example belongs to the section whose subtopic it actually serves.</li>",
      "<li>Do not restate a statistic, example or argument another section already made \u2014 reference it in a phrase instead.</li>",
      "<li>Every sentence carrying a number, date or factual assertion ends with an inline citation to the exact source it came from.</li>",
      "<li>A comparison or ranking must state its criteria and apply them to every item it compares.</li>",
      "<li>Where the evidence only shows research potential \u2014 a method, prototype or dataset \u2014 say so rather than presenting it as demonstrated practice.</li>",
      "<li>Paragraphs stay short and technical terms are defined once in plain words.</li>",
    "</ul>",
    '<div class="callout">',
      '<span class="c-ico">', I.layers, "</span>",
      '<div class="c-body"><div class="c-title">Waves are what make de-duplication possible</div>',
      "<p>Sections researched fully in parallel cannot see each other, so two sections independently reach for the same headline statistic. Running them in bounded waves, and handing each wave the drafts already written, is what turns the anti-duplication instructions in the prompt from a request into something the writer can actually satisfy.</p></div>",
    "</div>"
  ];


  C.verification = [
    '<p class="eyebrow">Evidence &amp; Verification</p>',
    '<h2 id="verification">Claim verification</h2>',
    '<p class="lede">Every claim is checked against the source text it cites. The verifier is deterministic — no model call — so it runs on every claim, every pass, in microseconds.</p>',

    '<h3 id="ver-checks">The hard checks</h3>',
    '<div class="table-wrap"><table>',
      "<thead><tr><th>Check</th><th>What it catches</th><th>Threshold</th></tr></thead><tbody>",
        "<tr><td><strong>Weighted lexical overlap</strong></td><td>Topical mismatch — claim not actually in the source</td><td>≥ 0.35 (digits ×1.6, long terms ×1.5)</td></tr>",
        "<tr><td><strong>Source authority</strong></td><td>Unreliable publisher</td><td>≥ 0.55</td></tr>",
        "<tr><td><strong>Numeric grounding</strong></td><td>Fabricated figures</td><td>2% relative tolerance, unit/scale aware</td></tr>",
        "<tr><td><strong>Polarity consistency</strong></td><td>Negation inversion (“X” vs “not X”)</td><td>best-sentence overlap ≥ 0.55</td></tr>",
        "<tr><td><strong>Quote location</strong></td><td>Fabricated direct quotes</td><td>quote must appear in source text</td></tr>",
        "<tr><td><strong>Freshness</strong></td><td>Stale evidence presented as current</td><td>flagged when score &lt; 0.25</td></tr>",
      "</tbody>",
    "</table></div>",
    "<p>Output is a per-claim <code>verification_score</code> plus <code>verified</code>, <code>verification_reason</code>, and a compact summary — total, verified, rate, top failure reasons, stale count, numeric failures, polarity failures and unreachable sources. Truncated claims are dropped, not verified.</p>",

    '<div class="callout mint">',
      '<span class="c-ico">', I.check, "</span>",
      '<div class="c-body"><div class="c-title">Why deterministic</div>',
      "<p>A verifier that costs a model call is a verifier you consult less often. The deterministic checks run at roughly 153&nbsp;µs per claim and measured <strong>F1 = 1.00</strong> with <strong>0% hallucination leak</strong> on the labeled benchmark set — fabricated numbers, invented facts and retraction claims were all rejected.</p></div>",
    "</div>",

    '<h3 id="ver-why-not">Why “not” is never a stopword</h3>',
    "<p>The negation token is weighted like a number (2.00). This single decision keeps “X” and “not X” from merging in dedup, keeps them inside the contradiction band, and keeps citation support from counting a negation as an affirmation. It was found by the benchmark suite, not by intuition.</p>"
  ];

  /* ============================ Contradictions ============================ */
  C.contradictions = [
    '<p class="eyebrow">Evidence &amp; Verification</p>',
    '<h2 id="contradictions">Contradiction detection</h2>',
    '<p class="lede">DeepScout does not blend disagreement into an average. It surfaces the conflict, names the values, and feeds a resolution follow-up back into research.</p>',
    "<p>Three detectors share one semantic engine and emit one output shape. They run in a deliberate order:</p>",
    '<div class="pipeline">',
      '<div class="pipe-node"><div class="pn-ico">1</div><div class="pn-body"><div class="pn-title">Polarity</div><div class="pn-desc">Checked first, outside the similarity band. Fires when both claims carry non-zero, opposing polarity. Fixed severity 0.55.</div></div></div>', ARROW,
      '<div class="pipe-node"><div class="pn-ico">2</div><div class="pn-body"><div class="pn-title">Temporal</div><div class="pn-desc">Same measure across different periods. Severity scales with divergence.</div></div></div>', ARROW,
      '<div class="pipe-node"><div class="pn-ico">3</div><div class="pn-body"><div class="pn-title">Numeric (unit-aware)</div><div class="pn-desc">Inside the similarity band, with 5% relative divergence. Disjoint scope (geography, population) is downgraded to a scope conflict rather than a false numeric one.</div></div></div>',
    "</div>",
    "<p>Results are sorted by severity descending (numeric &gt; polarity &gt; temporal, divergence-scaled) and capped at five. Same-source pairs are skipped. Resolved conflicts — where the difference is period, scope or metric rather than substance — are recorded with their explanation so the report is honest about the spread without mislabelling it as an open disagreement.</p>",
    '<div class="callout info">',
      '<span class="c-ico">', I.info, "</span>",
      '<div class="c-body"><div class="c-title">Why contradictions penalize instead of blending</div>',
      "<p>A weighted average lets three strong sources mask one direct lie. The confidence penalty is pool-size scaled: one conflict among three facts means a third of the evidence disagrees; among thirty it is one stale page.</p></div>",
    "</div>"
  ];

  /* ============================ Citations ============================ */
  C.citations = [
    '<p class="eyebrow">Evidence &amp; Verification</p>',
    '<h2 id="citations">Citations &amp; health</h2>',
    '<p class="lede">Two independent questions are asked of every citation: does the sentence match the source’s evidence, and does the link still resolve?</p>',

    '<h3 id="cit-live">Live URL re-validation</h3>',
    "<p>At delivery, each cited URL in the report’s legend is probed:</p>",
    "<ol>",
      "<li>A <code>HEAD</code> request with redirects followed.</li>",
      "<li>On failure or a <code>403/405/501</code>, a ranged <code>GET</code> for the first 2&nbsp;KB only — never a full page download.</li>",
      "<li>A <code>200–399</code> response is reachable; a changed final URL is marked redirected.</li>",
    "</ol>",
    "<p>Probes run under a concurrency cap of six with a five-second timeout and an identifying user-agent, and are never fatal to the run.</p>",

    '<h3 id="cit-verdicts">Per-source verdicts</h3>',
    "<p>Link health is fused with sentence-support evidence into a single verdict per source:</p>",
    '<div class="table-wrap"><table>',
      "<thead><tr><th>Verdict</th><th>Meaning</th></tr></thead><tbody>",
        '<tr><td><span class="pill mint">ok</span></td><td>URL reachable and all citing sentences supported.</td></tr>',
        '<tr><td><span class="pill amber">warn</span></td><td>Reachable, but some citing sentences are not supported by its evidence.</td></tr>',
        '<tr><td><span class="pill">broken</span></td><td>URL unreachable, but sentence support was fine.</td></tr>',
        '<tr><td><span class="pill bad">bad</span></td><td>Unreachable <em>and</em> under-supported.</td></tr>',
        '<tr><td><span class="pill">unchecked</span></td><td>Non-HTTP or skipped URL.</td></tr>',
      "</tbody>",
    "</table></div>",
    "<p>Broken and bad citations produce an explicit limitations line; warnings are disclosed as partially unsupported. The console shows a citation-health row with the <code>ok / warn / broken / bad / unchecked</code> counts.</p>",

    '<h3 id="cit-support">Answer support</h3>',
    "<p>Separately from link health, the synthesizer’s sentences are checked against the evidence of the source they cite using the shared semantic engine (<code>SUPPORT_THRESHOLD = 0.30</code>). The resulting support rate feeds both the confidence engine and the answer-quality gate.</p>"
  ];

  /* ============================ Confidence ============================ */
  C.confidence = [
    '<p class="eyebrow">Evidence &amp; Verification</p>',
    '<h2 id="confidence">Confidence engine</h2>',
    '<p class="lede">Confidence is computed from measured evidence, not self-reported by a model. Nine signals combine into a score, with a pool-size-scaled contradiction penalty subtracted.</p>',

    '<h3 id="conf-signals">Signals and weights</h3>',
    '<div class="card" style="padding:18px 22px">',
      '<div class="signal-row"><div class="s-label">Citation coverage</div><div class="s-bar"><div class="s-fill" style="width:100%"></div></div><div class="s-val">0.25</div></div>',
      '<div class="signal-row"><div class="s-label">Source quality</div><div class="s-bar"><div class="s-fill" style="width:80%"></div></div><div class="s-val">0.20</div></div>',
      '<div class="signal-row"><div class="s-label">Source diversity</div><div class="s-bar"><div class="s-fill" style="width:60%"></div></div><div class="s-val">0.15</div></div>',
      '<div class="signal-row"><div class="s-label">Claim verification strength</div><div class="s-bar"><div class="s-fill" style="width:60%"></div></div><div class="s-val">0.15</div></div>',
      '<div class="signal-row"><div class="s-label">Cross-source agreement</div><div class="s-bar"><div class="s-fill" style="width:60%"></div></div><div class="s-val">0.15</div></div>',
      '<div class="signal-row"><div class="s-label">Critic survival</div><div class="s-bar"><div class="s-fill" style="width:40%"></div></div><div class="s-val">0.10</div></div>',
      '<div class="signal-row"><div class="s-label">Citation support</div><div class="s-bar"><div class="s-fill" style="width:40%"></div></div><div class="s-val">carved</div></div>',
      '<div class="signal-row"><div class="s-label">Axis coverage</div><div class="s-bar"><div class="s-fill" style="width:20%"></div></div><div class="s-val">carved</div></div>',
      '<div class="signal-row"><div class="s-label">Freshness</div><div class="s-bar"><div class="s-fill" style="width:20%"></div></div><div class="s-val">carved</div></div>',
    "</div>",
    '<p class="small muted">The three conditional signals carve weight from existing ones when their inputs are present, so historical scores stay comparable when they are absent. Freshness is measured only when publish dates parse.</p>',

    '<h3 id="conf-penalty">Contradiction penalty</h3>',
    "<p>Conflicts are subtracted, not averaged, and scaled by pool size (<code>scale = min(2.0, max(1.0, 6.0 / fact_count))</code>):</p>",
    "<ul>",
      "<li>Severe conflict (severity ≥ 0.60): <strong>−0.06</strong> each</li>",
      "<li>Moderate conflict: <strong>−0.03</strong> each</li>",
      "<li>Total penalty capped at <strong>−0.18</strong></li>",
      "<li>Resolved conflicts and intra-source conflicts are skipped</li>",
    "</ul>",

    '<h3 id="conf-degraded">Degraded-run cap</h3>',
    "<p>If the summarizer or synthesizer fell back to deterministic extraction, or any provider degraded, the overall confidence is capped at <strong>0.55</strong> — below the 0.75 sufficiency threshold. This is the decisive guard against a full LLM outage silently producing a confident-looking report.</p>",
    '<div class="callout warn">',
      '<span class="c-ico">', I.warn, "</span>",
      '<div class="c-body"><div class="c-title">Why this exists</div>',
      "<p>A live run once failed every LLM call and “completed” on extractive fallbacks with off-topic sentences — yet scored 0.772, labelled “High.” The cap, the disclosed <code>degraded</code> flag and the UI banner all exist because self-verifying fallback evidence inflates every lexical signal by construction.</p></div>",
    "</div>",
    '<div class="grid cols-2" style="margin-top:18px">',
      '<div class="metric-card"><div class="m-val">0.823</div><div class="m-lab">Calibration · strong evidence</div></div>',
      '<div class="metric-card"><div class="m-val">0.283</div><div class="m-lab">Calibration · weak evidence</div></div>',
    "</div>",
    '<p class="small muted" style="margin-top:12px">Confidence calibration measured <strong>100% in expected band</strong> with monotonic ordering on the labeled set.</p>'
  ];

  /* ============================ Budget ============================ */
  C.budget = [
    '<p class="eyebrow">Operations</p>',
    '<h2 id="budget">Run limits</h2>',
    '<p class="lede">Every run is bounded, so a slow or failing provider can never hold a request open indefinitely. The bounds are wall-clock and call-count based — DeepScout does not meter or cap spend.</p>',
    '<h3 id="bud-ceilings">The ceilings</h3>',
    '<div class="table-wrap"><table>',
      '<thead><tr><th>Ceiling</th><th class="num">Default</th><th>Setting</th></tr></thead><tbody>',
        '<tr><td>LLM calls per run</td><td class="num">60</td><td><code>MAX_LLM_CALLS</code></td></tr>',
        '<tr><td>Research wall-clock seconds</td><td class="num">300</td><td><code>MAX_RESEARCH_SECONDS</code></td></tr>',
        '<tr><td>Per-request ceiling</td><td class="num">1000</td><td><code>RESEARCH_TIMEOUT_SEC</code></td></tr>',
        '<tr><td>Per-call timeout (Groq / Hugging Face)</td><td class="num">60</td><td><code>LLM_TIMEOUT_SEC</code></td></tr>',
        '<tr><td>Per-call timeout (custom provider)</td><td class="num">60</td><td><code>CUSTOM_LLM_TIMEOUT_SEC</code></td></tr>',
        '<tr><td>Concurrent LLM calls</td><td class="num">4</td><td><code>MAX_PARALLEL_LLM</code></td></tr>',
      "</tbody>",
    "</table></div>",
    "<p>Mode multipliers scale the run ceilings together. The custom-provider timeout is separate because OpenAI-compatible hosts are typically slower on planner-sized prompts, and the circuit breaker trips on a single timeout so one stalled host cannot degrade every stage.</p>",

    '<h3 id="bud-ledger">The run ledger</h3>',
    "<p>The ledger rides a <code>ContextVar</code>, so concurrent runs never share state. It counts LLM calls, search calls, cache hits and misses, and wall time per run. Those counts are what the UI shows; DeepScout deliberately does not attach a price to them.</p>",
    '<div class="callout info">',
      '<span class="c-ico">', I.info, "</span>",
      '<div class="c-body"><div class="c-title">Bounded work, not metered spend</div>',
      "<p>There is no USD ceiling, no token ceiling, and no budget governor. If a run cannot finish inside <code>RESEARCH_TIMEOUT_SEC</code> it is reported as a timeout rather than left hanging, and the browser disconnects cleanly on Stop.</p></div>",
    "</div>"
  ];

  /* ============================ Stopping ============================ */
  C.stopping = [
    '<p class="eyebrow">Operations</p>',
    '<h2 id="stopping">Depth &amp; stopping</h2>',
    '<p class="lede">A run ends when its stages finish or when a bound is hit. There is no adaptive depth controller: the amount of work a run does is decided up front, by the mode and the outline the planner produces.</p>',
    '<h3 id="stop-order">How much a run does</h3>',
    "<ol>",
      "<li><strong>Mode sets the ceiling.</strong> The planner may propose up to <code>3</code> sections in <code>quick</code>, <code>5</code> in <code>standard</code> and <code>8</code> in <code>deep</code>.</li>",
      "<li><strong>Section research runs in waves.</strong> <code>MAX_PARALLEL_AGENTS</code> sections are researched at a time; each wave's writers also receive the sections already written, so later sections do not restate earlier findings.</li>",
      "<li><strong>Review loops are bounded.</strong> Plan review and draft review may each send work back a fixed number of times; the fact checker may revise once.</li>",
      "<li><strong>Bounds end the run.</strong> If a stage exceeds its share of the budget or the request hits <code>RESEARCH_TIMEOUT_SEC</code>, the run stops and says so.</li>",
    "</ol>",
    "<p>There is no confidence target per mode and no marginal-gain stopping: a run researches the sections it planned, once, and assembles the report.</p>",
    '<p class="small muted">When a run stops on a bound rather than completing, the reason is reported in the stream as an <code>error</code> or <code>timeout</code> event — an early stop is disclosed, never hidden.</p>'
  ];

  /* ============================ Reliability ============================ */
  C.reliability = [
    '<p class="eyebrow">Operations</p>',
    '<h2 id="reliability">Reliability</h2>',
    '<p class="lede">The system is designed to fail loudly, cheaply and in the right direction.</p>',

    '<h3 id="rel-provider">Provider chain</h3>',
    "<p>Generation tries providers in order: a UI-selected OpenAI-compatible provider first, then Groq, then HuggingFace. A UI-selected active provider is exclusive by default — if it fails, the run raises rather than silently falling back to a different model. HuggingFace itself walks a short internal candidate list on 404/410.</p>",

    '<h3 id="rel-breakers">Circuit breakers and timeouts</h3>',
    "<ul>",
      "<li>Three consecutive failures open a provider breaker for 60&nbsp;s; a single per-attempt timeout opens it immediately.</li>",
      "<li>A 429 does <strong>not</strong> open the breaker — throttling is not an outage.</li>",
      "<li>Auth, payment and permanent errors (<code>401/402/403</code>, permanent <code>400</code>, <code>404</code>) fail fast and are never retried.</li>",
      "<li>Timeouts are never retried; they move straight to the next provider.</li>",
      "<li>Retry-After hints are honored but capped at 3&nbsp;s.</li>",
    "</ul>",
    "<p>Two timeout budgets exist because the two worlds differ: <code>llm_timeout_sec = 25</code> for Groq and HuggingFace, and <code>custom_llm_timeout_sec = 60</code> for slower OpenAI-compatible hosts. A hard wall-clock cap at 1.5× the base guards against drip-feeding proxies.</p>",

    '<h3 id="rel-concurrency">Concurrency as a reliability feature</h3>',
    "<p><code>MAX_PARALLEL_LLM=2</code> exists because concurrent large prompts are exactly what exhausts free-tier token quotas — observed live when three parallel summarizer calls burned a 200k-token daily budget. The semaphore bounds in-flight prompts, not threads.</p>",

    '<h3 id="rel-preflight">Pre-flight probe</h3>',
    "<p>Before a run starts, the LLM client probes every configured provider in parallel. If none is reachable, the run fails in seconds with per-provider reasons instead of degrading for minutes. Successful probes are cached for 30&nbsp;s; failures are not cached, so a quota reset is noticed immediately.</p>",

    '<h3 id="rel-patterns">Failure patterns the codebase guards against</h3>',
    "<ul>",
      "<li><strong>Silent fallback hiding a real bug.</strong> Every broad exception logs with context; a <code>NameError</code> once hid for the entire history of the planner behind a bare <code>except</code>.</li>",
      "<li><strong>Mutating module-level state.</strong> Per-request state is scoped to the request coroutine; there is no unbounded request-keyed global.</li>",
      "<li><strong>Cache poisoning on failure.</strong> An agent’s empty result is never cached — the summarizer once poisoned its cache key for a full TTL.</li>",
      "<li><strong>Stale code serving a verification run.</strong> Live verifications assert the request id appears in a fresh backend log.</li>",
    "</ul>"
  ];

  /* ============================ Persistence ============================ */
  C.persistence = [
    '<p class="eyebrow">Operations</p>',
    '<h2 id="persistence">Persistence &amp; trace</h2>',
    '<p class="lede">SQLite in WAL mode, auto-initialising schema, additive migrations only. Every run leaves a complete, replayable record.</p>',
    '<div class="table-wrap"><table>',
      "<thead><tr><th>Table</th><th>Purpose</th></tr></thead><tbody>",
        "<tr><td><code>research_runs</code></td><td>One row per run: query, complexity, agent count, status, cost, confidence, iterations.</td></tr>",
        "<tr><td><code>agent_tasks</code></td><td>Planned contracts for the run.</td></tr>",
        "<tr><td><code>sources</code></td><td>Retrieved sources, deduped by URL, with reliability score.</td></tr>",
        "<tr><td><code>evidence</code></td><td>Raw snippets that claims derive from.</td></tr>",
        "<tr><td><code>claims</code></td><td>Extracted claims with verification flag, confidence, agent and challenged state.</td></tr>",
        "<tr><td><code>verification_results</code></td><td>Per-claim score and reason.</td></tr>",
        "<tr><td><code>contradictions</code></td><td>Detected conflict pairs.</td></tr>",
        "<tr><td><code>critic_reviews</code></td><td>Every critic pass, with confidence breakdown and improved queries.</td></tr>",
        "<tr><td><code>citations</code></td><td>Parsed legend markers, domains and URLs.</td></tr>",
        "<tr><td><code>decisions</code></td><td>Decision-layer options.</td></tr>",
        "<tr><td><code>final_reports</code></td><td>Canonical markdown report keyed by run id.</td></tr>",
        "<tr><td><code>agent_events</code></td><td>Node timings, retries and fallbacks — the replay timeline.</td></tr>",
        "<tr><td><code>research_reports</code></td><td>Legacy flat store, kept in sync for existing readers.</td></tr>",
        "<tr><td><code>llm_providers</code></td><td>Saved providers with Fernet-encrypted keys and key hints.</td></tr>",
      "</tbody>",
    "</table></div>",
    "<p>Schema changes are additive and idempotent — <code>CREATE TABLE IF NOT EXISTS</code> and <code>ALTER TABLE ... ADD COLUMN</code> guarded by <code>PRAGMA table_info</code>. The WAL journal allows concurrent readers alongside the writer, and a busy timeout prevents lock errors.</p>",

    '<h3 id="persist-replay">Research replay</h3>',
    "<p><code>GET /api/research/{run_id}/trace</code> reconstructs the full ordered timeline — plan, sources, claims, verification, contradictions, citations, every critic review and decisions. The UI renders a read-only research card from the trace alone, with no re-fetching. A failed or timed-out run can be resumed at the critic node from its persisted evidence rather than re-running the whole pipeline.</p>",

    '<h3 id="persist-security">Security posture</h3>',
    "<ul>",
      "<li>Provider keys are Fernet-encrypted at rest (<code>DEEPSCOUT_SECRET_KEY</code> or an auto-created 0600 <code>.deepscout_secret</code>) and never serialised to the UI.</li>",
      "<li>The service binds to <code>127.0.0.1</code> by default; do not expose it without an auth layer.</li>",
      "<li>The research endpoint is rate-limited (<code>RATE_LIMIT=5/minute</code> by default).</li>",
      "<li>Secrets are never logged; provider probe failures log only the exception type.</li>",
    "</ul>"
  ];

  /* ============================ API ============================ */
  C.api = [
    '<p class="eyebrow">Operations</p>',
    '<h2 id="api">API reference</h2>',
    '<p class="lede">A small, focused HTTP surface. The streaming endpoint is the heart of the system.</p>',

    '<h3 id="api-research">Research</h3>',
    '<div class="card" style="padding:6px 0">',
      '<div class="endpoint"><span class="method post">POST</span><span class="path">/api/research/stream</span><span class="ep-desc">Start a run; NDJSON event stream</span></div>',
      '<div class="endpoint"><span class="method post">POST</span><span class="path">/api/research/{run_id}/resume</span><span class="ep-desc">Resume a failed/timed-out run from its checkpoint</span></div>',
      '<div class="endpoint"><span class="method get">GET</span><span class="path">/api/research/{run_id}/trace</span><span class="ep-desc">Full replay reconstruction of a run</span></div>',
    "</div>",

    '<h3 id="api-providers">Providers</h3>',
    '<div class="card" style="padding:6px 0">',
      '<div class="endpoint"><span class="method get">GET</span><span class="path">/api/providers</span><span class="ep-desc">Saved providers (keys masked) + active id</span></div>',
      '<div class="endpoint"><span class="method post">POST</span><span class="path">/api/providers</span><span class="ep-desc">Add an OpenAI-compatible provider</span></div>',
      '<div class="endpoint"><span class="method put">PUT</span><span class="path">/api/providers/{id}</span><span class="ep-desc">Partial update; omit key to keep stored key</span></div>',
      '<div class="endpoint"><span class="method del">DEL</span><span class="path">/api/providers/{id}</span><span class="ep-desc">Delete a provider</span></div>',
      '<div class="endpoint"><span class="method post">POST</span><span class="path">/api/providers/{id}/active</span><span class="ep-desc">Set the single active provider</span></div>',
      '<div class="endpoint"><span class="method post">POST</span><span class="path">/api/providers/active/clear</span><span class="ep-desc">Fall back to the env/Groq/HF chain</span></div>',
      '<div class="endpoint"><span class="method post">POST</span><span class="path">/api/providers/{id}/test</span><span class="ep-desc">Single-shot latency probe</span></div>',
    "</div>",

    '<h3 id="api-health">Health</h3>',
    '<div class="card" style="padding:6px 0">',
      '<div class="endpoint"><span class="method get">GET</span><span class="path">/api/health</span><span class="ep-desc">Liveness check</span></div>',
    "</div>",

    '<h3 id="api-request">Stream request body</h3>',
    '<div class="code-block" data-lang="json"><div class="code-head"><span class="lang">json</span><button class="copy-btn" type="button">', I.copy, " Copy</button></div><pre><code>{\n  \"query\": \"Should Bangladesh expand nuclear energy?\",   <span class=\"tok-c\">// 5-500 chars</span>\n  \"mode\": \"standard\",                                     <span class=\"tok-c\">// quick|standard|deep</span>\n  \"deep_research\": false                                  <span class=\"tok-c\">// opt-in fan-out</span>\n}</code></pre></div>"
  ];

  /* ============================ Benchmarks ============================ */
  C.benchmarks = [
    '<p class="eyebrow">Operations</p>',
    '<h2 id="benchmarks">Benchmarks</h2>',
    '<p class="lede">Deterministic, network-free measurement against hand-labelled fixtures. Every number is reproducible with <code>python bench/run_offline.py</code>.</p>',
    '<div class="grid cols-3">',
      '<div class="metric-card"><div class="m-val acc">F1 1.00</div><div class="m-lab">Verification accuracy</div><div class="m-sub">8 TP · 0 FP · 0 FN · 8 TN</div></div>',
      '<div class="metric-card"><div class="m-val acc">0.000</div><div class="m-lab">Hallucination leak rate</div><div class="m-sub">6/6 fabricated claims rejected</div></div>',
      '<div class="metric-card"><div class="m-val acc">1.00</div><div class="m-lab">Contradiction F1</div><div class="m-sub">numeric · polarity · temporal</div></div>',
      '<div class="metric-card"><div class="m-val">100%</div><div class="m-lab">Confidence calibration</div><div class="m-sub">in expected band, monotonic</div></div>',
      '<div class="metric-card"><div class="m-val">66.7%</div><div class="m-lab">LLM cache hit ratio</div><div class="m-sub">6.26× repeat-pass speedup</div></div>',
      '<div class="metric-card"><div class="m-val">0.709</div><div class="m-lab">Semantic Spearman ρ</div><div class="m-sub">dedup F1 0.75 @ 0.86</div></div>',
    "</div>",

    '<h3 id="bench-perf">Performance</h3>',
    '<div class="table-wrap"><table>',
      '<thead><tr><th>Operation</th><th class="num">Latency</th></tr></thead><tbody>',
        '<tr><td>Single claim verification</td><td class="num">152.9 µs</td></tr>',
        '<tr><td>Pair similarity (single)</td><td class="num">140.7 µs</td></tr>',
        '<tr><td>Answer-support check (40 sentences)</td><td class="num">0.4 ms</td></tr>',
        '<tr><td>Cross-similarity (40 × 200)</td><td class="num">16.0 ms</td></tr>',
        '<tr><td>Similarity matrix (60 facts)</td><td class="num">365.7 ms</td></tr>',
        '<tr><td>Similarity matrix (200 facts)</td><td class="num">924.8 ms</td></tr>',
      "</tbody>",
    "</table></div>",
    "<p class=\"small muted\">Peak-RSS delta across a 300-fact matrix workload was 21.8&nbsp;MB — the semantic engine fits comfortably in the 8&nbsp;GB budget alongside the rest of the stack.</p>",

    '<h3 id="bench-limits">Known limitations, measured not hidden</h3>',
    "<ul>",
      "<li><strong>Lexical, not embedding-level.</strong> Paraphrases such as “doubled over the past decade” versus “nearly doubled in the last ten years” score below the dedup threshold. Dedup recall on the labeled set is 0.60 — but precision is 1.00: it never merges what it should not.</li>",
      "<li><strong>Offline suite scope.</strong> The deterministic suite exercises measurable components and a scripted pipeline; live-model wording quality is measured by <code>bench/run_live.py</code> with real provider keys.</li>",
      "<li><strong>Cache speedup.</strong> The committed run reports 6.26× on a repeat-heavy workload; README quotes 6.7× from a separate run. Both share the same 66.7% hit ratio.</li>",
    "</ul>",

    '<div class="callout mint">',
      '<span class="c-ico">', I.check, "</span>",
      '<div class="c-body"><div class="c-title">Decisions are made on measurements</div>',
      "<p>Several early bugs — the negation-word dedup merge, the electrical-transformer contamination, the off-topic limitations list — were found by writing a failing benchmark case first, then fixing the code. The benchmark suite is the regression net, not a report card.</p></div>",
    "</div>"
  ];

  window.DEEPSCOUT_DOCS = { nav: NAV, content: C, icon: I };
})();
