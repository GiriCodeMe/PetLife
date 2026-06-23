"""
UC-02 Claims Adjudication Service — FastAPI application.

PHI RULE:
  - NEVER log claim details, patient data, financial amounts, or member information.
  - Allowed log fields: claim_id, decision, approved_amount, duration_ms.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .models import (
    AdjudicationRequest,
    AdjudicationResponse,
    PolicyRecord,
)
from .rules import adjudicate

# ---------------------------------------------------------------------------
# Logging — PHI-safe (no patient/financial data)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("uc02.adjudication")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_KEY = os.environ.get("API_KEY", "changeme-dev-key")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "phi4:14b-q4_K_M")
OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "60"))

# ---------------------------------------------------------------------------
# Mock Policy Store — 3 realistic sample policies
# ---------------------------------------------------------------------------
POLICY_STORE: dict[str, PolicyRecord] = {
    "POL-001": PolicyRecord(
        policy_id="POL-001",
        member_id="MBR-10001",
        pet_name="Biscuit",
        species="dog",
        breed="Golden Retriever",
        policy_start="2024-01-15",
        annual_benefit_max=10000.00,
        annual_benefit_used=1250.00,
        deductible=200.00,
        deductible_met=200.00,
        coinsurance_pct=0.80,
        per_incident_max=3000.00,
        category_limits={
            "dental": 500.00,
            "preventive": 300.00,
            "wellness": 200.00,
        },
        category_spent={
            "dental": 0.00,
            "preventive": 120.00,
            "wellness": 50.00,
        },
        excluded_codes=["99999", "V72.0"],
        excluded_conditions=["cosmetic", "elective"],
        waiting_periods={
            "illness": 14,
            "dental": 30,
            "orthopedic": 180,
        },
    ),
    "POL-002": PolicyRecord(
        policy_id="POL-002",
        member_id="MBR-10002",
        pet_name="Whiskers",
        species="cat",
        breed="Domestic Shorthair",
        policy_start="2023-06-01",
        annual_benefit_max=5000.00,
        annual_benefit_used=4800.00,
        deductible=100.00,
        deductible_met=100.00,
        coinsurance_pct=0.90,
        per_incident_max=None,
        category_limits={
            "dental": 300.00,
            "preventive": 150.00,
        },
        category_spent={
            "dental": 250.00,
            "preventive": 150.00,
        },
        excluded_codes=["00100"],
        excluded_conditions=["pre-existing", "hereditary"],
        waiting_periods={
            "illness": 14,
            "dental": 14,
        },
    ),
    "POL-003": PolicyRecord(
        policy_id="POL-003",
        member_id="MBR-10003",
        pet_name="Rocket",
        species="dog",
        breed="Border Collie",
        policy_start="2025-03-01",
        annual_benefit_max=15000.00,
        annual_benefit_used=0.00,
        deductible=500.00,
        deductible_met=0.00,
        coinsurance_pct=0.80,
        per_incident_max=5000.00,
        category_limits={
            "dental": 1000.00,
            "preventive": 500.00,
            "wellness": 400.00,
            "orthopedic": 5000.00,
        },
        category_spent={},
        excluded_codes=[],
        excluded_conditions=["cosmetic"],
        waiting_periods={
            "illness": 14,
            "dental": 30,
            "orthopedic": 180,
            "preventive": 0,
        },
    ),
}


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="UC-02 Claims Adjudication Service",
    description="Pet insurance claim adjudication with rule engine and LLM explanation.",
    version="1.0.0",
)

security = HTTPBearer()


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------
async def verify_bearer(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> None:
    """Validates Bearer token against API_KEY env var."""
    if credentials.credentials != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ---------------------------------------------------------------------------
# LLM helper — generates customer-facing explanation for DENIED/PARTIAL
# ---------------------------------------------------------------------------
async def generate_explanation(
    denial_reasons: list[str],
    decision: str,
) -> tuple[str, Optional[str]]:
    """
    Calls Ollama to produce a 2-3 sentence customer-friendly explanation.
    Returns (explanation_text, model_used).
    Falls back to a plain-text default if Ollama is unavailable.

    PHI RULE: prompt contains only denial reason codes — NO patient/financial data.
    """
    reason_list = ", ".join(denial_reasons) if denial_reasons else "policy limitations"
    prompt = (
        f"A pet insurance claim has been {decision.lower()} due to the following reasons: "
        f"{reason_list}. "
        "Write a 2-3 sentence customer-friendly explanation of why the claim was not fully "
        "approved. Be empathetic, clear, and avoid jargon. Do not mention specific dollar amounts."
    )

    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.0},
                },
            )
            response.raise_for_status()
            data = response.json()
            explanation = data.get("response", "").strip()
            model_used = data.get("model", OLLAMA_MODEL)
            if explanation:
                return explanation, model_used
    except Exception as exc:
        # Log the exception type only — no PHI
        logger.warning("LLM explanation unavailable: %s", type(exc).__name__)

    # Fallback plain-text explanation
    fallback = (
        f"Your claim has been {decision.lower()} based on your policy terms. "
        "Some services may not be covered due to waiting periods, benefit limits, or exclusions. "
        "Please contact customer support if you have questions about your coverage."
    )
    return fallback, None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", tags=["ops"])
async def health_check() -> dict:
    """Simple liveness probe."""
    return {"status": "ok", "service": "uc02-claims-adjudication"}


@app.post(
    "/api/v1/claims/adjudicate",
    response_model=AdjudicationResponse,
    status_code=status.HTTP_200_OK,
    tags=["adjudication"],
    dependencies=[Depends(verify_bearer)],
)
async def adjudicate_claim(request: AdjudicationRequest) -> AdjudicationResponse:
    """
    Adjudicate a pet insurance claim.

    - Validates bearer token.
    - Looks up policy from mock store (404 if not found).
    - Runs R-01..R-09 rule engine.
    - For DENIED or PARTIAL outcomes, calls Phi-4 14B via Ollama to generate explanation.
    - Returns full adjudication decision.

    PHI RULE: logs only claim_id, decision, approved_amount, duration_ms.
    """
    start_time = time.monotonic()

    # Policy lookup — 404 if not found
    policy = POLICY_STORE.get(request.policy_id)
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy '{request.policy_id}' not found",
        )

    # Validate member ownership of the policy
    if policy.member_id != request.member_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Policy does not belong to specified member",
        )

    # Run rule engine
    decision, line_decisions, denial_reasons = await adjudicate(
        invoice_data=request.invoice,
        policy=policy,
    )

    # Compute totals
    approved_amount = round(sum(ld.approved_amount for ld in line_decisions), 2)
    denied_amount = round(sum(ld.original_amount for ld in line_decisions) - approved_amount, 2)

    # LLM explanation for non-full-approval outcomes
    explanation = ""
    model_used: Optional[str] = None
    if decision in ("DENIED", "PARTIAL"):
        explanation, model_used = await generate_explanation(denial_reasons, decision)

    duration_ms = int((time.monotonic() - start_time) * 1000)

    # PHI-safe log — claim_id, decision, approved_amount, duration only
    logger.info(
        "claim_id=%s decision=%s approved_amount=%.2f duration_ms=%d",
        request.claim_id,
        decision,
        approved_amount,
        duration_ms,
    )

    return AdjudicationResponse(
        claim_id=request.claim_id,
        decision=decision,
        approved_amount=approved_amount,
        denied_amount=denied_amount,
        line_decisions=line_decisions,
        denial_reasons=denial_reasons,
        explanation=explanation,
        processing_time_ms=duration_ms,
        model_used=model_used,
    )
