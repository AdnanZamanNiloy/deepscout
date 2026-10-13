/* Agent Library — the DeepScout workforce as it actually exists. Objectives and
 * tools are stated from the code, not marketing copy. Live per-agent latency
 * and cost are not instrumented yet — said plainly rather than faked.
 *
 * The roster mirrors the shipped research graph
 * (`app/agents/team.py`): eight stages in execution order. */

import { IconChart, IconDoc, IconSearch, IconShieldCheck, IconTarget } from "./icons";

const AGENTS = [
  {
    name: "Pathfinder", icon: IconSearch,
    objective: "Discovers relevant sources and maps the research landscape.",
    tools: ["web_search", "fetch_content"],
    outputs: ["initial research report", "source pool"],
  },
  {
    name: "Research Architect", icon: IconTarget,
    objective: "Designs the research strategy and creates the report outline.",
    tools: ["LLM planning", "methodology prompt"],
    outputs: ["section outline", "report title"],
  },
  {
    name: "Review Gate", icon: IconShieldCheck,
    objective: "Approves the research plan before the investigation begins.",
    tools: ["plan gate"],
    outputs: ["accept / revise decision"],
  },
  {
    name: "Scout Swarm", icon: IconSearch,
    objective: "Investigates individual sections in parallel and gathers evidence.",
    tools: ["web_search", "fetch_content", "section reviewer/reviser"],
    outputs: ["section drafts", "per-section sources"],
  },
  {
    name: "Narrative Writer", icon: IconDoc,
    objective: "Crafts the introduction and conclusion into a coherent narrative.",
    tools: ["LLM writing", "deterministic fallback"],
    outputs: ["introduction", "conclusion"],
  },
  {
    name: "Evidence Auditor", icon: IconShieldCheck,
    objective: "Checks factual claims, source support, and potential inconsistencies.",
    tools: ["LLM fact-check"],
    outputs: ["fact-check notes", "revision decision"],
  },
  {
    name: "Diagram Designer", icon: IconChart,
    objective: "Creates a Mermaid diagram to illustrate the research.",
    tools: ["LLM diagram generation"],
    outputs: ["mermaid diagram"],
  },
  {
    name: "Report Assembler", icon: IconChart,
    objective: "Combines the researched content into the final Markdown report.",
    tools: ["report assembly"],
    outputs: ["final report"],
  },
];

export default function AgentsView() {
  return (
    <div className="view anim-rise">
      <div className="view-head">
        <h2>Agents</h2>
        <p>
          {AGENTS.length} pipeline stages in execution order · section research runs a
          reviewer/reviser loop per section
        </p>
      </div>

      {/* The same numbered spine used in the intelligence rail: stage order
          is a real, stable sequence, not decoration. */}
      <ol className="spine" aria-label="Pipeline execution order">
        {AGENTS.map((a, i) => (
          <li key={a.name} className="spine-node">
            <span className="spine-idx">{i + 1}</span>
            <span className="spine-body">
              <span className="spine-title">
                <a.icon size={14} style={{ color: "var(--t3)", flex: "none" }} />
                {a.name}
              </span>
              <span className="spine-desc">{a.objective}</span>
              <span className="spine-meta">
                tools: {a.tools.join(" · ")} — outputs: {a.outputs.join(" · ")}
              </span>
            </span>
          </li>
        ))}
      </ol>

      <p className="empty" style={{ marginTop: 18 }}>
        Per-agent latency, cost and success rate need tool-call tracing, which is not
        instrumented yet. Nothing above is estimated.
      </p>
    </div>
  );
}
