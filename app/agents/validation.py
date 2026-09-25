"""
Validation Agent.

Responsibility: independently check that every per-dimension finding is
actually supported by ITS OWN retrieved evidence, BEFORE those findings
are combined into the final decision.

IMPORTANT DESIGN NOTE (fixed after real-world testing): validation must
happen at the DimensionFinding level, not after the Decision Agent has
already paraphrased findings into short `key_findings` bullets. The
Decision Agent's combine step naturally rewords things (e.g. a finding's
full conclusion "The 30-day waiting period does not apply because..."
becomes the shorter bullet "30-day waiting period satisfied due to
continuous coverage"). A paraphrase is never a substring of the sentence
it paraphrases, so attempting to match key_findings back to citations by
string containment always fails -- this produced a bug where EVERY
decision was incorrectly flagged as unsupported, regardless of whether it
was actually well-grounded. Validating each DimensionFinding against its
own `evidence` field (set directly by the Decision Agent, before any
paraphrasing) avoids this entirely: the pairing is exact and unambiguous.
"""

import time

from app.models import CoverageFindings, DimensionFinding, ValidationResult, ValidationStatus, TraceEntry
from app.llm_client import call_llm_json


VALIDATION_SYSTEM_PROMPT = """You are a strict fact-checker. You will be given a
conclusion statement and one or more retrieved policy excerpts. The excerpts
were retrieved for this dimension but may include some that turn out to be
irrelevant noise -- retrieval is not perfectly precise.

Determine: does AT LEAST ONE of the excerpts directly support the conclusion?
You do NOT need every excerpt to be relevant or supportive -- ignore excerpts
that are irrelevant, and only check whether the conclusion is backed by the
excerpt(s) that DO relate to it. Be strict about that supporting excerpt itself
-- if the conclusion adds specifics (numbers, conditions) not present in it,
or contradicts it, mark it unsupported.

A conclusion that HONESTLY ADMITS uncertainty or says evidence is inconclusive
should be marked SUPPORTED, as long as that admission is an accurate reading of
the excerpts. Example: conclusion "The breakdown of expenses by category is not
provided, so it is unclear whether the sub-limit is exceeded" is SUPPORTED if
an excerpt states a sub-limit but the claim facts genuinely don't break down
expenses by category -- this is an honest, correct statement of uncertainty,
not an unsupported claim. Do not penalize a conclusion for admitting it cannot
reach a definite answer; only penalize it for asserting something definite that
no excerpt actually establishes.

A conclusion that performs SIMPLE, CORRECT ARITHMETIC on a rule explicitly
stated in an excerpt should also be marked SUPPORTED, even if the excerpt only
states a percentage/formula rather than a precomputed rupee figure. Example: if
an excerpt says "Normal Room expenses: 1.0% of Basic Sum Insured" and the
conclusion states "this caps room rent at Rs. 5,000" for a claim with a
Rs. 500,000 Sum Insured, that IS supported -- 1% of 500,000 is correctly 5,000.
This is not a hallucinated fact; it is a correct derivation from an explicitly
stated rule. Only mark such a derivation unsupported if the arithmetic itself
is wrong, or if it relies on a percentage/number NOT actually present in any
excerpt.

Respond with strict JSON:
{ "supported": true | false, "reason": "<one sentence>" }
"""


def _validate_finding(finding: DimensionFinding) -> bool:
    if not finding.evidence:
        # A conclusion with zero backing evidence can never be "supported" --
        # this usually means retrieval came back empty for this dimension.
        return False

    # Fast-path, no LLM call: a finding that already honestly reports
    # supports_admissibility=None is, by construction, an admission of
    # uncertainty rather than a definite claim -- there is nothing to
    # hallucinate here, since the Decision Agent itself already said "I
    # can't determine this either way." Relying on a second LLM call to
    # correctly apply the "honest uncertainty is fine" rule from the
    # validation prompt proved inconsistent across repeated tests (see
    # FAILURE_ANALYSIS.md, Failures 6-7) -- the validation model would
    # sometimes flag an admitted-uncertain conclusion as unsupported
    # anyway, despite explicit prompt instructions and worked examples not
    # to. Rather than continuing to tune the prompt and hope the model
    # follows it every time, this class of finding is made deterministically
    # safe in code: an inconclusive finding cannot be "wrong" in the sense
    # validation cares about, so it always passes without needing an LLM
    # to agree.
    if finding.supports_admissibility is None:
        return True

    evidence_block = "\n\n".join(
        f"[{e.chunk_id}] (page {e.page}, section: {e.section})\n{e.text}"
        for e in finding.evidence
    )
    result = call_llm_json(
        system=VALIDATION_SYSTEM_PROMPT,
        user=f"Conclusion: {finding.conclusion}\n\nPolicy excerpt(s):\n{evidence_block}",
    )
    return bool(result.get("supported", False))


def validate_coverage(coverage: CoverageFindings, trace: list[TraceEntry]) -> tuple[CoverageFindings, ValidationResult]:
    """Validates each dimension finding against its own evidence. Findings
    that fail are REPLACED with an honest 'could not verify' conclusion
    (rather than silently dropped) so the Decision Agent still sees that
    this dimension was investigated but is unresolved -- this naturally
    pushes the final decision toward NEEDS_REVIEW when enough dimensions
    fail, without the orchestrator needing separate special-case logic.
    """
    unsupported = []
    validated_findings = []

    for finding in coverage.findings:
        t0 = time.time()
        supported = _validate_finding(finding)
        trace.append(
            TraceEntry(
                agent="ValidationAgent",
                action=f"check_claim_support[{finding.dimension}]",
                detail=finding.conclusion[:100],
                elapsed_ms=(time.time() - t0) * 1000,
            )
        )

        if supported:
            validated_findings.append(finding)
        else:
            unsupported.append(f"{finding.dimension}: {finding.conclusion}")
            validated_findings.append(
                DimensionFinding(
                    dimension=finding.dimension,
                    conclusion="Could not verify this dimension against the retrieved "
                               "policy text with sufficient confidence.",
                    supports_admissibility=None,
                    applicable_limit=None,
                    evidence=finding.evidence,  # keep evidence for audit trail
                )
            )

    status = ValidationStatus.FAIL if unsupported else ValidationStatus.PASS
    trace.append(
        TraceEntry(
            agent="ValidationAgent",
            action="final_validation_status",
            detail=f"{status.value} ({len(unsupported)}/{len(coverage.findings)} dimensions unsupported)",
        )
    )

    validated_coverage = CoverageFindings(
        case_id=coverage.case_id,
        findings=validated_findings,
        missing_evidence=coverage.missing_evidence,
    )
    return validated_coverage, ValidationResult(status=status, unsupported_claims=unsupported)
