"""
Orchestrator: a plain Python state machine that runs the three-agent
pipeline end to end.

Flow (revised after real-world testing -- see app/agents/validation.py
docstring for why validation now happens BEFORE combining findings, not
after):
  ClaimCase
    -> CaseAnalysisAgent      -> CaseAnalysisOutput
    -> DecisionAgent (evidence gathering + per-dimension assessment)
                              -> CoverageFindings (raw, unvalidated)
    -> ValidationAgent        -> validated CoverageFindings + ValidationResult
                                 (unsupported per-dimension findings are
                                 replaced with an honest "could not verify"
                                 conclusion, not silently dropped)
    -> DecisionAgent (combine) -> FinalDecision, built only from
                                  already-validated findings

This is a deliberately simple, explicit state machine rather than a
LangGraph graph -- chosen for build speed under the 24-hour timeline. The
interfaces are already agent-shaped, so porting to LangGraph later is a
wiring change, not a rewrite.
"""

import time

from app.models import ClaimCase, FinalDecision, TraceEntry, ValidationStatus, DecisionStatus
from app.agents.case_analysis import analyze_case
from app.agents.decision import run_coverage_analysis, make_final_decision
from app.agents.validation import validate_coverage


def analyze_claim(case: ClaimCase) -> FinalDecision:
    trace: list[TraceEntry] = []

    # --- Agent 1: Case Analysis ---
    t0 = time.time()
    analysis = analyze_case(case)
    trace.append(
        TraceEntry(
            agent="CaseAnalysisAgent",
            action="build_investigation_plan",
            detail=f"{len(analysis.investigation_plan)} dimensions identified",
            elapsed_ms=(time.time() - t0) * 1000,
        )
    )

    # --- Agent 2: Decision Agent, evidence-gathering half ---
    coverage = run_coverage_analysis(case, analysis, trace)

    # --- Agent 3: Validation -- audits each dimension finding against its
    # own evidence BEFORE any of them get combined/paraphrased into the
    # final decision. This is the key fix: validating post-hoc against
    # paraphrased key_findings bullets was comparing two differently-worded
    # strings that would never match, causing every decision to be
    # incorrectly flagged as unsupported regardless of actual grounding.
    validated_coverage, validation = validate_coverage(coverage, trace)

    # If EVERY dimension failed validation, there's nothing reliable left
    # to base a decision on -- go straight to NEEDS_REVIEW rather than
    # asking the Decision Agent to combine zero real findings.
    if validation.status == ValidationStatus.FAIL and len(validation.unsupported_claims) == len(coverage.findings):
        trace.append(
            TraceEntry(
                agent="Orchestrator",
                action="all_dimensions_unsupported",
                detail="Every dimension failed validation; abstaining rather than "
                       "combining zero reliable findings.",
            )
        )
        decision = FinalDecision(
            case_id=case.case_id,
            decision=DecisionStatus.NEEDS_REVIEW,
            confidence=0.2,
            key_findings=["Unable to verify sufficient policy evidence for this case."],
            applicable_limits=[],
            missing_evidence=validation.unsupported_claims,
            citations=[],
            validation=validation,
            trace=trace,
        )
        return decision

    # --- Agent 2: Decision Agent, combine half -- built only from findings
    # that passed validation (or an honest "could not verify" placeholder
    # for the ones that didn't) ---
    decision = make_final_decision(case, validated_coverage, trace)
    decision.validation = validation

    # If some (but not all) dimensions were unsupported, that's already
    # reflected in missing_evidence via make_final_decision, and the
    # Decision Agent's own prompt instructs it to lean NEEDS_REVIEW when
    # material evidence is missing. Just make sure confidence reflects the
    # partial validation failure even if the LLM didn't fully account for it.
    if validation.status == ValidationStatus.FAIL:
        decision.confidence = min(decision.confidence, 0.7)

    decision.trace = trace
    return decision