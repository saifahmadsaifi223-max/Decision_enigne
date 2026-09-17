# Architecture & Design Note

**Policy-Aware Multi-Agent RAG Claim Decision Engine** — Aptino AI Engineer
Take-Home Assignment

## Problem framing

Health insurance claim adjudication is not a single-lookup question. A
claim's admissibility depends on multiple, sometimes interacting policy
provisions (waiting periods, disease-specific exclusions, sub-limits,
definitions), and a naive "retrieve top-k chunks, ask an LLM" pipeline
tends to either miss provisions that don't share vocabulary with the
claim, or confidently assert conclusions the retrieved text doesn't
actually support. The design goal here was reliability and
auditability over conversational fluency: every material claim the
system makes must trace back to a specific page/section of the actual
policy, and the system must be willing to say "I don't know" when the
evidence doesn't support a confident answer.

## Agent boundaries

Three agents, each with a distinct, non-overlapping responsibility:

**Case Analysis Agent** (`app/agents/case_analysis.py`) — deterministic,
rule-based, not an LLM call. Reads the case JSON and decides *which*
policy questions are even relevant (e.g. `pre_existing=True` triggers a
PED-waiting-period investigation; a `domiciliary` treatment type triggers
a domiciliary sub-limit investigation). It also explicitly distinguishes
"this field is unset because it's irrelevant to this case" from "this
field is set to `null` because the fact is genuinely unknown" — the
latter becomes flagged missing evidence, scoped per-field rather than
per-case, so a case can have doubt about medical necessity without that
doubt bleeding into an unrelated Hospital-definition question (see
Failure 1 in `FAILURE_ANALYSIS.md` for why this scoping matters).

*Design choice:* this agent is rule-based rather than LLM-based. The
mapping from structured case fields to relevant policy dimensions is
close to deterministic, and keeping it rule-based removes an entire class
of hallucination risk (an LLM inventing an irrelevant investigation
dimension, or missing an obvious one) at zero LLM cost. The trade-off is
reduced flexibility for case shapes the current heuristics don't
anticipate — documented as a known limitation.

**Decision Agent** (`app/agents/decision.py`) — owns two responsibilities
that the assignment's reference design splits into two agents (Policy
Evidence + Coverage & Exclusion), merged here for build-time reasons but
kept internally separated as distinct functions: `gather_evidence()` runs
hybrid retrieval per investigation dimension; `assess_dimension()` makes
one grounded LLM call per dimension, constrained to only use the
retrieved text; `make_final_decision()` combines all per-dimension
findings into the Section 5 decision contract. Splitting these back into
two literal agent classes later is a wiring change, not a rewrite.

**Validation Agent** (`app/agents/validation.py`) — audits each
per-dimension `conclusion` against its own paired `evidence`, *before*
those findings get combined and paraphrased into the top-level decision.
This ordering is deliberate and was arrived at after finding a real bug:
validating *after* combination meant comparing the Decision Agent's
paraphrased summary against the original evidence text, which structurally
can never match via substring comparison (a paraphrase is not a substring
of what it paraphrases). Validating each dimension against its own
un-paraphrased evidence, before combination, makes the check exact rather
than approximate.

## State flow

```
ClaimCase → CaseAnalysisOutput → CoverageFindings (raw)
          → CoverageFindings (validated, via ValidationAgent)
          → FinalDecision (combined from validated findings only)
```

All inter-agent state is typed Pydantic models (`app/models.py`), not
free-form text — a dimension's finding, its evidence, and its
support/non-support are all structured fields an agent downstream can
consume programmatically rather than re-parsing prose.

## Retrieval design

Dense (ChromaDB, `all-MiniLM-L6-v2`) and sparse (BM25) retrieval run in
parallel per investigation-dimension query, combined via Reciprocal Rank
Fusion, then reranked with a cross-encoder (`ms-marco-MiniLM-L-6-v2`)
before the top-6 chunks reach the Decision Agent's LLM call. Chunking is
structure-aware, not fixed-size: Definitions are split one-chunk-per-term,
exclusions and standard terms one-chunk-per-numbered-item, everything else
by paragraph up to a word cap — chosen specifically so that a citation can
point to exactly the defined term or exclusion item relevant to a claim,
not a diluted block containing several unrelated provisions.

## Key trade-offs

- **3 agents, not 5** — build-time compression; internally still 5
  functionally separated responsibilities.
- **Rule-based Case Analysis Agent** — cheaper and less hallucination-prone,
  less flexible to novel case shapes.
- **Plain Python state machine, not LangGraph** — faster to build and
  debug under a 24-hour timeline; the agent function signatures are
  already state-in/state-out, so this is not a dead-end choice.
- **Local, free-tier models throughout** (Groq-hosted LLM, local
  embedding + reranking models) — zero marginal cost, at some quality
  ceiling relative to larger hosted alternatives (GPT-4-class models,
  Cohere Rerank).
- **Validate-before-combine, not validate-after** — more implementation
  complexity (validation needs access to per-dimension state, not just
  the final decision), but structurally correct rather than
  approximately correct.
