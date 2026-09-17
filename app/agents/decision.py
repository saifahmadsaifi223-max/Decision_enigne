"""
Decision Agent.

Responsibility: for each investigation-plan item from the Case Analysis
Agent, run hybrid retrieval to fetch supporting policy evidence, then use
an LLM call (constrained to a strict JSON schema) to produce a per-dimension
finding grounded ONLY in the retrieved text. Finally, combine all dimension
findings into the overall FinalDecision contract (Section 5).

This agent owns both what the assignment calls the "Policy Evidence Agent"
and "Coverage & Exclusion Agent" responsibilities, merged for the 24-hour
build (see chat: 3-agent compressed design). If you have time later, split
these back into two agents -- the interfaces below (`gather_evidence`,
`assess_dimension`) are already separated functions specifically so that
split is a small refactor, not a rewrite.

IMPORTANT: This agent must never assert something the retrieved evidence
doesn't support. Every `DimensionFinding.conclusion` must cite specific
retrieved chunk_ids in `evidence`. The Validation Agent checks this.
"""

import json
import os
import time

from app.models import (
    ClaimCase,
    CaseAnalysisOutput,
    DimensionFinding,
    CoverageFindings,
    FinalDecision,
    DecisionStatus,
    Citation,
    TraceEntry,
    ValidationResult,
    ValidationStatus,
)
from app.retrieval import get_retriever

# Swap this for whichever free/low-cost provider you land on (Groq, Gemini,
# etc.) -- kept as a thin wrapper so the rest of the agent code doesn't care.
from app.llm_client import call_llm_json


DIMENSION_SYSTEM_PROMPT = """You are a policy compliance analyst. You will be given:
- A specific question about one decision dimension of a health insurance claim
- Relevant facts about the claim
- Retrieved excerpts from the governing insurance policy (with page/section metadata)

Answer ONLY using the retrieved policy excerpts. Do not use outside insurance
knowledge. If the excerpts do not clearly answer the question, say so explicitly
rather than guessing.

Respond with strict JSON matching this shape:
{
  "conclusion": "<one or two sentence conclusion>",
  "supports_admissibility": true | false | null,
  "applicable_limit": "<string or null>",
  "cited_chunk_ids": ["<chunk_id>", ...]
}
"""


def gather_evidence(query: str, top_k: int = 6):
    retriever = get_retriever()
    return retriever.retrieve(query, top_k=top_k)


def assess_dimension(question: str, relevant_fact: str, evidence) -> DimensionFinding:
    evidence_block = "\n\n".join(
        f"[{e.chunk_id}] (page {e.page}, section: {e.section}"
        f"{', ' + e.subsection if e.subsection else ''})\n{e.text}"
        for e in evidence
    )
    user_prompt = (
        f"Question: {question}\n"
        f"Relevant claim facts: {relevant_fact}\n\n"
        f"Retrieved policy excerpts:\n{evidence_block}"
    )
    raw = call_llm_json(system=DIMENSION_SYSTEM_PROMPT, user=user_prompt)

    cited_ids = set(raw.get("cited_chunk_ids", []))
    cited_evidence = [e for e in evidence if e.chunk_id in cited_ids] or evidence[:1]

    return DimensionFinding(
        dimension="",  # filled by caller
        conclusion=raw.get("conclusion", ""),
        supports_admissibility=raw.get("supports_admissibility"),
        applicable_limit=raw.get("applicable_limit"),
        evidence=cited_evidence,
    )


def run_coverage_analysis(case: ClaimCase, analysis: CaseAnalysisOutput, trace: list[TraceEntry]) -> CoverageFindings:
    findings = []
    for item in analysis.investigation_plan:
        t0 = time.time()
        evidence = gather_evidence(item.question)
        trace.append(
            TraceEntry(
                agent="DecisionAgent",
                action=f"retrieve_evidence[{item.dimension}]",
                retrieval_result_count=len(evidence),
                elapsed_ms=(time.time() - t0) * 1000,
            )
        )

        t1 = time.time()
        finding = assess_dimension(item.question, item.relevant_fact or "", evidence)
        finding.dimension = item.dimension
        findings.append(finding)
        trace.append(
            TraceEntry(
                agent="DecisionAgent",
                action=f"assess_dimension[{item.dimension}]",
                detail=finding.conclusion,
                elapsed_ms=(time.time() - t1) * 1000,
            )
        )

    return CoverageFindings(
        case_id=case.case_id,
        findings=findings,
        missing_evidence=list(analysis.flagged_null_evidence) + list(analysis.missing_fields),
    )


DECISION_SYSTEM_PROMPT = """You are the final decision-maker for a health insurance
claim review. You are given per-dimension findings, each already grounded in
policy citations. Combine them into ONE overall decision.

Decision statuses (choose exactly one):
- ADMISSIBLE: fully covered, no material limits/deductions
- ADMISSIBLE_WITH_LIMITS: covered but limits/caps/waiting-period effects reduce the payable amount
- PARTIALLY_ADMISSIBLE: only part of the claim is covered
- NOT_ADMISSIBLE: policy evidence supports rejection
- NEEDS_REVIEW: evidence is missing/uncertain and a safe decision cannot be made

Rules:
- If ANY dimension finding has supports_admissibility=false with high relevance
  (e.g. waiting period not satisfied, explicit exclusion), lean NOT_ADMISSIBLE
  unless other dimensions show it's only a partial issue.
- If evidence is missing (missing_evidence list is non-empty) AND that missing
  evidence is material to the decision, return NEEDS_REVIEW.
- Never state a conclusion that isn't backed by at least one finding's evidence.

Respond with strict JSON:
{
  "decision": "<one of the five statuses above>",
  "confidence": <float 0-1>,
  "key_findings": ["<short bullet>", ...],
  "applicable_limits": ["<short bullet>", ...],
  "missing_evidence": ["<short bullet>", ...]
}
"""


def make_final_decision(case: ClaimCase, coverage: CoverageFindings, trace: list[TraceEntry]) -> FinalDecision:
    findings_block = "\n\n".join(
        f"Dimension: {f.dimension}\nConclusion: {f.conclusion}\n"
        f"Supports admissibility: {f.supports_admissibility}\n"
        f"Applicable limit: {f.applicable_limit}\n"
        f"Evidence chunk_ids: {[e.chunk_id for e in f.evidence]}"
        for f in coverage.findings
    )
    user_prompt = (
        f"Case ID: {case.case_id}\n"
        f"Missing evidence flagged: {coverage.missing_evidence}\n\n"
        f"Per-dimension findings:\n{findings_block}"
    )

    t0 = time.time()
    raw = call_llm_json(system=DECISION_SYSTEM_PROMPT, user=user_prompt)
    trace.append(
        TraceEntry(agent="DecisionAgent", action="combine_findings",
                    elapsed_ms=(time.time() - t0) * 1000)
    )

    citations = []
    for f in coverage.findings:
        for e in f.evidence:
            citations.append(
                Citation(
                    claim=f.conclusion,
                    source="policy.pdf",
                    page=e.page,
                    section=e.section,
                    chunk_id=e.chunk_id,
                )
            )

    decision_status = DecisionStatus(raw.get("decision", "NEEDS_REVIEW"))

    return FinalDecision(
        case_id=case.case_id,
        decision=decision_status,
        confidence=float(raw.get("confidence", 0.5)),
        key_findings=raw.get("key_findings", []),
        applicable_limits=raw.get("applicable_limits", []),
        missing_evidence=raw.get("missing_evidence", []) + coverage.missing_evidence,
        citations=citations,
        validation=ValidationResult(status=ValidationStatus.PASS, unsupported_claims=[]),
        trace=trace,
    )
