# Policy-Aware Multi-Agent RAG Claim Decision Engine

Built for the Aptino AI Engineer take-home assignment. Analyzes health-insurance
claim cases against the actual policy PDF using hybrid retrieval + a 3-agent
pipeline, and returns an evidence-grounded structured decision.

## Architecture

```
ClaimCase (JSON)
     │
     ▼
┌─────────────────────┐
│ Case Analysis Agent  │  rule-based: identifies which decision dimensions
│ (app/agents/         │  are relevant (waiting period, PED, exclusions,
│  case_analysis.py)   │  sub-limits, etc.), flags missing/null evidence
└─────────┬────────────┘
          │ CaseAnalysisOutput (structured state)
          ▼
┌─────────────────────┐      ┌─────────────────────────────┐
│ Decision Agent        │◄────│ Hybrid Retrieval              │
│ (app/agents/          │     │ (app/retrieval.py)             │
│  decision.py)          │     │ dense (Chroma) + sparse (BM25) │
│ - gathers evidence per │     │ + RRF fusion + cross-encoder   │
│   dimension            │     │ reranking                       │
│ - LLM assesses each    │     └─────────────────────────────┘
│   dimension, grounded  │
│   only in retrieved    │
│   text                 │
│ - combines into final  │
│   decision             │
└─────────┬───────────────┘
          │ FinalDecision (decision + citations, unvalidated)
          ▼
┌─────────────────────┐
│ Validation Agent      │  independently re-checks that every material
│ (app/agents/          │  claim is actually supported by its cited
│  validation.py)        │  evidence; triggers one retry, then downgrades
└─────────┬────────────┘  to NEEDS_REVIEW if still unsupported
          │
          ▼
    FinalDecision (Section 5 contract)
```

Orchestration is a plain Python state machine (`app/orchestrator.py`), not
LangGraph — chosen for build speed; the agent function signatures are
already state-in/state-out, so swapping orchestration frameworks later is
a wiring change, not a rewrite.

## Why 3 agents, not 5

The assignment's reference design lists 5 agents. We merged Policy Evidence
+ Coverage & Exclusion into one Decision Agent (still functionally
separated internally via `gather_evidence()` / `assess_dimension()` /
`make_final_decision()`), keeping Case Analysis and Validation distinct.
This satisfies "at least three genuinely specialized agents... responsibilities
must be meaningfully separated" while fitting a compressed build timeline.
See `app/agents/decision.py` docstring for the exact split points if you
want to separate them later.

## Setup

```bash
git clone <your-repo-url>
cd aptino-claim-engine
python3.10 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and add your GROQ_API_KEY (free tier: https://console.groq.com/keys)
```

### 1. Ingest the policy PDF

```bash
mkdir -p data
cp /path/to/USGIC-CSCIndividualHealthInsurance_2017-2018.pdf data/
python -m app.ingestion data/USGIC-CSCIndividualHealthInsurance_2017-2018.pdf
```

This produces `data/policy_chunks.json`. Sanity-check the chunking:

```bash
python -c "
import json
chunks = json.load(open('data/policy_chunks.json'))
print(f'{len(chunks)} chunks')
print([c for c in chunks if c[\"subsection\"] == \"Hospital\"])
"
```

### 2. Build retrieval indexes and sanity-check retrieval

```bash
python -m app.retrieval
```

This builds the Chroma + BM25 indexes and runs a handful of test queries
against known clauses (waiting period, PED, Hospital definition, cosmetic
exclusion, domiciliary sub-limit) so you can visually confirm retrieval is
returning the right page/section before wiring up the agents.

### 3. Copy the public test cases

```bash
cp /path/to/public_test_cases.json data/
```

### 4. Run the API locally

```bash
uvicorn app.api:app --reload --port 8000
```

Test it:

```bash
curl http://localhost:8000/health

curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d @data/public_test_cases.json   # NOTE: adjust to send ONE case object,
                                      # not the whole array -- see example below
```

Example single-case request body (matches `AnalyzeRequest`):
```json
{ "case": { "case_id": "PUB-001", "...": "..." } }
```

Example response body (matches the Section 5 contract):
```json
{
  "result": {
    "case_id": "PUB-001",
    "decision": "ADMISSIBLE_WITH_LIMITS",
    "confidence": 0.87,
    "key_findings": ["..."],
    "applicable_limits": ["Room rent capped at 1% of Sum Insured per day"],
    "missing_evidence": [],
    "citations": [
      {"claim": "...", "source": "policy.pdf", "page": 7, "section": "Scope of Cover", "chunk_id": "p7_scope_of_cover_002"}
    ],
    "validation": {"status": "PASS", "unsupported_claims": []},
    "trace": [ {"agent": "CaseAnalysisAgent", "action": "build_investigation_plan", "...": "..."} ]
  }
}
```

### 5. Run the frontend

```bash
streamlit run frontend/streamlit_app.py
```

### 6. Run the evaluation

```bash
python -m eval.evaluate
```

Writes `eval/results.json` and prints a summary (decision accuracy,
citation presence rate, validation pass rate, abstention count). Ground
truth and the reasoning behind each expected decision is documented
separately in `expected_outcomes_ground_truth.md` (also serves as the
answer to "explain how the expected outcome is established").

## Adding your own test cases

Write at least 5 new cases into `eval/custom_cases.json` (stub provided —
do NOT touch `data/public_test_cases.json`). Suggested scenarios not
already covered by the 12 public cases:
- A **PARTIALLY_ADMISSIBLE** case: one covered line item + one excluded
  line item in the same claim (e.g. accident-related surgery + unrelated
  dental work billed together).
- A case testing **sum-insured exhaustion**: claim amount that would
  exceed remaining sum insured after cumulative bonus adjustments.
- A case with **two policy sections in tension** (e.g. day-care procedure
  that's also in the first-year exclusion list, but with prior continuity
  — forces the agents to combine evidence from multiple sections, per
  Section 10's second reliability scenario).
- A case with an **irrelevant attribute** the model should ignore (Section
  10, sixth bullet) — e.g. an extra field like `patient.blood_type` that
  has no bearing on the policy decision.
- A second, more marginal **NEEDS_REVIEW** case distinct from the "Hospital
  definition" pattern already covered by PUB-006/PUB-011 — e.g. ambiguous
  documentation of whether treatment was "medically necessary."

Add the corresponding expected decision to `EXPECTED_DECISIONS` in
`eval/evaluate.py` once written.

## Known limitations / trade-offs

- **3 agents instead of 5** (see above) — a deliberate scope cut for the
  build timeline, not an oversight.
- **Rule-based Case Analysis Agent** rather than LLM-based — deterministic
  mapping from case fields to decision dimensions reduces hallucination
  risk and cost, at the expense of flexibility if a genuinely novel case
  shape appears that isn't covered by the current heuristics.
- **Cross-encoder reranker runs locally** (`ms-marco-MiniLM-L-6-v2`) rather
  than a hosted reranking API, to stay in the free-tier requirement —
  slightly lower quality than BGE-reranker-large or Cohere Rerank, but
  zero cost and no external dependency.
- **Validation Agent retries once, then abstains** rather than looping
  indefinitely — bounded retries keep latency predictable.
- The source policy PDF has at least one **truncated/ambiguous clause**
  (see `expected_outcomes_ground_truth.md`, PUB-004 notes) which the
  chunker preserves as-is rather than attempting to "fix" — silently
  repairing ambiguous source text would be worse than surfacing it.

## Failure analysis

See `expected_outcomes_ground_truth.md` for 3+ documented ambiguities in
the source policy that surfaced during development, and how the system
handles (or should be made to handle) each one. Update this section with
concrete before/after examples once you've run the full evaluation and
observed actual failures — the assignment specifically wants failures you
found and fixed, not just theoretical ones.

## Deployment

- Backend: deploy `app/api.py` to Render or Hugging Face Spaces
  (Docker-based Space recommended for FastAPI + Chroma's local persistence).
- Frontend: deploy `frontend/streamlit_app.py` to Streamlit Community Cloud,
  pointing `API_BASE_URL` at the deployed backend.
- Remember to set `GROQ_API_KEY` (or your chosen provider's key) as a
  secret/environment variable on whichever platform you use — never commit
  it.
