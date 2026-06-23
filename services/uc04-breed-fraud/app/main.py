"""
UC-04 Breed Fraud Verification — FastAPI application entry point.

PHI RULE: Never log policy_id or pet_name.
Permitted log fields: request_id, declared_breed, verdict, risk_tier, duration_ms.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .models import VerificationResponse
from .verifier import VISION_MODEL
from .verifier import (
    analyze_image_with_vision,
    assess_image_quality,
    build_breed_assessment,
    apply_fraud_rules,
    check_duplicate,
    check_prior_fraud,
    compute_image_hash,
    get_image_embedding,
    store_embedding,
)

# ---------------------------------------------------------------------------
# Logging — structured, PHI-safe
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("uc04.main")

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
API_KEY: str = os.environ.get("API_KEY", "")
PG_DSN: str = os.environ.get(
    "PG_DSN",
    "postgresql://postgres:postgres@localhost:5432/breedfraud",
)
MAX_IMAGE_BYTES: int = 20 * 1024 * 1024  # 20 MB

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="UC-04 Breed Fraud Verification",
    description=(
        "Detects breed fraud in pet insurance submissions using "
        "Llama 3.2 Vision and CLIP ViT-L/14 embeddings."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
bearer_scheme = HTTPBearer(auto_error=True)


def require_api_key(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> None:
    """Validate Bearer token against API_KEY env var."""
    if not API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API_KEY not configured on server",
        )
    if credentials.credentials != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", tags=["ops"])
async def health_check() -> dict:
    """Liveness probe — no auth required."""
    return {"status": "ok", "service": "uc04-breed-fraud"}


@app.post(
    "/api/v1/verification/breed",
    response_model=VerificationResponse,
    status_code=status.HTTP_200_OK,
    tags=["verification"],
    summary="Submit a pet photo for breed fraud verification",
    dependencies=[Depends(require_api_key)],
)
async def verify_breed(
    image: UploadFile = File(..., description="Pet photo (JPEG/PNG/WEBP, max 20 MB)"),
    policy_id: str = Form(..., description="Insurance policy identifier"),
    declared_breed: str = Form(..., description="Breed as declared by the policyholder"),
    declared_species: str = Form(..., description="Species as declared (dog, cat, etc.)"),
    pet_name: Optional[str] = Form(None, description="Pet name (optional, not logged)"),
    pet_age_years: Optional[float] = Form(None, description="Pet age in years (optional)"),
) -> VerificationResponse:
    """
    Analyse a submitted pet photo for breed fraud indicators.

    Runs the following checks:
    - Vision model breed identification (Llama 3.2 Vision 11B)
    - Duplicate image detection via CLIP embeddings + pgvector
    - Stock photo hash check
    - Image quality assessment
    - Prior fraud flag lookup
    """
    request_id = str(uuid.uuid4())
    t_start = time.monotonic()

    # ------------------------------------------------------------------
    # 1. Validate image upload
    # ------------------------------------------------------------------
    content_type = (image.content_type or "").lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported image type '{content_type}'. Must be JPEG, PNG or WEBP.",
        )

    image_bytes = await image.read()
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Image exceeds 20 MB limit ({len(image_bytes)} bytes received).",
        )
    if len(image_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Image file is empty.",
        )

    # ------------------------------------------------------------------
    # 2. Image quality assessment (synchronous, Pillow)
    # ------------------------------------------------------------------
    try:
        image_quality = assess_image_quality(image_bytes)
    except Exception as exc:
        logger.error("request_id=%s image_quality_error=%s", request_id, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not decode image: {exc}",
        )

    # ------------------------------------------------------------------
    # 3. Compute image hash and CLIP embedding
    # ------------------------------------------------------------------
    image_hash = compute_image_hash(image_bytes)
    embedding = await get_image_embedding(image_bytes)  # 768-dim stub

    # ------------------------------------------------------------------
    # 4. Duplicate check via pgvector
    # ------------------------------------------------------------------
    is_duplicate = await check_duplicate(embedding, PG_DSN)

    # ------------------------------------------------------------------
    # 5. Prior fraud check
    # ------------------------------------------------------------------
    prior_fraud_tier = await check_prior_fraud(policy_id, PG_DSN)

    # ------------------------------------------------------------------
    # 6. Vision model analysis
    # ------------------------------------------------------------------
    try:
        vision_result = await analyze_image_with_vision(
            image_bytes, declared_breed, declared_species
        )
    except httpx.ConnectError as exc:
        logger.error(
            "request_id=%s ollama_connect_error=%s",
            request_id,
            str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vision service (Ollama) is unreachable.",
        )
    except httpx.HTTPStatusError as exc:
        logger.error(
            "request_id=%s ollama_http_error=%s",
            request_id,
            exc.response.status_code,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Vision service returned error {exc.response.status_code}.",
        )
    except Exception as exc:
        logger.error("request_id=%s vision_error=%s", request_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error during vision analysis.",
        )

    # ------------------------------------------------------------------
    # 7. Apply fraud rules
    # ------------------------------------------------------------------
    verdict, risk_tier, fraud_signals = apply_fraud_rules(
        vision_result=vision_result,
        declared_breed=declared_breed,
        declared_species=declared_species,
        image_quality=image_quality,
        is_duplicate=is_duplicate,
        image_hash=image_hash,
        prior_fraud_tier=prior_fraud_tier,
    )

    # ------------------------------------------------------------------
    # 8. Build breed assessment
    # ------------------------------------------------------------------
    breed_assessment = build_breed_assessment(
        vision_result, declared_breed, declared_species
    )

    # ------------------------------------------------------------------
    # 9. Persist embedding for future duplicate checks (best-effort)
    # ------------------------------------------------------------------
    await store_embedding(
        policy_id=policy_id,
        image_hash=image_hash,
        embedding=embedding,
        breed_label=breed_assessment.detected_breed,
        pg_dsn=PG_DSN,
    )

    # ------------------------------------------------------------------
    # 10. Build response
    # ------------------------------------------------------------------
    duration_ms = int((time.monotonic() - t_start) * 1000)
    requires_manual_review = verdict in ("NEEDS_REVIEW", "BREED_MISMATCH") or risk_tier >= 3

    # PHI-SAFE audit log — NO policy_id, NO pet_name
    logger.info(
        "request_id=%s declared_breed=%s verdict=%s risk_tier=%d duration_ms=%d",
        request_id,
        declared_breed,
        verdict,
        risk_tier,
        duration_ms,
    )

    return VerificationResponse(
        request_id=request_id,
        policy_id=policy_id,
        overall_verdict=verdict,
        risk_tier=risk_tier,
        breed_assessment=breed_assessment,
        fraud_signals=fraud_signals,
        image_quality=image_quality,
        requires_manual_review=requires_manual_review,
        processing_time_ms=duration_ms,
        model_used=f"vision:{VISION_MODEL}",
    )
