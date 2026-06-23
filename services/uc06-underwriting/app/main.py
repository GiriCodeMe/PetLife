"""
UC-06 Multi-Agent Risk Underwriting — FastAPI application.

Endpoints:
  POST   /api/v1/underwriting/policies              — submit application (202 Accepted)
  GET    /api/v1/underwriting/policies/{job_id}     — poll status / retrieve result
  GET    /api/v1/underwriting/policies/{job_id}/stream  — SSE real-time progress
  POST   /api/v1/underwriting/policies/{job_id}/override — human underwriter override

Auth: Bearer token (API_KEY env var).
PHI: Never logged; only job_id, species, breed, decision, duration_ms appear in logs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any, AsyncGenerator

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sse_starlette.sse import EventSourceResponse

from app.models import (
    JobState,
    JobStatus,
    OverrideRequest,
    UnderwritingDecision,
    UnderwritingPackage,
    UnderwritingRequest,
)
from app.orchestrator import run_underwriting_pipeline

# ---------------------------------------------------------------------------
# Logging — structured, PHI-safe
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("uc06")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_KEY = os.getenv("API_KEY", "changeme-local-dev")
UNDERWRITER_ROLES_HEADER = "X-Underwriter-ID"

# ---------------------------------------------------------------------------
# In-memory job store
# Schema per job_id:
#   state: JobState
#   result: UnderwritingPackage | None
#   progress_queue: asyncio.Queue[dict]
#   current_phase: str
#   progress_pct: int
#   error: str | None
#   override_history: list[dict]
#   created_at: datetime
#   application_id: str  (non-PHI reference)
# ---------------------------------------------------------------------------

job_store: dict[str, Any] = {}
_store_lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="UC-06 Multi-Agent Risk Underwriting",
    description=(
        "Async LangGraph-style DAG underwriting pipeline. "
        "Llama 3.3 70B orchestrator + Qwen 3 32B specialist agents."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer()


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme)) -> str:
    if credentials.credentials != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


def verify_underwriter_role(request: Request) -> str:
    """
    Checks for a valid underwriter identity header on override requests.
    In a production system this would validate against an IAM system.
    """
    underwriter_id = request.headers.get(UNDERWRITER_ROLES_HEADER, "").strip()
    if not underwriter_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing {UNDERWRITER_ROLES_HEADER} header — UNDERWRITER role required",
        )
    return underwriter_id


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def _startup() -> None:
    # Attach the global lock to the store so orchestrator can reference it
    job_store["_lock"] = _store_lock
    logger.info("UC-06 underwriting service started")


@app.on_event("shutdown")
async def _shutdown() -> None:
    logger.info("UC-06 underwriting service shutting down")


# ---------------------------------------------------------------------------
# Helper: create job entry
# ---------------------------------------------------------------------------


def _create_job(application_id: str) -> str:
    job_id = str(uuid.uuid4())
    job_store[job_id] = {
        "state": JobState.PENDING,
        "result": None,
        "progress_queue": asyncio.Queue(maxsize=200),
        "current_phase": "Pending",
        "progress_pct": 0,
        "error": None,
        "override_history": [],
        "created_at": datetime.utcnow().isoformat(),
        "application_id": application_id,
    }
    return job_id


# ---------------------------------------------------------------------------
# Helper: get job or 404
# ---------------------------------------------------------------------------


def _get_job(job_id: str) -> dict:
    entry = job_store.get(job_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )
    return entry


# ---------------------------------------------------------------------------
# POST /api/v1/underwriting/policies
# ---------------------------------------------------------------------------


@app.post(
    "/api/v1/underwriting/policies",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit underwriting application",
    response_description="Job accepted — poll GET endpoint or use SSE stream for progress",
    dependencies=[Depends(verify_token)],
)
async def submit_underwriting(
    application: UnderwritingRequest,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """
    Accept an underwriting application and launch the async DAG pipeline.
    Returns HTTP 202 with job_id for polling.
    """
    job_id = _create_job(application.application_id)

    logger.info(
        "underwriting_submitted job_id=%s application_id=%s species=%s breed=%s",
        job_id,
        application.application_id,
        application.species,
        application.breed,
    )

    background_tasks.add_task(run_underwriting_pipeline, job_id, application, job_store)

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "job_id": job_id,
            "status": "PENDING",
            "message": "Underwriting pipeline started",
            "poll_url": f"/api/v1/underwriting/policies/{job_id}",
            "stream_url": f"/api/v1/underwriting/policies/{job_id}/stream",
        },
    )


# ---------------------------------------------------------------------------
# GET /api/v1/underwriting/policies/{job_id}
# ---------------------------------------------------------------------------


@app.get(
    "/api/v1/underwriting/policies/{job_id}",
    summary="Poll underwriting job status or retrieve completed result",
    dependencies=[Depends(verify_token)],
)
async def get_underwriting_status(job_id: str) -> JobStatus:
    """
    Returns the current job status.
    - While running: returns JobStatus with state, progress_pct, current_phase.
    - When completed: returns JobStatus with embedded UnderwritingPackage in `result`.
    - On failure: returns JobStatus with state=FAILED and error message.
    """
    entry = _get_job(job_id)

    # Re-attach override_history to result if present
    result: UnderwritingPackage | None = entry.get("result")
    if result and entry.get("override_history"):
        result.override_history = entry["override_history"]

    return JobStatus(
        job_id=job_id,
        state=entry["state"],
        progress_pct=entry.get("progress_pct", 0),
        current_phase=entry.get("current_phase", ""),
        error=entry.get("error"),
        result=result,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/underwriting/policies/{job_id}/stream
# ---------------------------------------------------------------------------


async def _progress_event_generator(
    job_id: str,
    entry: dict,
) -> AsyncGenerator[dict, None]:
    """
    Yields SSE events from the job's progress_queue until a __done__ sentinel.
    Includes an initial connected event and a heartbeat every 15 seconds.
    """
    yield {
        "event": "connected",
        "data": json.dumps({"job_id": job_id, "message": "Stream connected"}),
    }

    queue: asyncio.Queue = entry["progress_queue"]

    while True:
        try:
            # Wait up to 15 seconds for next event, then send heartbeat
            event = await asyncio.wait_for(queue.get(), timeout=15.0)
        except asyncio.TimeoutError:
            # SSE heartbeat to keep connection alive
            yield {
                "event": "heartbeat",
                "data": json.dumps(
                    {
                        "job_id": job_id,
                        "state": entry["state"].value,
                        "progress_pct": entry.get("progress_pct", 0),
                    }
                ),
            }
            # Check if job completed/failed while we were waiting
            if entry["state"] in (JobState.COMPLETED, JobState.FAILED):
                break
            continue

        if event.get("__done__"):
            # Final event before closing stream
            final_state = entry["state"]
            final_data: dict[str, Any] = {
                "job_id": job_id,
                "state": final_state.value,
            }
            if final_state == JobState.COMPLETED and entry.get("result"):
                pkg: UnderwritingPackage = entry["result"]
                final_data["decision"] = pkg.underwriting_decision.value
                final_data["risk_score"] = pkg.risk_score
                final_data["processing_time_ms"] = pkg.processing_time_ms
            if event.get("error"):
                final_data["error"] = event["error"]

            yield {
                "event": "complete",
                "data": json.dumps(final_data),
            }
            break

        yield {
            "event": "progress",
            "data": json.dumps(event),
        }


@app.get(
    "/api/v1/underwriting/policies/{job_id}/stream",
    summary="Server-Sent Events stream of underwriting progress",
    dependencies=[Depends(verify_token)],
)
async def stream_underwriting(job_id: str) -> EventSourceResponse:
    """
    SSE endpoint providing real-time underwriting pipeline progress.
    Events: connected, progress, heartbeat, complete.
    Stream closes automatically when the job finishes or fails.
    """
    entry = _get_job(job_id)
    return EventSourceResponse(
        _progress_event_generator(job_id, entry),
        media_type="text/event-stream",
    )


# ---------------------------------------------------------------------------
# POST /api/v1/underwriting/policies/{job_id}/override
# ---------------------------------------------------------------------------


@app.post(
    "/api/v1/underwriting/policies/{job_id}/override",
    summary="Manual underwriter decision override",
    dependencies=[Depends(verify_token)],
)
async def override_underwriting(
    job_id: str,
    override: OverrideRequest,
    request: Request,
) -> JSONResponse:
    """
    Allows a human underwriter (UNDERWRITER role) to override the automated decision.
    Requires X-Underwriter-ID header.
    Override is appended to the audit trail (UR-12).
    GDPR Art.22: If new decision is DECLINE or REFER, override.rationale must be substantive.
    """
    underwriter_id = verify_underwriter_role(request)
    entry = _get_job(job_id)

    if entry["state"] not in (JobState.COMPLETED, JobState.FAILED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job {job_id} is not in a final state (current: {entry['state'].value}). "
                   "Override is only allowed after pipeline completion.",
        )

    pkg: UnderwritingPackage | None = entry.get("result")
    if pkg is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job {job_id} has no result to override (state: {entry['state'].value})",
        )

    # GDPR Art.22: adverse override must have substantive explanation
    if override.new_decision in (UnderwritingDecision.DECLINE, UnderwritingDecision.REFER):
        if len(override.rationale.strip()) < 50:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "GDPR Art.22: Override rationale for DECLINE/REFER decisions must be "
                    "at least 50 characters and clearly explain the specific reasons."
                ),
            )

    previous_decision = pkg.underwriting_decision.value
    audit_entry: dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat(),
        "underwriter_id": underwriter_id,
        "override_id": override.underwriter_id,
        "previous_decision": previous_decision,
        "new_decision": override.new_decision.value,
        "rationale": override.rationale,
        "override_conditions": override.override_conditions,
    }

    # Apply override to the package
    async with _store_lock:
        pkg.underwriting_decision = override.new_decision
        pkg.overall_verdict = override.new_decision
        pkg.requires_manual_review = False  # Human has reviewed

        if override.new_decision in (UnderwritingDecision.DECLINE, UnderwritingDecision.REFER):
            # Ensure explanation_for_adverse is set (GDPR Art.22)
            pkg.explanation_for_adverse = (
                f"[HUMAN OVERRIDE by {underwriter_id}] {override.rationale}"
            )
            pkg.requires_manual_review = False
        elif override.new_decision == UnderwritingDecision.APPROVE:
            pkg.conditions_for_approval = list(override.override_conditions)
            pkg.decline_codes = []

        if override.override_conditions:
            pkg.conditions_for_approval = list(override.override_conditions)

        entry["override_history"].append(audit_entry)
        pkg.override_history = entry["override_history"]

    # PHI-safe audit log (UR-12)
    logger.info(
        "underwriting_override job_id=%s underwriter_id=%s previous=%s new=%s",
        job_id,
        underwriter_id,
        previous_decision,
        override.new_decision.value,
    )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "job_id": job_id,
            "override_applied": True,
            "previous_decision": previous_decision,
            "new_decision": override.new_decision.value,
            "audit_entry": audit_entry,
        },
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "uc06-underwriting"}
