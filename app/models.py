"""
Pydantic data models for the claim decision engine.

Two families of models:
1. Input models — mirror the structure of public_test_cases.json exactly,
   so cases load without transformation.
2. Output/contract models — match Section 5 of the assignment
   ("Decision and Evidence Contract"), plus the internal structured state
   that agents pass to each other (Section 4.3: "agents should exchange
   structured state rather than only free-form text").
"""

from __future__ import annotations
from datetime import date
from enum import Enum
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# INPUT MODELS — mirror public_test_cases.json
# ---------------------------------------------------------------------------

class Patient(BaseModel):
    age: int


class Hospital(BaseModel):
    name: str
    network_provider: bool


class Treatment(BaseModel):
    type: str  # "inpatient" | "domiciliary" | "day_care"
    admission_hours: int
    diagnosis: str
    procedure: str
    pre_existing: bool = False
    experimental: bool = False
    # Only present for domiciliary cases:
    hospital_room_unavailable: Optional[bool] = None
    patient_cannot_be_moved: Optional[bool] = None


class Expenses(BaseModel):
    room: float = 0
    doctor_fees: float = 0
    medicines_diagnostics: float = 0
    pre_hospitalization: float = 0
    post_hospitalization: float = 0
    ambulance: float = 0

    @property
    def total(self) -> float:
        return (
            self.room
            + self.doctor_fees
            + self.medicines_diagnostics
            + self.pre_hospitalization
            + self.post_hospitalization
            + self.ambulance
        )


class PriorPolicy(BaseModel):
    insurer_type: Optional[str] = None
    continuous_years: Optional[int] = None
    database_and_claim_history_received: Optional[bool] = None
    previous_sum_insured_inr: Optional[float] = None


class ExpenseTiming(BaseModel):
    pre_hospitalization_days_before_admission: Optional[int] = None
    post_hospitalization_days_after_discharge: Optional[int] = None
    same_condition_confirmed: Optional[bool] = None


class EvidenceContext(BaseModel):
    """Fields here are deliberately Optional[bool] so that `null` in the
    supplied JSON is distinguishable from an unset field entirely — a
    `null` is a signal that this fact is unknown and must be surfaced as
    missing_evidence, not silently treated as False."""
    hospital_registered: Optional[bool] = None
    medical_necessity_confirmed: Optional[bool] = None
    hospital_minimum_criteria_documented: Optional[bool] = None


class ClaimCase(BaseModel):
    case_id: str
    policy_id: str
    policy_start_date: date
    claim_date: date
    sum_insured_inr: float
    continuous_coverage_months: int
    prior_insurer_continuous_years: int = 0
    patient: Patient
    hospital: Hospital
    treatment: Treatment
    expenses_inr: Expenses
    documents: List[str] = Field(default_factory=list)
    task: str
    # Optional blocks that only appear in some cases:
    evidence_context: Optional[EvidenceContext] = None
    expense_timing: Optional[ExpenseTiming] = None
    prior_policy: Optional[PriorPolicy] = None


# ---------------------------------------------------------------------------
# RETRIEVAL / EVIDENCE MODELS
# ---------------------------------------------------------------------------

class PolicyChunk(BaseModel):
    """A single indexed unit of the policy document."""
    chunk_id: str
    text: str
    page: int
    section: str
    subsection: Optional[str] = None  # e.g. a specific defined term or exclusion item number


class RetrievedEvidence(BaseModel):
    chunk_id: str
    text: str
    page: int
    section: str
    subsection: Optional[str] = None
    dense_score: Optional[float] = None
    sparse_score: Optional[float] = None
    fused_score: Optional[float] = None
    rerank_score: Optional[float] = None


class Citation(BaseModel):
    claim: str
    source: str = "policy.pdf"
    page: int
    section: str
    chunk_id: Optional[str] = None


# ---------------------------------------------------------------------------
# DECISION STATUSES (Section 6)
# ---------------------------------------------------------------------------

class DecisionStatus(str, Enum):
    ADMISSIBLE = "ADMISSIBLE"
    ADMISSIBLE_WITH_LIMITS = "ADMISSIBLE_WITH_LIMITS"
    PARTIALLY_ADMISSIBLE = "PARTIALLY_ADMISSIBLE"
    NOT_ADMISSIBLE = "NOT_ADMISSIBLE"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class ValidationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


# ---------------------------------------------------------------------------
# INTER-AGENT STRUCTURED STATE
# ---------------------------------------------------------------------------

class InvestigationItem(BaseModel):
    """One decision dimension the Case Analysis Agent wants investigated,
    e.g. 'waiting_period', 'pre_existing_disease', 'sub_limits'."""
    dimension: str
    question: str  # natural-language question to drive retrieval, e.g.
                    # "Does a 30-day initial waiting period apply here?"
    relevant_fact: Optional[str] = None  # the case fact that triggered this item


class CaseAnalysisOutput(BaseModel):
    """Output of the Case Analysis Agent."""
    case_id: str
    decision_dimensions: List[str]  # e.g. ["waiting_period", "sub_limits", "hospital_definition"]
    investigation_plan: List[InvestigationItem]
    missing_fields: List[str] = Field(default_factory=list)
    flagged_null_evidence: List[str] = Field(default_factory=list)  # fields explicitly null in evidence_context


class DimensionFinding(BaseModel):
    """Structured output for a single investigated dimension, produced by
    retrieval + reasoning over the retrieved evidence."""
    dimension: str
    conclusion: str  # short natural-language conclusion
    supports_admissibility: Optional[bool] = None  # None = inconclusive
    applicable_limit: Optional[str] = None
    evidence: List[RetrievedEvidence] = Field(default_factory=list)


class CoverageFindings(BaseModel):
    """Output of the Coverage & Exclusion Agent."""
    case_id: str
    findings: List[DimensionFinding]
    missing_evidence: List[str] = Field(default_factory=list)


class FinalDecision(BaseModel):
    """The Decision & Evidence Contract (Section 5)."""
    case_id: str
    decision: DecisionStatus
    confidence: float = Field(ge=0.0, le=1.0)
    key_findings: List[str] = Field(default_factory=list)
    applicable_limits: List[str] = Field(default_factory=list)
    missing_evidence: List[str] = Field(default_factory=list)
    citations: List[Citation] = Field(default_factory=list)
    validation: "ValidationResult"
    trace: List["TraceEntry"] = Field(default_factory=list)


class ValidationResult(BaseModel):
    status: ValidationStatus
    unsupported_claims: List[str] = Field(default_factory=list)


class TraceEntry(BaseModel):
    """One entry in the execution trace shown in the frontend. Concise and
    auditable — NEVER hidden chain-of-thought (Section 5 requirement)."""
    agent: str
    action: str
    detail: Optional[str] = None
    retrieval_result_count: Optional[int] = None
    elapsed_ms: Optional[float] = None


FinalDecision.model_rebuild()


# ---------------------------------------------------------------------------
# API REQUEST/RESPONSE
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    case: ClaimCase


class AnalyzeResponse(BaseModel):
    result: FinalDecision
