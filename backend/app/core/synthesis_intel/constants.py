from __future__ import annotations

import re
from typing import Dict, Set, Tuple



_CITATION_RE = re.compile(r"\[\d+\]")


_STOPWORDS: Set[str] = {
    "a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "for",
    "with", "by", "as", "at", "from", "into", "than", "that", "this",
    "these", "those", "it", "its", "is", "are", "was", "were", "be", "been",
    "being", "has", "have", "had", "do", "does", "did", "will", "would",
    "can", "could", "may", "might", "should", "which", "who", "whom",
    "whose", "what", "when", "where", "why", "how", "there", "their",
    "they", "them", "he", "she", "his", "her", "him", "we", "our", "you",
    "your", "i", "also", "such", "more", "most", "less", "least", "very",
    "just", "only", "then", "so", "if", "about", "over", "under", "across",
    "during", "after", "before", "while", "because", "however", "thus",
}


_SUFFIXES = ("ings", "ing", "ies", "ied", "es", "ed", "s")


_MECHANISM_RE = re.compile(
    r"(?i)\b(because|driven by|as a result of|mechanism|results? in|leads? to|"
    r"caused by|gives? rise to|due to|stems? from|the reason|so that|thereby|"
    r"which forces|forcing|trigger(?:s|ed|ing)?|explains? why|follows? from|"
    r"the driver|is driven|arises? from|comes? from|"
    r"which (?:removed|eliminated|enabled|allowed|reduced|caused|created|"
    r"introduced|replaced|made))\b"
)


_IMPLICATION_RE = re.compile(
    r"(?i)\b(therefore|impl(?:y|ies|ied|ication)|at the cost of|trade-?offs?|"
    r"(?:which |this )?means|the consequences?|as a consequence|in turn|"
    r"puts? pressure|what follows|the upshot|raises? the question|signals? that|"
    r"raises? the .{0,24}cost|raises? cost|raise(?:s|d)? .{0,16}cost|"
    r"the price of|at the expense of|comes? at the cost|requires? "
    r"(?:backup|storage|subsid)|entails|necessitates|has? implications?)\b"
)


_COMPARISON_RE = re.compile(
    r"(?i)\b(compared (?:with|to)|versus|\bvs\.?\b|whereas|unlike|in contrast|"
    r"on the other hand|relative to|outweighs?|higher than|lower than|"
    r"cheaper than|more expensive than|better than|worse than|"
    r"comparatively|by comparison|less than|more than)\b"
)


_UNCERTAINTY_RE = re.compile(
    r"(?i)\b(unresolved|uncertain|unclear|not established|remains unknown|"
    r"open question|cannot be settled|disputed|contested|not yet verified|"
    r"single-source|provisional|could not verify|no evidence|insufficient "
    r"evidence|contradict)\b"
)


_DIMENSIONS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("mechanism", _MECHANISM_RE),
    ("implication", _IMPLICATION_RE),
    ("comparison", _COMPARISON_RE),
    ("uncertainty", _UNCERTAINTY_RE),
)


MOVES: Tuple[str, ...] = (
    "implication",
    "mechanism",
    "causal",
    "tradeoff",
    "uncertainty",
    "comparison",
    "strategic",
)


_TRANSITION_BY_MOVE: Dict[str, Tuple[str, ...]] = {
    "implication": (
        "Building on the picture above:",
        "Extending that finding to what follows from it:",
        "Taking that established point one step further:",
        "Reading that figure for its consequences:",
    ),
    "mechanism": (
        "Building on that fact, the mechanism runs as follows:",
        "The mechanism behind that established point is worth stating plainly:",
        "That outcome follows from a mechanism the earlier section did not name:",
    ),
    "causal": (
        "Building on that fact, the causal chain is worth naming:",
        "That outcome did not arise by accident:",
        "The cause behind that established point runs as follows:",
    ),
    "tradeoff": (
        "Building on that established point, it carries a trade-off:",
        "That gain is not free:",
        "Weighing that claim against what it costs:",
    ),
    "comparison": (
        "Set against the alternatives already discussed:",
        "Compared with the other options the report has covered:",
        "Weighing that claim against the alternatives:",
    ),
    "uncertainty": (
        "That figure is less settled than a single statement suggests:",
        "Building on that point, its reliability must be qualified:",
        "That claim carries an unresolved caveat:",
    ),
    "strategic": (
        "Building on that established point, the strategic reading is this:",
        "For the decision the report is answering, that changes the calculus:",
        "Extending that finding to the plan it informs:",
    ),
    # Legacy move names mapped onto the adaptive set for backward compatibility.
    "strength": (
        "That finding is unusually well grounded:",
        "Building on that evidence, its provenance is stronger than most:",
        "That claim draws on source material worth foregrounding:",
    ),
    "framing": (
        "Building on the picture already established:",
        "Returning to that established fact for this section's angle:",
        "With that established, this section turns to what it implies here:",
    ),
}


_TOPIC_ANALYSIS: Tuple[Tuple["re.Pattern[str]", Dict[str, str]], ...] = (
    (
        re.compile(r"(?i)\b(cost|costs|finance|financ|loan|debt|tariff|budget|"
                   r"billion|trillion|million|invest|price|subsid)\b"),
        {
            "implication": (
                "which means the long-term financing obligations it commits the "
                "sector to constrain the choices the rest of the analysis depends on"
            ),
            "mechanism": (
                "the cost pressure follows a mechanism in which fixed capital "
                "must be recovered over a long operating life, which raises the "
                "stakes of any delay"
            ),
            "causal": (
                "that cost level did not appear by chance: it follows from the "
                "capital structure and construction timeline the project entails, "
                "which is why it recurs"
            ),
            "tradeoff": (
                "that financing buys capacity at the cost of locking the budget "
                "into one pathway for decades"
            ),
            "comparison": (
                "that cost stands out compared with the alternatives discussed "
                "elsewhere, where relative cost per unit of output is the "
                "deciding factor"
            ),
            "uncertainty": (
                "the cost figure is disputed across sources, so it should be "
                "read as a provisional range rather than a settled estimate"
            ),
            "strategic": (
                "for the budgeting decision that changes the role the cost "
                "plays, which means the sector is committed to one financing "
                "profile"
            ),
            "strength": (
                "the cost figure is corroborated by multiple independent "
                "sources, which raises confidence in it rather than merely "
                "repeating it"
            ),
            "framing": (
                "for this section's angle the cost matters because it frames "
                "the financing trade-off the section goes on to examine"
            ),
        },
    ),
    (
        re.compile(r"(?i)\b(emission|carbon|climate|renewabl|solar|wind|"
                   r"nuclear|energy|electric|grid|power|generation|capacity|"
                   r"megawatt|gigawatt|mwh|kwh)\b"),
        {
            "implication": (
                "which means the value of firm output shifts, so the flexibility "
                "the system can rely on is what shapes the grid's real choices"
            ),
            "mechanism": (
                "the mechanism is that generation choices lock in an "
                "infrastructure pathway whose costs and emissions persist for "
                "decades"
            ),
            "causal": (
                "that outcome follows from how the technology converts its fuel "
                "or resource into dispatchable output, not from its headline "
                "nameplate size"
            ),
            "tradeoff": (
                "that strength is bought at the cost of slow build times and "
                "high upfront capital, which is the trade-off the comparison turns on"
            ),
            "comparison": (
                "that firm output stands out compared with the alternative "
                "generation options discussed elsewhere, where output per unit "
                "of cost is the deciding factor"
            ),
            "uncertainty": (
                "the figure is disputed across sources, so it should be read "
                "as a provisional range rather than a settled estimate"
            ),
            "strategic": (
                "for a grid balancing variable renewables, that changes its "
                "role from bulk energy producer to dispatchable system "
                "stabilizer, which means flexibility sets its value"
            ),
            "strength": (
                "the finding is corroborated by multiple independent sources, "
                "which raises confidence in it rather than merely repeating it"
            ),
            "framing": (
                "for this section's angle the point matters because it frames "
                "the generation trade-off the section goes on to examine"
            ),
        },
    ),
)


_ANALYSIS_BY_MOVE: Dict[str, str] = {
    "implication": (
        "which means it constrains the choices the rest of the analysis "
        "depends on, rather than being an isolated number"
    ),
    "mechanism": (
        "the underlying mechanism is that the earlier conditions compound, "
        "which forces the trade-offs described here"
    ),
    "causal": (
        "that outcome follows from the conditions named above, which is why it "
        "recurs across the evidence rather than standing alone"
    ),
    "tradeoff": (
        "the gain comes at the expense of a competing objective, so it must be "
        "weighed rather than treated as strictly better"
    ),
    "comparison": (
        "that stands out compared with the alternatives discussed elsewhere, "
        "where the relative merits are the deciding factor"
    ),
    "uncertainty": (
        "the claim is disputed across sources, so it should be read as a "
        "provisional finding rather than a settled point"
    ),
    "strategic": (
        "for the decision at hand that changes the role it can play, which "
        "means the repeated figure is decision-relevant"
    ),
    "strength": (
        "the claim is corroborated by multiple independent sources, which "
        "raises confidence in it rather than merely repeating it"
    ),
    "framing": (
        "for this section's angle the point matters because it frames the "
        "trade-off the section goes on to examine"
    ),
}


_NEGATION_RE = re.compile(r"(?i)\b(not|no|never|without|neither|nor|fails? to|does not|did not|is not|are not)\b")


_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")


_BULLET_RE = re.compile(r"^\s*[-*]\s+")


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


_TRIVIAL_RE = re.compile(r"^(?:[-*]\s*)?$")


_MIN_CONTENT_TOKENS = 3


RESTATEMENT_SIMILARITY = 0.78


ANCHOR_OVERLAP_MIN = 0.55


ANCHOR_SHARED_MIN = 2


_GENERIC_ANCHORS: Set[str] = {
    "cost", "costs", "capac", "capita", "energy", "nuclear", "power", "plant",
    "project", "year", "countr", "govern", "invest", "investig", "percent",
    "share", "global", "world", "report", "data", "increase", "higher", "lower",
    "billion", "trillion", "million", "percentag", "point", "source", "sourc",
    "estim", "total", "develop", "developing", "growth", "risk", "risks",
}


_QUANT_RE = re.compile(r"\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\b", re.IGNORECASE)


_COMPARISON_AXIS_RE = re.compile(
    r"(?i)\b(compar|versus|\bvs\b|alternative|how it compares|benchmark|"
    r"trade-?off|weigh)"
)


_MECHANISM_AXIS_RE = re.compile(
    r"(?i)\b(how it works|mechanism|internal|process|architecture|"
    r"how .{0,20}works|technical)"
)


_CAUSAL_AXIS_RE = re.compile(
    r"(?i)\b(causal|cause|why|root|origins?|history|background|drivers?)"
)


_STRATEGIC_AXIS_RE = re.compile(
    r"(?i)\b(strateg|decision|recommend|outlook|policy|future|plan|"
    r"implication|what it means|over \d+ years)"
)


_DECISION_QUERY_RE = re.compile(
    r"(?i)\b(should|recommend|strategy|strategic|policy|policymaker|"
    r"decide|decision|choice|prioriti|over \d+ ?(?:years|decades)|"
    r"government|regulat|plan for|roadmap|most (?:effective|viable|economical))\b"
)


_CAUSAL_QUERY_RE = re.compile(
    r"(?i)\b(cause[ds]?|why|explain (?:what|how)|what led to|what drove|"
    r"origins?|how did .{0,30}happen|reason)\b"
)


_COMPARISON_QUERY_RE = re.compile(
    r"(?i)\b(compare|comparison|versus|\bvs\b|better|which .{0,20}(?:better|"
    r"cheaper|safer)|trade-?off|alternatives?)\b"
)


_MECHANISM_QUERY_RE = re.compile(
    r"(?i)\b(how does|how do|how it works|mechanism|process|work internally|"
    r"explain how)\b"
)


_MECHANISM_CLAIM_RE = re.compile(
    r"(?i)\b(process|mechanism|works? by|functions?|operates?|reacts?|"
    r"converts?|transfers?|pipeline|architecture|algorithm|protocol|"
    r"how .{0,20}works|enables?|allows? .{0,20}to)\b"
)


_CAUSAL_CLAIM_RE = re.compile(
    r"(?i)\b(because|due to|as a result|driven by|caused|led to|triggered|"
    r"stemmed|arose|resulted from)\b"
)


_MOVE_RULES: Tuple[Tuple[str, str], ...] = (
    ("contradicted_comparative", "tradeoff"),
    ("contradicted", "uncertainty"),
    ("decision_query", "strategic"),
    ("comparison_query", "comparison"),
    ("causal_query", "causal"),
    ("mechanism_query", "mechanism"),
    ("comparison_axis", "comparison"),
    ("mechanism_axis", "mechanism"),
    ("causal_axis", "causal"),
    ("strategic_axis", "strategic"),
    ("causal_claim", "causal"),
    ("mechanism_claim", "mechanism"),
    ("comparative_claim", "comparison"),
    ("quantitative_authoritative", "implication"),
    ("quantitative", "tradeoff"),
    ("authoritative", "implication"),
    ("default", "implication"),
)


MAX_APPENDED_WORDS = 38


_FRAGMENT_RE = re.compile(r"^\s*(?:\*\*|[-*]\s|\d+\)\s|[a-z])")


_LABEL_DELIM_RE = re.compile(r"[:—]")


_AUXILIARY_VERBS: Set[str] = {
    "is", "are", "was", "were", "be", "been", "being", "am",
    "has", "have", "had", "do", "does", "did",
    "will", "would", "shall", "should", "can", "could", "may", "might",
    "must", "cannot", "isn't", "aren't", "wasn't", "weren't", "doesn't",
    "don't", "didn't", "hasn't", "haven't", "hadn't",
}


_TRANSITION_MARKERS: Tuple[str, ...] = tuple(
    phrase for phrases in _TRANSITION_BY_MOVE.values() for phrase in phrases
)
