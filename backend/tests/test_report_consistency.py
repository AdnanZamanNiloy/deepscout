"""One evidence conclusion, obeyed by every section of the report.

The failure these tests pin: the evidence review concludes no defensible #1
exists, the Executive Summary says so, and then another section names a "leading"
option anyway. Each section is written from its own prompt, and the per-section
prompt did not carry the report-wide contract — only the Executive Summary prompt
did.

The report must be a faithful SYNTHESIS of the evidence review, not a second
interpretation of the evidence.

No test asserts on a real subject; the stand-ins exist to make the assertions
concrete, not because the code knows them.
"""

import re
import tokenize
from io import StringIO

from app.agents.report_consistency import (
    STATUS_CLUSTER,
    STATUS_NO_NUMBER_ONE,
    STATUS_OPEN,
    STATUS_RANKED,
    assess_report_consistency,
    ranking_language_without_basis,
    render_consistency_contract,
    report_status,
    section_violations,
)

CONVERGED = {
    "identified": True,
    "reason": "no source provides a ranking",
    "missing_evidence": "a source that ranks the candidates on a common measure",
}
CLUSTER = ["role one", "role two"]


def _no_number_one_status():
    return report_status("q", convergence=CONVERGED, cluster=CLUSTER)


# ---------------------------------------------------------------------------
# The report status
# ---------------------------------------------------------------------------


def test_convergence_yields_no_number_one_and_forbids_ranking():
    status = _no_number_one_status()
    assert status.status == STATUS_NO_NUMBER_ONE
    assert status.forbids_ranking is True
    assert status.allowed_ranking is False
    assert status.cluster == CLUSTER


def test_a_shortlist_basis_yields_a_cluster_status():
    status = report_status("q", ranking_basis={"verdict": "shortlist"}, cluster=CLUSTER)
    assert status.status == STATUS_CLUSTER
    assert status.forbids_ranking is True


def test_a_ranked_basis_permits_ranking_language():
    status = report_status("q", ranking_basis={"verdict": "ranked"})
    assert status.status == STATUS_RANKED
    assert status.allowed_ranking is True
    assert status.forbids_ranking is False
    # And no contract is emitted when ranking is allowed.
    assert render_consistency_contract(status) == ""


def test_an_unresolved_run_is_open():
    assert report_status("q").status == STATUS_OPEN


def test_convergence_outranks_the_ranking_basis():
    """The loop's conclusion governs, whatever the basis assessment saw."""
    status = report_status(
        "q", convergence=CONVERGED, ranking_basis={"verdict": "ranked"}
    )
    assert status.status == STATUS_NO_NUMBER_ONE
    assert status.forbids_ranking is True


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


def test_the_contract_forbids_ranking_words_everywhere():
    contract = render_consistency_contract(_no_number_one_status()).lower()
    assert "every section" in contract
    for word in ("top", "highest", "leading", "strongest", "ranks first"):
        assert word in contract
    assert "do not use ranking language" in contract


def test_the_contract_names_the_cluster_wording():
    contract = render_consistency_contract(_no_number_one_status())
    assert "supported cluster" in contract.lower()
    assert "role one" in contract and "role two" in contract
    assert "never a ranking" in contract.lower()


def test_the_contract_requires_explaining_why_it_cannot_be_ranked():
    contract = render_consistency_contract(_no_number_one_status()).lower()
    assert "why it cannot be ranked" in contract
    assert "no source compares them" in contract


def test_the_contract_names_what_would_settle_it():
    contract = render_consistency_contract(_no_number_one_status())
    assert "what would settle it" in contract.lower()
    assert "a source that ranks the candidates" in contract


def test_the_contract_forbids_off_question_filler():
    contract = render_consistency_contract(_no_number_one_status()).lower()
    assert "stay on the question" in contract
    assert "unrelated mechanisms, sectors" in contract
    assert "empty rather than filled" in contract


def test_no_contract_when_ranking_is_allowed():
    assert render_consistency_contract(report_status("q")) == ""


# ---------------------------------------------------------------------------
# Detecting ranking language
# ---------------------------------------------------------------------------


def test_ranking_language_is_flagged_without_a_basis():
    status = _no_number_one_status()
    for text in (
        "Nursing sits at the top of the ranking.",
        "Nursing is the leading candidate.",
        "This role has the highest strain.",
        "Nursing is the strongest option.",
    ):
        assert ranking_language_without_basis(text, status), text


def test_stating_the_conclusion_is_not_a_violation():
    """A report must be able to SAY there is no #1 without being flagged."""
    status = _no_number_one_status()
    for text in (
        "No single #1 can be established.",
        "No source provides a ranking.",
        "This report cannot rank the candidates.",
        "There is no clear leading candidate.",
        "#1 is not established by any source.",
        "The supported cluster includes nursing and midwifery.",
    ):
        assert ranking_language_without_basis(text, status) == [], text


def test_ranking_language_is_allowed_when_a_basis_exists():
    status = report_status("q", ranking_basis={"verdict": "ranked"})
    assert ranking_language_without_basis("Nursing ranks first.", status) == []


# ---------------------------------------------------------------------------
# Per-section propagation — the reported failure
# ---------------------------------------------------------------------------


def test_a_violation_names_the_section_that_broke_consistency():
    status = _no_number_one_status()
    sections = {
        "Executive Summary": "No single #1 can be established from this evidence.",
        "Evidence & Data": "The data support a supported cluster of roles.",
        "Outlook": "Nursing leads and sits at the top of the ranking.",
        "How It Works": "The mechanism behind this is unrelated filler.",
    }
    violations = section_violations(sections, status)
    assert "Outlook" in violations, violations
    # The sections that obeyed the conclusion are NOT reported.
    assert "Executive Summary" not in violations
    assert "Evidence & Data" not in violations


def test_the_summary_obeying_while_another_section_does_not_is_detected():
    """Exactly the reported inconsistency."""
    status = _no_number_one_status()
    sections = {
        "Executive Summary": "No defensible #1 exists; no source ranks these.",
        "Outlook": "Nursing is the top choice for 2027.",
    }
    violations = section_violations(sections, status)
    assert list(violations) == ["Outlook"]


def test_a_fully_consistent_report_has_no_violations():
    status = _no_number_one_status()
    sections = {
        "Executive Summary": (
            "No single #1 can be established. The supported cluster is role one "
            "and role two; no source compares them, so they cannot be ranked. A "
            "ranking source would settle it."
        ),
        "Evidence & Data": "The evidence describes these roles as a cluster.",
        "Outlook": "Without a comparative measure, no ordering is possible.",
    }
    assert section_violations(sections, status) == {}


def test_the_full_assessment_reports_status_and_violations():
    report = assess_report_consistency(
        "q",
        {"Executive Summary": "No #1 exists.", "Outlook": "Role one leads."},
        convergence=CONVERGED,
        cluster=CLUSTER,
    )
    assert report["status"]["status"] == STATUS_NO_NUMBER_ONE
    assert "Outlook" in report["violations"]
    assert "Executive Summary" not in report["violations"]


def test_assessment_is_total_on_garbage():
    for query, sections, conv in (
        ("", {}, None),
        ("q", {}, None),
        ("q", {"S": None}, {}),
        (None, {"S": "x"}, {"identified": True}),
    ):
        report = assess_report_consistency(query, sections, convergence=conv)
        assert isinstance(report, dict)
        assert "status" in report


# ---------------------------------------------------------------------------
# The instruction reaches every writer path
# ---------------------------------------------------------------------------


def test_the_per_section_prompt_carries_the_contract():
    """The per-section prompt omitted it, which caused the inconsistency.

    The Executive Summary prompt included the report-wide hint; the per-section
    prompt used only its own length hint, so sections never saw the conclusion.
    """
    import inspect

    from app.agents import synthesizer

    source = inspect.getsource(synthesizer._synthesize_sectioned)
    writer = source[source.index("def _write_section"):]
    assert "length_hint" in writer, "per-section prompt missing the report-wide contract"


def test_the_synthesizer_builds_the_consistency_contract():
    import inspect

    from app.agents import synthesizer

    source = inspect.getsource(synthesizer.synthesize)
    assert "render_consistency_contract" in source
    assert "report_status" in source


def test_the_consistency_audit_runs():
    import inspect

    from app.agents import synthesizer

    source = inspect.getsource(synthesizer._finalize)
    assert "assess_report_consistency" in source
    assert "consistency_audit" in source


def test_the_audits_reach_the_caller():
    from app.agents.synthesizer import _mirror_machine_notes

    outer = {}
    _mirror_machine_notes(
        {"consistency_audit": {"a": 1}, "report_status": {"b": 2}}, outer
    )
    assert outer.get("consistency_audit")
    assert outer.get("report_status")


def test_the_consistency_module_names_no_subject():
    """Domain agnosticism: the rules are about ranking language, not topics."""
    import ast

    source = open("app/agents/report_consistency.py").read()
    tree = ast.parse(source)
    docstring_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if ast.get_docstring(node, clean=False) is not None:
                body = node.body[0]
                docstring_lines.update(range(body.lineno, (body.end_lineno or 0) + 1))

    topics = ("nursing", "midwifery", "demanding", "job", "seafaring",
              "obstetrics", "strain", "ai", "cybersecurity", "finance")
    offenders = []
    for tok in tokenize.generate_tokens(StringIO(source).readline):
        if tok.type != tokenize.STRING or tok.start[0] in docstring_lines:
            continue
        for topic in topics:
            if re.search(rf"\b{topic}\b", tok.string, re.IGNORECASE):
                offenders.append((tok.start[0], topic))
    assert not offenders, f"executable subject strings in report_consistency.py: {offenders}"
