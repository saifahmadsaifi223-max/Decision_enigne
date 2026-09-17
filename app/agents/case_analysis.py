"""
Case Analysis Agent.

Responsibility: read the raw ClaimCase, identify which policy decision
dimensions are relevant, flag any explicitly-missing/null evidence, and
produce a structured investigation plan that drives the retrieval queries
used downstream. This agent does NOT touch the policy text at all --
it only reasons over the case JSON.

This is intentionally rule-based rather than an LLM call: the decision
dimensions map fairly directly and deterministically off case fields
(e.g. `treatment.experimental == True` -> always investigate the
experimental-treatment dimension). Keeping this deterministic where
possible reduces hallucination risk and makes the pipeline cheaper/faster;
save the LLM calls for the genuinely open-ended reasoning steps
(Decision Agent, Validation Agent).
"""

from app.models import ClaimCase, CaseAnalysisOutput, InvestigationItem


def analyze_case(case: ClaimCase) -> CaseAnalysisOutput:
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

    # --- Cosmetic / clearly-excluded-category treatment (keyword heuristic) ---
    exclusion_keywords = ["cosmetic", "plastic"]
    if any(kw in case.treatment.diagnosis.lower() or kw in case.treatment.procedure.lower()
           for kw in exclusion_keywords):
        dimensions.append("cosmetic_exclusion")
        plan.append(
            InvestigationItem(
                dimension="cosmetic_exclusion",
                question=f"Is '{case.treatment.procedure}' excluded as cosmetic/aesthetic treatment?",
                relevant_fact=f"diagnosis={case.treatment.diagnosis}",
            )
        )

    # --- Hospital definition (only relevant when there's doubt) ---
    if case.evidence_context is not None:
        dimensions.append("hospital_definition")
        plan.append(
            InvestigationItem(
                dimension="hospital_definition",
                question="Does the treating facility meet the policy's definition of 'Hospital'?",
                relevant_fact=f"network_provider={case.hospital.network_provider}, "
                              f"evidence_context={case.evidence_context.model_dump()}",
            )
        )
        # Explicitly flag any null fields -- these are deliberate "unknown"
        # signals in the supplied data, not absent/False.
        for field_name, value in case.evidence_context.model_dump().items():
            if value is None:
                flagged_null_evidence.append(field_name)

    # --- Missing documents heuristic ---
    expected_docs = {"claim_form", "discharge_summary", "itemized_bill"}
    if case.treatment.type == "inpatient":
        missing = expected_docs - set(case.documents)
        if missing:
            missing_fields.extend(sorted(missing))

    return CaseAnalysisOutput(
        case_id=case.case_id,
        decision_dimensions=dimensions,
        investigation_plan=plan,
        missing_fields=missing_fields,
        flagged_null_evidence=flagged_null_evidence,
    )
