/**
 * Mock replay harness — a local preview of the pipeline trace.
 *
 * Feeds the component a scripted sequence of REAL-shaped wire events on a timer,
 * so the motion, staggered entrances, chips and status transitions can be
 * reviewed without running a research query. The frames are the same shapes the
 * backend emits; only the content is invented.
 *
 * Usage (dev only):
 *   /trace-preview.html           — open in a browser via Vite
 *   import { mockFrames } from "./mock";
 */
import type { WireFrame } from "./events";

let t = 0;
/** Monotonic clock so the demo's timestamps look real. */
const at = (): number => (t += 40 + Math.floor(Math.random() * 90));

export const mockFrames: WireFrame[] = [
  { type: "progress", message: "Query received", __ts: at() },
  {
    type: "intent",
    query_type: "analytical",
    domain: "business",
    explanation_level: "detailed",
    ambiguity: true,
    senses: [{ label: "technology" }, { label: "economy" }],
    __ts: at(),
  },
  {
    type: "route",
    path: "research",
    reason: "Query requires external evidence (freshness, quantitative).",
    confidence: 0.9,
    origin: "heuristic",
    __ts: at(),
  },
  {
    type: "plan",
    items: [
      "What are the trends and opportunities in 2026?",
      "Technology shifts worth watching — trends and opportunities predictions",
      "Business opportunities emerging markets 2026",
      "Risks and constraints on the outlook",
    ],
    __ts: at(),
  },
  { type: "search_progress", snippets: 4, __ts: at() },
  {
    type: "search_query",
    query: "2026 trends opportunities predictions technology business economy",
    results: [
      { title: "The Most Impactful Business Technology Trends in 2026", url: "https://www.forbes.com/councils/forbestechcouncil/2025/01/22/the-most-impactful-business-technology-trends/", source: "tavily" },
      { title: "Five trends to watch in the global economy in 2026", url: "https://www.atlanticcouncil.org/content-series/five-trends-to-watch/", source: "tavily" },
      { title: "Predictions 2026: The Race To Trust And Value", url: "https://www.forbes.com/sites/sergeiklebnikov/2026/01/05/predictions-2026-the-race-to-trust-and-value/", source: "tavily" },
      { title: "Tech Trends 2026: Deloitte Insights", url: "https://www.deloitte.com/us/en/insights/topics/emerging-technologies.html", source: "tavily" },
    ],
    __ts: at(),
  },
  {
    type: "search_query",
    query: "2026 technology trends artificial intelligence quantum computing robotics",
    results: [
      { title: "Top Technology Trends in 2026: AI, Quantum Computing …", url: "https://www.weforum.org/publications/top-10-emerging-technologies-2026/", source: "tavily" },
      { title: "2026 Technology Innovation Trends: AI Agents, Humanoid Robots", url: "https://www.linkedin.com/pulse/2026-technology-trends-ai-agents-humanoid-robots", source: "ddg_text" },
      { title: "Tech Trends 2026: Forces shaping the future", url: "https://www.gartner.com/en/articles/technology-trends-2026", source: "ddg_text" },
      { title: "Global Reports", url: "https://www.weforum.org/reports/", source: "ddg_text" },
    ],
    __ts: at(),
  },
  {
    type: "search_query",
    query: "2026 business opportunities emerging markets startup trends",
    results: [
      { title: "Startup Industry Trends 2026", url: "https://www.cnbc.com/2026/02/11/startup-industry-trends-2026.html", source: "tavily" },
      { title: "Top 10 Entrepreneurship Trends Shaping 2026", url: "https://www.forbes.com/sites/jodiecook/2026/01/08/top-10-entrepreneurship-trends-shaping-2026/", source: "tavily" },
      { title: "8 Global Venture Capital Trends to Watch in 2026", url: "https://endeavor.vc/2026-global-vc-trends/", source: "tavily" },
    ],
    __ts: at(),
  },
  {
    type: "findings",
    items: [
      { claim: "Enterprise AI spending is projected to grow 34% in 2026 as agentic systems move from pilots into production.", source: "https://www.gartner.com/en/articles/technology-trends-2026", verified: null },
      { claim: "Quantum computing investment reached a record high in 2025, though commercial scale remains years away.", source: "https://www.weforum.org/reports/", verified: null },
      { claim: "Emerging-market startup funding grew for a third consecutive year, led by Southeast Asia and India.", source: "https://endeavor.vc/2026-global-vc-trends/", verified: null },
    ],
    __ts: at(),
  },
  { type: "critic", iteration: 1, reason: "Quantitative claims need a second source.", __ts: at() },
  {
    type: "findings",
    verified_update: true,
    items: [
      { claim: "Enterprise AI spending is projected to grow 34% in 2026.", source: "https://www.gartner.com/en/articles/technology-trends-2026", verified: true },
      { claim: "Quantum computing investment reached a record high in 2025.", source: "https://www.weforum.org/reports/", verified: true },
    ],
    __ts: at(),
  },
  { type: "critic", iteration: 2, reason: "Sufficient after verification.", __ts: at() },
  {
    type: "final_report",
    report:
      "## Trends shaping 2026\n\nAI moves from pilot to production across enterprise budgets, while quantum stays a multi-year investment story [1][2].\n\n## Where the opportunities are\n\nEmerging-market funding grew for a third year [3].",
    confidence: 0.78,
    answer_support: 0.84,
    __ts: at(),
  },
];

/** Replay the mock on an interval, exactly as a live stream would arrive. */
export function startMockReplay(
  onFrame: (frame: WireFrame, index: number) => void,
  onDone?: () => void,
  intervalMs = 420,
): () => void {
  let i = 0;
  const id = setInterval(() => {
    if (i >= mockFrames.length) {
      clearInterval(id);
      onDone?.();
      return;
    }
    onFrame(mockFrames[i], i);
    i += 1;
  }, intervalMs);
  return () => clearInterval(id);
}