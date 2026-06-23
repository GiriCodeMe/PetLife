"""
UC-03 Automated Medical Coding — FastAPI application entry point.

Endpoints:
  POST /api/v1/coding/notes  — code a veterinary clinical note (bearer auth required)
  GET  /health               — liveness probe (no auth)

PHI RULE: clinical note content is NEVER written to any log.
Logged fields only: request_id, species, note_word_count, pass1_count, pass2_count, duration_ms.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .coder import apply_coding_rules, extract_concepts, map_codes
from .models import CodingRequest, CodingResponse

# ---------------------------------------------------------------------------
# Logging — structured, PHI-safe
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("uc03.main")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_KEY: str = os.environ.get("API_KEY", "")
MODEL_NAME: str = os.environ.get(
    "OLLAMA_MODEL", "qwen2.5:14b-instruct-q4_K_M"
)
OLLAMA_BASE_URL: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="UC-03 Automated Medical Coding",
    description=(
        "Two-pass veterinary clinical note coding service. "
        "Extracts clinical concepts (Pass 1) and maps them to "
        "SNOMED-CT + ICD-10-CM codes (Pass 2) using Qwen2.5 14B via Ollama."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# Shared httpx client (reused across requests for connection pooling)
# ---------------------------------------------------------------------------

_http_client: httpx.AsyncClient | None = None


@app.on_event("startup")
async def _startup() -> None:
    global _http_client
    _http_client = httpx.AsyncClient(base_url=OLLAMA_BASE_URL, timeout=180.0)
    logger.info("Startup complete. model=%s ollama_base=%s", MODEL_NAME, OLLAMA_BASE_URL)


@app.on_event("shutdown")
async def _shutdown() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None
    logger.info("Shutdown complete.")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=True)


async def require_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials, Security(_bearer_scheme)],
) -> None:
    """Validate Bearer token against API_KEY env var."""
    if not API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Service is not configured: API_KEY environment variable is not set.",
        )
    if credentials.credentials != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health", summary="Liveness probe", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok", "model": MODEL_NAME}


@app.post(
    "/api/v1/coding/notes",
    response_model=CodingResponse,
    status_code=status.HTTP_200_OK,
    summary="Code a veterinary clinical note",
    tags=["coding"],
    dependencies=[Depends(require_api_key)],
)
async def code_note(
    request: Request,
    payload: CodingRequest,
) -> CodingResponse:
    """
    Perform two-pass medical coding of a veterinary clinical note.

    - **Pass 1**: extract clinical concepts (conditions, procedures, symptoms, body systems)
    - **Pass 2**: map each concept to SNOMED-CT + ICD-10-CM codes with confidence scores
    - Coding rules CR-01 through CR-10 are applied post-LLM

    PHI RULE: the clinical note text is never logged.
    """
    t_start = time.monotonic()

    # Assign a request_id for tracing
    req_id: str = payload.request_id or str(uuid.uuid4())

    # PHI-safe pre-processing metric only
    note_word_count = len(payload.clinical_note.split())

    logger.info(
        "coding_start request_id=%s species=%s note_word_count=%d",
        req_id,
        payload.species,
        note_word_count,
    )

    # --- Pass 1: concept extraction ---
    try:
        concepts = await extract_concepts(
            note=payload.clinical_note,
            species=payload.species,
            model=MODEL_NAME,
            http_client=_http_client,
        )
    except Exception as exc:
        logger.error("Pass 1 failed request_id=%s error=%s", req_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Pass 1 (concept extraction) failed: {exc}",
        ) from exc

    pass1_count = len(concepts)
    logger.info("pass1_complete request_id=%s pass1_concepts_found=%d", req_id, pass1_count)

    # --- Pass 2: code mapping ---
    try:
        raw_findings = await map_codes(
            concepts=concepts,
            species=payload.species,
            model=MODEL_NAME,
            http_client=_http_client,
        )
    except Exception as exc:
        logger.error("Pass 2 failed request_id=%s error=%s", req_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Pass 2 (code mapping) failed: {exc}",
        ) from exc

    pass2_count = len(raw_findings)

    # --- Apply coding rules CR-01..CR-10 ---
    coded_findings = apply_coding_rules(raw_findings, species=payload.species)

    # --- Build summary fields ---
    primary: Any = next(
        (f for f in coded_findings if f.is_primary_diagnosis), None
    )

    unmapped = [
        f.concept
        for f in coded_findings
        if f.snomed_code == "UNMAPPED" or f.icd10_code == "UNMAPPED"
    ]

    duration_ms = int((time.monotonic() - t_start) * 1000)

    logger.info(
        "coding_complete request_id=%s species=%s pass1_count=%d pass2_count=%d "
        "num_codes_found=%d duration_ms=%d",
        req_id,
        payload.species,
        pass1_count,
        pass2_count,
        len(coded_findings),
        duration_ms,
    )

    return CodingResponse(
        request_id=req_id,
        note_id=payload.note_id,
        species=payload.species,
        coded_findings=coded_findings,
        primary_diagnosis=primary,
        unmapped_concepts=unmapped,
        processing_time_ms=duration_ms,
        model_used=MODEL_NAME,
        pass1_concepts_found=pass1_count,
        pass2_codes_mapped=pass2_count,
    )
