"""
Case Analysis Agent.

Responsibility: read the raw ClaimCase, identify which policy decision
dimensions are relevant, flag any explicitly-missing/null evidence, and
produce a structured investigation plan that drives the retrieval queries
used downstream.

DESIGN: hybrid rule-based + LLM fallback, not purely rule-based. The
deterministic rules below cover every dimension the 17 evaluation cases
actually exercise, and stay deterministic (zero LLM cost, zero
hallucination risk) for exactly that reason. But a rule list written
against 17 known cases will not anticipate every real-world claim shape --
a claim mentioning an adventure-sports injury, a war-zone treatment, or an
HIV-related complication (all explicit but less common policy exclusions)
would silently fall through the cracks of a purely rule-based detector,
not because retrieval or reasoning is bad, but because nobody ever asked
the right question in the first place. `detect_additional_dimensions()`
closes this gap with one LLM call that reviews the claim against the
dimensions already identified and proposes anything the rules missed. This
call only ever proposes INVESTIGATION QUESTIONS, never policy answers --
the actual grounded assessment still happens downstream via retrieval +
the Decision Agent, so a bad proposal here wastes a retrieval call at
worst, it cannot inject an unsupported claim into the final decision.
"""

from app.models import ClaimCase, CaseAnalysisOutput, InvestigationItem
from app.llm_client import call_llm_json


FALLBACK_DIMENSION_SYSTEM_PROMPT = """You are an insurance claims triage assistant.
You are given a claim's key facts and a list of policy-decision dimensions
ALREADY being investigated for this claim.

Your ONLY job is to flag whether there are OTHER potentially relevant policy
considerations not already covered -- for example (not exhaustive): adventure
sports injury, war/riot/terrorism, HIV/AIDS-related treatment, self-inflicted
injury or intoxication, alternative/naturopathic/non-allopathic treatment,
outpatient-only treatment, treatment lasting fewer than three days, external
medical equipment used at home, multiple/overlapping insurance policies, or
any other explicit policy consideration suggested by the claim's specific
diagnosis or procedure.

Do NOT decide whether the claim is covered -- only propose additional
investigation QUESTIONS if genuinely warranted by the claim's specific facts.
If the existing dimensions already cover everything relevant, return an
empty list. Do not propose a dimension already in the existing list.

Respond with strict JSON:
{
  "additional_dimensions": [
    {"dimension": "<short_snake_case_name>", "question": "<specific investigation question>", "relevant_fact": "<the case fact that triggered this>"}
  ]
}
"""


def detect_additional_dimensions(case: ClaimCase, existing_dimensions: list[str]) -> list[InvestigationItem]:
    case_summary = (
        f"Diagnosis: {case.treatment.diagnosis}\n"
        f"Procedure: {case.treatment.procedure}\n"
        f"Treatment type: {case.treatment.type}\n"
        f"Pre-existing: {case.treatment.pre_existing}, Experimental: {case.treatment.experimental}\n"
        f"Admission hours: {case.treatment.admission_hours}\n"
        f"Hospital network provider: {case.hospital.network_provider}\n"
        f"Documents supplied: {case.documents}\n"
        f"Task description: {case.task}"
    )
    try:
        raw = call_llm_json(
            system=FALLBACK_DIMENSION_SYSTEM_PROMPT,
            user=f"Claim facts:\n{case_summary}\n\n"
                 f"Dimensions already being investigated: {existing_dimensions}",
        )
    except Exception:
        # Best-effort safety net, not a hard dependency. If this call fails
        # (rate limit, transient network issue), the deterministic rules
        # above still cover every well-understood dimension -- degrade
        # silently rather than fail the whole case analysis over a
        # fallback step whose entire purpose is catching EXTRA edge cases.
        return []

    items = []
    for entry in raw.get("additional_dimensions", []):
        dim = str(entry.get("dimension", "")).strip()
        if not dim or dim in existing_dimensions:
            continue
        items.append(
            InvestigationItem(
                dimension=dim,
                question=entry.get("question", ""),
                relevant_fact=entry.get("relevant_fact"),
            )
        )
    return items


def analyze_case(case: ClaimCase, use_llm_fallback: bool = True) -> CaseAnalysisOutput:
    dimensions: list[str] = []
    plan: list[InvestigationItem] = []
    missing_fields: list[str] = []
    flagged_null_evidence: list[str] = []

    # --- Waiting period (always relevant) ---
    dimensions.append("initial_waiting_period")
    plan.append(
        InvestigationItem(
            dimension="initial_waiting_period",
            question="Does the initial 30-day waiting period apply to this claim, "
                      "given continuous_coverage_months and prior_insurer_continuous_years?",
            relevant_fact=f"continuous_coverage_months={case.continuous_coverage_months}, "
                          f"prior_insurer_continuous_years={case.prior_insurer_continuous_years}",
        )
    )

    # --- Pre-existing disease ---
    if case.treatment.pre_existing:
        dimensions.append("pre_existing_disease")
        plan.append(
            InvestigationItem(
                dimension="pre_existing_disease",
                question="Does the 48-month pre-existing-disease waiting period bar this claim, "
                          "or is it reduced by continuous prior coverage?",
                relevant_fact=f"pre_existing=True, continuous_coverage_months={case.continuous_coverage_months}",
            )
        )

    # --- First-year specific-disease exclusion list ---
    dimensions.append("first_year_disease_exclusion")
    plan.append(
        InvestigationItem(
            dimension="first_year_disease_exclusion",
            question=f"Is '{case.treatment.diagnosis}' / '{case.treatment.procedure}' on the "
                      "policy's first-year exclusion list, and if so has enough time elapsed?",
            relevant_fact=f"diagnosis={case.treatment.diagnosis}, "
                          f"continuous_coverage_months={case.continuous_coverage_months}",
        )
    )

    # --- Domiciliary treatment ---
    if case.treatment.type == "domiciliary":
        dimensions.append("domiciliary_treatment")
        plan.append(
            InvestigationItem(
                dimension="domiciliary_treatment",
                question="Does this domiciliary treatment satisfy the policy's qualifying "
                          "conditions, and what sub-limit applies?",
                relevant_fact=f"hospital_room_unavailable={case.treatment.hospital_room_unavailable}, "
                              f"patient_cannot_be_moved={case.treatment.patient_cannot_be_moved}",
            )
        )

    # --- Day care / less-than-24-hour treatment ---
    if case.treatment.type == "day_care" or case.treatment.admission_hours < 24:
        dimensions.append("day_care_treatment")
        plan.append(
            InvestigationItem(
                dimension="day_care_treatment",
                question=f"Does '{case.treatment.procedure}' qualify as covered day-care "
                          "treatment despite being under 24 hours?",
                relevant_fact=f"admission_hours={case.treatment.admission_hours}, "
                              f"procedure={case.treatment.procedure}",
            )
        )

    # --- Experimental / unproven treatment ---
    if case.treatment.experimental:
        dimensions.append("experimental_treatment")
        plan.append(
            InvestigationItem(
                dimension="experimental_treatment",
                question="Is this treatment excluded as experimental/unproven under the policy?",
                relevant_fact=f"experimental=True, procedure={case.treatment.procedure}",
            )
        )

    # --- Sub-limits / category caps (relevant whenever there's a payable amount) ---
    dimensions.append("sub_limits")
    plan.append(
        InvestigationItem(
            dimension="sub_limits",
            question="What room-rent, doctor-fee, and category-expense sub-limits apply, "
                      "and do the claimed expenses exceed them?",
            relevant_fact=f"expenses_total={case.expenses_inr.total}, sum_insured={case.sum_insured_inr}",
        )
    )

    # --- Pre/post hospitalization windows ---
    if case.expense_timing is not None:
        dimensions.append("pre_post_hospitalization_window")
        plan.append(
            InvestigationItem(
                dimension="pre_post_hospitalization_window",
                question="Do the pre/post-hospitalization expenses fall within the policy's "
                          "30-day/60-day windows?",
                relevant_fact=str(case.expense_timing.model_dump()),
            )
        )

    # --- Portability / prior insurer continuity ---
    if case.prior_policy is not None or case.prior_insurer_continuous_years > 0:
        dimensions.append("portability_continuity")
        plan.append(
            InvestigationItem(
                dimension="portability_continuity",
                question="Does continuous prior coverage with another insurer reduce or "
                          "waive any waiting periods for this claim?",
                relevant_fact=str(case.prior_policy.model_dump()) if case.prior_policy else
                              f"prior_insurer_continuous_years={case.prior_insurer_continuous_years}",
            )
        )

    # --- Explicitly excluded treatment categories (keyword heuristic) ---
    # These map to flat exclusions in "What We Exclude" (p.9) that have NO
    # injury/accident carve-out in the policy text -- unlike, say,
    # circumcision or cosmetic surgery, which ARE covered when required by
    # an accidental injury. Dental treatment (item 7) is excluded "of any
    # kind" with no such carve-out, which is a genuinely citable nuance
    # worth an agent catching even when dental treatment appears alongside
    # an otherwise-covered accident claim (see CUST-001).
    exclusion_keywords = {
        "cosmetic": "cosmetic_exclusion",
        "plastic": "cosmetic_exclusion",
        "dental": "dental_exclusion",
        "pregnancy": "pregnancy_exclusion",
        "childbirth": "pregnancy_exclusion",
        "maternity": "pregnancy_exclusion",
    }
    matched_dims = set()
    for kw, dim in exclusion_keywords.items():
        if kw in case.treatment.diagnosis.lower() or kw in case.treatment.procedure.lower():
            matched_dims.add(dim)
    for dim in matched_dims:
        dimensions.append(dim)
        plan.append(
            InvestigationItem(
                dimension=dim,
                question=f"Is '{case.treatment.procedure}' (diagnosis: '{case.treatment.diagnosis}') "
                          f"excluded under the policy's exclusion list, and does any injury/accident "
                          f"carve-out apply?",
                relevant_fact=f"diagnosis={case.treatment.diagnosis}, procedure={case.treatment.procedure}",
            )
        )

    # --- Hospital definition / medical necessity doubt ---
    # Each evidence_context field is investigated as its OWN dimension,
    # scoped strictly to fields the case actually provided (exclude_unset),
    # not fields that are merely absent/defaulted. This distinction matters:
    # an earlier version of this function checked the resolved attribute
    # value directly (e.g. `ec.medical_necessity_confirmed is None`), which
    # can't tell "explicitly flagged as unknown in this case" apart from
    # "this case never mentioned this field at all" -- both resolve to
    # None on the Pydantic model. That caused PUB-011 (which only ever
    # raises a Hospital-definition doubt) to also spuriously investigate
    # medical necessity, an ambiguity that case was never designed to
    # test. Using exclude_unset=True fixes this by only considering
    # fields present in the source JSON.
    if case.evidence_context is not None:
        ec = case.evidence_context
        ec_provided = ec.model_dump(exclude_unset=True)

        for field_name, value in ec_provided.items():
            if value is None:
                flagged_null_evidence.append(field_name)

        hospital_fields_provided = {"hospital_registered", "hospital_minimum_criteria_documented"} & ec_provided.keys()
        hospital_doubt = any(ec_provided[f] in (None, False) for f in hospital_fields_provided)
        if hospital_doubt:
            dimensions.append("hospital_definition")
            plan.append(
                InvestigationItem(
                    dimension="hospital_definition",
                    question="Does the treating facility meet the policy's definition of 'Hospital'?",
                    relevant_fact=f"network_provider={case.hospital.network_provider}, "
                                  f"hospital_registered={ec.hospital_registered}, "
                                  f"hospital_minimum_criteria_documented={ec.hospital_minimum_criteria_documented}",
                )
            )

        if "medical_necessity_confirmed" in ec_provided and ec_provided["medical_necessity_confirmed"] in (None, False):
            dimensions.append("medical_necessity")
            plan.append(
                InvestigationItem(
                    dimension="medical_necessity",
                    question="Is there sufficient evidence that this hospitalization/treatment was "
                              "medically necessary, per the policy's definition of 'Medically Necessary'?",
                    relevant_fact=f"medical_necessity_confirmed={ec.medical_necessity_confirmed}, "
                                  f"documents={case.documents}",
                )
            )

    # --- Missing documents heuristic ---
    expected_docs = {"claim_form", "discharge_summary", "itemized_bill"}
    if case.treatment.type == "inpatient":
        missing = expected_docs - set(case.documents)
        if missing:
            missing_fields.extend(sorted(missing))

    # --- LLM fallback: catch dimensions the deterministic rules above
    # didn't anticipate (see module docstring for why this exists). This
    # is the only LLM call in this agent, and it can only ADD investigation
    # questions -- it never removes or overrides the deterministic rules.
    if use_llm_fallback:
        additional_items = detect_additional_dimensions(case, dimensions)
        for item in additional_items:
            dimensions.append(item.dimension)
            plan.append(item)

    return CaseAnalysisOutput(
        case_id=case.case_id,
        decision_dimensions=dimensions,
        investigation_plan=plan,
        missing_fields=missing_fields,
        flagged_null_evidence=flagged_null_evidence,
    )


