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
conclusion statement and the exact policy excerpt(s) it is supposed to be based on.

Determine: do the excerpts actually, directly support the conclusion? Be strict --
if the conclusion adds specifics (numbers, conditions) not present in the excerpts,
or contradicts them, mark it unsupported. A conclusion that says evidence is
missing/inconclusive should be marked supported as long as that's an honest
reading of the excerpts (i.e. it's fine for a conclusion to admit uncertainty).

Respond with strict JSON:
{ "supported": true | false, "reason": "<one sentence>" }
"""


def _validate_finding(finding: DimensionFinding) -> bool:
    if not finding.evidence:
        # A conclusion with zero backing evidence can never be "supported" --
        # this usually means retrieval came back empty for this dimension.
        return False

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

