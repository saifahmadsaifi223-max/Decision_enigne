"""
Reproducible end-to-end evaluation script (Section 9).

Usage:
    python -m eval.evaluate

Runs every case in data/public_test_cases.json AND eval/custom_cases.json
through the orchestrator, compares against the ground-truth expected
decisions (see expected_outcomes_ground_truth.md for how these were
established from the actual policy text), and reports:
  - decision accuracy (overall + per-case)
  - citation presence rate (does every non-abstained decision have >=1 citation)
  - validation pass rate
  - abstention count (must be >= 2 per assignment requirement)

Writes results to eval/results.json and prints a summary table.
"""

import json
import time
from pathlib import Path

from app.models import ClaimCase
from app.orchestrator import analyze_claim

PUBLIC_CASES_PATH = Path("data/public_test_cases.json")
CUSTOM_CASES_PATH = Path("eval/custom_cases.json")
RESULTS_PATH = Path("eval/results.json")

# Ground truth established in expected_outcomes_ground_truth.md by manually
# tracing each case against the actual policy clauses (page/section cited
# there). Keep this in sync if you add/modify custom cases.
EXPECTED_DECISIONS = {
    "PUB-001": "ADMISSIBLE_WITH_LIMITS",
    "PUB-002": "NOT_ADMISSIBLE",
    "PUB-003": "NOT_ADMISSIBLE",
    "PUB-004": "ADMISSIBLE_WITH_LIMITS",
    "PUB-005": "ADMISSIBLE",
    "PUB-006": "NEEDS_REVIEW",
    "PUB-007": "ADMISSIBLE_WITH_LIMITS",
    "PUB-008": "NOT_ADMISSIBLE",
    "PUB-009": "ADMISSIBLE_WITH_LIMITS",
    "PUB-010": "ADMISSIBLE",
    "PUB-011": "NEEDS_REVIEW",
    "PUB-012": "NOT_ADMISSIBLE",
    # Add your 5 custom case_ids -> expected decisions here once written,
    # e.g. "CUST-001": "PARTIALLY_ADMISSIBLE",
}


def load_cases() -> list[dict]:
    cases = json.loads(PUBLIC_CASES_PATH.read_text())
    if CUSTOM_CASES_PATH.exists():
        cases += json.loads(CUSTOM_CASES_PATH.read_text())
    return cases


def run_evaluation():
    raw_cases = load_cases()
    results = []

    for raw_case in raw_cases:
        case = ClaimCase(**raw_case)
        expected = EXPECTED_DECISIONS.get(case.case_id)

        t0 = time.time()
        try:
            decision = analyze_claim(case)
            elapsed = time.time() - t0
            correct = (expected is not None) and (decision.decision.value == expected)
            has_citations = len(decision.citations) > 0
            results.append(
                {
                    "case_id": case.case_id,
                    "expected": expected,
                    "actual": decision.decision.value,
                    "correct": correct,
                    "confidence": decision.confidence,
                    "has_citations": has_citations,
                    "citation_count": len(decision.citations),
                    "validation_status": decision.validation.status.value,
                    "elapsed_sec": round(elapsed, 2),
                }
            )
        except Exception as e:  # noqa: BLE001
            results.append(
                {
                    "case_id": case.case_id,
                    "expected": expected,
                    "actual": "ERROR",
                    "correct": False,
                    "error": str(e),
                    "elapsed_sec": round(time.time() - t0, 2),
                }
            )

    # --- Aggregate metrics ---
    scored = [r for r in results if r.get("expected") is not None]
    accuracy = sum(r["correct"] for r in scored) / len(scored) if scored else 0.0
    citation_rate = sum(r.get("has_citations", False) for r in results) / len(results)
    validation_pass_rate = sum(
        r.get("validation_status") == "PASS" for r in results
    ) / len(results)
    abstention_count = sum(r["actual"] == "NEEDS_REVIEW" for r in results)

    summary = {
        "total_cases": len(results),
        "scored_cases": len(scored),
        "decision_accuracy": round(accuracy, 3),
        "citation_presence_rate": round(citation_rate, 3),
        "validation_pass_rate": round(validation_pass_rate, 3),
        "abstention_count": abstention_count,
        "abstention_requirement_met": abstention_count >= 2,
    }

    output = {"summary": summary, "per_case": results}
    RESULTS_PATH.write_text(json.dumps(output, indent=2))

    print("\n=== EVALUATION SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    print("\n=== PER-CASE RESULTS ===")
    for r in results:
        mark = "✓" if r.get("correct") else ("?" if r.get("expected") is None else "✗")
        print(f"  [{mark}] {r['case_id']}: expected={r.get('expected')} actual={r.get('actual')} "
              f"citations={r.get('citation_count', 0)} validation={r.get('validation_status')}")

    print(f"\nFull results written to {RESULTS_PATH}")
    return output


if __name__ == "__main__":
    run_evaluation()
