"""
FastAPI backend.

Endpoints (Section 7):
  POST /analyze  -- analyze one claim case, return structured decision
  GET  /health   -- health/readiness check
"""

import logging
import traceback

from dotenv import load_dotenv
load_dotenv()  # must run before any other app import that reads env vars
                # (e.g. app.llm_client reading GROQ_API_KEY) -- kept as the
                # very first lines of the entrypoint module for that reason.

from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from app.models import ClaimCase, AnalyzeRequest, AnalyzeResponse
from app.orchestrator import analyze_claim
from app.retrieval import get_retriever

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aptino-claim-engine")

app = FastAPI(
    title="Policy-Aware Multi-Agent RAG Claim Decision Engine",
    version="0.1.0",
)

_ready = False


@app.on_event("startup")
def startup():
    """Build/load the retrieval indexes once at startup, not per-request."""
    global _ready
    try:
        get_retriever()
        _ready = True
        logger.info("Retriever ready.")
    except Exception:
        logger.exception("Failed to initialize retriever at startup.")
        _ready = False


@app.get("/health")
def health():
    return {"status": "ok" if _ready else "degraded", "retriever_ready": _ready}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest):
    if not _ready:
        raise HTTPException(
            status_code=503,
            detail="Retriever index not ready. Check server startup logs.",
        )
    case = request.case
    try:
        result = analyze_claim(case)
        return AnalyzeResponse(result=result)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error("Error analyzing case %s: %s\n%s", case.case_id, e, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Internal error analyzing case: {e}")