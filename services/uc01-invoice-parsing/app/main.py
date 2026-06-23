from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Optional

import httpx
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import ValidationError

from app.models import InvoiceData, LineItem, ParseResponse
from app.parser import (
    ExtractionParseError,
    OllamaUnavailableError,
    check_ollama_reachable,
    extract_text,
    parse_invoice,
)

# ---------------------------------------------------------------------------
# Logging — structured, PHI-safe. Only request_id / duration / status logged.
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("uc01.main")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_KEY: str = os.getenv("API_KEY", "")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.1:8b-instruct-q4_K_M")
MAX_FILE_BYTES: int = 10 * 1024 * 1024  # 10 MB
REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CACHE_TTL_SECONDS: int = 300  # 5 minutes

# ---------------------------------------------------------------------------
# Prometheus-style metrics (in-process counters)
# ---------------------------------------------------------------------------
_metrics: dict[str, Any] = {
    "requests_total": 0,
    "errors_total": 0,
    # latency samples for p95 — capped at 1000 samples (ring-buffer behaviour)
    "latency_samples": [],
}


def _record_latency(ms: int, success: bool) -> None:
    _metrics["requests_total"] += 1
    if not success:
        _metrics["errors_total"] += 1
    samples: list = _metrics["latency_samples"]
    samples.append(ms)
    if len(samples) > 1000:
        _metrics["latency_samples"] = samples[-1000:]


def _p95_ms() -> float:
    samples: list[int] = _metrics["latency_samples"]
    if not samples:
        return 0.0
    sorted_s = sorted(samples)
    idx = max(0, int(len(sorted_s) * 0.95) - 1)
    return float(sorted_s[idx])


# ---------------------------------------------------------------------------
# Redis cache (with in-memory fallback)
# ---------------------------------------------------------------------------
_memory_cache: dict[str, dict] = {}

try:
    import redis.asyncio as aioredis  # type: ignore

    _redis_client: Optional[Any] = aioredis.from_url(REDIS_URL, decode_responses=True)
    _redis_available = True
except Exception:
    _redis_client = None
    _redis_available = False


async def _cache_get(key: str) -> Optional[dict]:
    if _redis_available and _redis_client is not None:
        try:
            import json as _json

            raw = await _redis_client.get(key)
            if raw:
                return _json.loads(raw)
        except Exception:
            pass
    return _memory_cache.get(key)


async def _cache_set(key: str, value: dict) -> None:
    if _redis_available and _redis_client is not None:
        try:
            import json as _json

            await _redis_client.setex(key, CACHE_TTL_SECONDS, _json.dumps(value))
            return
        except Exception:
            pass
    # Fallback: in-memory (no TTL enforced, simple eviction at 10 000 entries)
    if len(_memory_cache) >= 10_000:
        # evict oldest 1000
        keys = list(_memory_cache.keys())[:1000]
        for k in keys:
            _memory_cache.pop(k, None)
    _memory_cache[key] = value


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------
async def verify_bearer(authorization: Optional[str] = Header(default=None)) -> None:
    """Check Authorization: Bearer <API_KEY>."""
    if not API_KEY:
        # No key configured — open access (dev mode)
        return
    if authorization is None:
        raise HTTPException(status_code=401, detail="Authorization header required")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401, detail="Authorization header must be 'Bearer <token>'"
        )
    if parts[1] != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")


# ---------------------------------------------------------------------------
# Validation rule helpers (RR-01..RR-06 are enforced inside InvoiceData)
# The model validator on InvoiceData handles RR-01, RR-02, RR-03, RR-04, RR-05,
# RR-06.  This function collects any additional soft-validation messages.
# ---------------------------------------------------------------------------
def _collect_validation_errors(exc: ValidationError) -> list[str]:
    errors: list[str] = []
    for err in exc.errors():
        loc = " -> ".join(str(l) for l in err["loc"])
        errors.append(f"{loc}: {err['msg']}")
    return errors


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="UC-01 Invoice Parsing",
    version="1.0.0",
    description="Veterinary invoice parsing via pdfplumber + Llama 3.1 8B (Ollama)",
)


# ---------------------------------------------------------------------------
# POST /api/v1/invoices/parse
# ---------------------------------------------------------------------------
@app.post(
    "/api/v1/invoices/parse",
    response_model=ParseResponse,
    summary="Parse a veterinary invoice PDF",
    dependencies=[Depends(verify_bearer)],
)
async def parse_invoice_endpoint(
    file: UploadFile = File(..., description="Veterinary invoice PDF (max 10 MB)"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
) -> ParseResponse:
    start_ts = time.monotonic()

    # 1. Resolve / generate request_id
    request_id: str = x_request_id if x_request_id else str(uuid.uuid4())

    # 2. Validate content type
    content_type = (file.content_type or "").lower()
    filename = (file.filename or "").lower()
    if content_type not in ("application/pdf", "application/x-pdf") and not filename.endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are accepted. Upload a file with Content-Type: application/pdf.",
        )

    # 3. Read file bytes and enforce size limit
    pdf_bytes = await file.read()
    file_size = len(pdf_bytes)
    if file_size > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File size {file_size} bytes exceeds the 10 MB limit.",
        )

    # PHI-safe log — only file_size, request_id (never file content)
    logger.info("request_id=%s file_size=%d parse_started", request_id, file_size)

    # 4. Idempotency check
    cached = await _cache_get(request_id)
    if cached is not None:
        logger.info("request_id=%s cache_hit", request_id)
        return ParseResponse(**cached)

    # 5. Extract text from PDF
    try:
        text = await extract_text(pdf_bytes)
    except Exception as exc:
        duration_ms = int((time.monotonic() - start_ts) * 1000)
        logger.error(
            "request_id=%s pdf_extraction_failed duration_ms=%d", request_id, duration_ms
        )
        _record_latency(duration_ms, success=False)
        raise HTTPException(
            status_code=422, detail=f"PDF text extraction failed: {exc}"
        ) from exc

    if not text.strip():
        duration_ms = int((time.monotonic() - start_ts) * 1000)
        logger.error("request_id=%s pdf_no_text duration_ms=%d", request_id, duration_ms)
        _record_latency(duration_ms, success=False)
        raise HTTPException(
            status_code=422,
            detail="No extractable text found in the PDF. The file may be scanned/image-only.",
        )

    # 6. Call Ollama for structured extraction
    try:
        raw_data = await parse_invoice(text, request_id)
    except OllamaUnavailableError as exc:
        duration_ms = int((time.monotonic() - start_ts) * 1000)
        logger.error(
            "request_id=%s ollama_unavailable duration_ms=%d", request_id, duration_ms
        )
        _record_latency(duration_ms, success=False)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ExtractionParseError as exc:
        duration_ms = int((time.monotonic() - start_ts) * 1000)
        logger.error(
            "request_id=%s extraction_parse_error duration_ms=%d", request_id, duration_ms
        )
        _record_latency(duration_ms, success=False)
        response = ParseResponse(
            request_id=request_id,
            status="failed",
            invoice=None,
            validation_errors=[str(exc)],
            processing_time_ms=duration_ms,
            model_used=OLLAMA_MODEL,
        )
        await _cache_set(request_id, response.model_dump())
        return response

    # 7. Validate and build InvoiceData
    validation_errors: list[str] = []
    invoice_data: Optional[InvoiceData] = None
    status: str = "success"

    try:
        invoice_data = InvoiceData(**raw_data)
    except (ValidationError, TypeError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            validation_errors = _collect_validation_errors(exc)
        else:
            validation_errors = [str(exc)]
        status = "partial" if raw_data else "failed"
        logger.error(
            "request_id=%s validation_failed error_count=%d",
            request_id,
            len(validation_errors),
        )

    duration_ms = int((time.monotonic() - start_ts) * 1000)
    success_flag = status == "success"
    _record_latency(duration_ms, success=success_flag)

    # PHI-safe log — only request_id, duration, status (never invoice fields)
    logger.info(
        "request_id=%s duration_ms=%d status=%s",
        request_id,
        duration_ms,
        status,
    )

    response = ParseResponse(
        request_id=request_id,
        status=status,
        invoice=invoice_data,
        validation_errors=validation_errors,
        processing_time_ms=duration_ms,
        model_used=OLLAMA_MODEL,
    )

    await _cache_set(request_id, response.model_dump())
    return response


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------
@app.get("/health", summary="Health check")
async def health_check() -> dict:
    ollama_ok = await check_ollama_reachable()
    return {
        "status": "ok",
        "model": OLLAMA_MODEL,
        "ollama_reachable": ollama_ok,
    }


# ---------------------------------------------------------------------------
# GET /metrics  (Prometheus text format)
# ---------------------------------------------------------------------------
@app.get("/metrics", response_class=PlainTextResponse, summary="Prometheus metrics")
async def metrics() -> str:
    p95 = _p95_ms()
    lines = [
        "# HELP uc01_requests_total Total number of parse requests",
        "# TYPE uc01_requests_total counter",
        f'uc01_requests_total {_metrics["requests_total"]}',
        "",
        "# HELP uc01_errors_total Total number of failed parse requests",
        "# TYPE uc01_errors_total counter",
        f'uc01_errors_total {_metrics["errors_total"]}',
        "",
        "# HELP uc01_latency_p95_ms 95th-percentile processing latency in milliseconds",
        "# TYPE uc01_latency_p95_ms gauge",
        f"uc01_latency_p95_ms {p95:.1f}",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Global exception handler — convert unhandled errors to 500 without leaking PHI
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled_exception type=%s", type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )
