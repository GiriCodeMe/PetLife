"""
UC-05 Longitudinal Medical History Review — FastAPI application entry point.

PHI POLICY: NEVER log any content extracted from medical records.
Log ONLY: review_id, page_count, status, duration_ms.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Annotated, AsyncGenerator

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from .models import ConditionEntry, JobStatus, ReviewResult
from .reviewer import process_review

# ---------------------------------------------------------------------------
# Logging — structured, no PHI
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("uc05.main")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_BUNDLE_BYTES = 50 * 1024 * 1024  # 50 MB
API_KEY = os.environ.get("API_KEY", "")

# ---------------------------------------------------------------------------
# In-memory job store
# Each entry:
#   status: str
#   progress_pct: int
#   current_pass: str | None
#   page_count: int | None
#   created_at: str
#   completed_at: str | None
#   result: ReviewResult | None
#   task: asyncio.Task | None
#   progress_queue: asyncio.Queue
# ---------------------------------------------------------------------------
_store: dict[str, dict] = {}
_store_lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="UC-05 Longitudinal Medical History Review",
    version="1.0.0",
    description="Asynchronous veterinary medical history review using Llama 3.3 70B via Ollama.",
)


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

async def require_bearer(authorization: Annotated[str | None, Header()] = None) -> None:
    """Validate Bearer token from Authorization header."""
    if not API_KEY:
        # If no API_KEY is configured, allow all requests (dev mode)
        return
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if parts[1] != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )


AuthDep = Annotated[None, Depends(require_bearer)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _job_to_status(review_id: str, job: dict) -> JobStatus:
    return JobStatus(
        review_id=review_id,
        status=job["status"],
        progress_pct=job.get("progress_pct", 0),
        current_pass=job.get("current_pass"),
        page_count=job.get("page_count"),
        created_at=job["created_at"],
        completed_at=job.get("completed_at"),
    )


def _get_job_or_404(review_id: str) -> dict:
    job = _store.get(review_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review {review_id} not found",
        )
    return job


# ---------------------------------------------------------------------------
# POST /api/v1/history/reviews
# ---------------------------------------------------------------------------

@app.post(
    "/api/v1/history/reviews",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a medical history PDF for review",
    dependencies=[Depends(require_bearer)],
)
async def create_review(
    background_tasks: BackgroundTasks,
    pdf_file: Annotated[UploadFile, File(description="Pet medical history PDF (max 50 MB)")],
    policy_inception_date: Annotated[str, Form(description="Policy start date YYYY-MM-DD")],
    species: Annotated[str, Form(description="Pet species, e.g. 'dog' or 'cat'")],
    member_id: Annotated[str, Form(description="Member identifier")],
) -> JSONResponse:
    """
    Accept a PDF medical history bundle and kick off async review.

    Returns HTTP 202 with review_id, poll_url, and stream_url.
    """
    # Validate content type
    if pdf_file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file must be a PDF (application/pdf)",
        )

    # Read and validate size
    pdf_bytes = await pdf_file.read()
    if len(pdf_bytes) > MAX_BUNDLE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"PDF exceeds maximum allowed size of {MAX_BUNDLE_BYTES // (1024*1024)} MB",
        )
    if len(pdf_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded PDF is empty",
        )

    review_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()

    # Initialise job record
    async with _store_lock:
        _store[review_id] = {
            "status": "queued",
            "progress_pct": 0,
            "current_pass": None,
            "page_count": None,
            "created_at": created_at,
            "completed_at": None,
            "result": None,
            "task": None,
            "progress_queue": asyncio.Queue(),
        }

    # Launch background task
    task = asyncio.create_task(
        process_review(
            review_id=review_id,
            pdf_bytes=pdf_bytes,
            policy_inception_date=policy_inception_date,
            species=species,
            store=_store,
        )
    )
    _store[review_id]["task"] = task

    logger.info("review_queued review_id=%s member_id_hash=%s", review_id, hash(member_id))

    base_url = f"/api/v1/history/reviews/{review_id}"
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "review_id": review_id,
            "status": "queued",
            "poll_url": base_url,
            "stream_url": f"{base_url}/progress",
        },
    )


# ---------------------------------------------------------------------------
# GET /api/v1/history/reviews/{review_id}
# ---------------------------------------------------------------------------

@app.get(
    "/api/v1/history/reviews/{review_id}",
    summary="Poll for review status or retrieve completed result",
    dependencies=[Depends(require_bearer)],
)
async def get_review(review_id: str) -> JSONResponse:
    """
    Returns JobStatus while processing is in progress.
    Returns full ReviewResult when status == 'completed'.
    """
    job = _get_job_or_404(review_id)

    if job["status"] == "completed" and job.get("result") is not None:
        result: ReviewResult = job["result"]
        return JSONResponse(content=result.model_dump())

    return JSONResponse(content=_job_to_status(review_id, job).model_dump())


# ---------------------------------------------------------------------------
# GET /api/v1/history/reviews/{review_id}/progress  (SSE)
# ---------------------------------------------------------------------------

@app.get(
    "/api/v1/history/reviews/{review_id}/progress",
    summary="SSE stream of processing progress events",
    dependencies=[Depends(require_bearer)],
)
async def stream_progress(review_id: str) -> EventSourceResponse:
    """
    Server-Sent Events stream.

    Events are JSON objects:
      {type: "progress", data: {progress_pct, current_pass, message}}
      {type: "complete", data: {review_id, status}}
      {type: "error",   data: {error, review_id}}

    The stream closes after a 'complete' or 'error' event.
    """
    job = _get_job_or_404(review_id)

    async def event_generator() -> AsyncGenerator[dict, None]:
        queue: asyncio.Queue = job["progress_queue"]

        # If already completed, emit a single complete event immediately
        if job["status"] in ("completed", "failed", "cancelled"):
            if job["status"] == "completed":
                yield {
                    "data": json.dumps({
                        "type": "complete",
                        "data": {"review_id": review_id, "status": "completed"},
                    })
                }
            else:
                yield {
                    "data": json.dumps({
                        "type": "error",
                        "data": {"error": f"Review {job['status']}", "review_id": review_id},
                    })
                }
            return

        # Stream live events from the queue
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # Send a heartbeat comment to keep the connection alive
                yield {"comment": "keepalive"}
                continue

            yield {"data": json.dumps(event)}

            # Terminal events — close the stream
            if event.get("type") in ("complete", "error"):
                break

            # Guard against missed terminal state
            if job.get("status") in ("completed", "failed", "cancelled"):
                break

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# DELETE /api/v1/history/reviews/{review_id}
# ---------------------------------------------------------------------------

@app.delete(
    "/api/v1/history/reviews/{review_id}",
    status_code=status.HTTP_200_OK,
    summary="Cancel or delete a review",
    dependencies=[Depends(require_bearer)],
)
async def delete_review(review_id: str) -> JSONResponse:
    """
    Cancel a queued or in-progress review, or delete a completed/failed one.
    """
    job = _get_job_or_404(review_id)

    current_status = job["status"]

    if current_status in ("queued", "processing"):
        task: asyncio.Task | None = job.get("task")
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        job["status"] = "cancelled"
        job["completed_at"] = datetime.utcnow().isoformat()
        logger.info("review_cancelled review_id=%s", review_id)

    # Remove from store
    async with _store_lock:
        _store.pop(review_id, None)

    return JSONResponse(
        content={
            "review_id": review_id,
            "status": "cancelled" if current_status in ("queued", "processing") else current_status,
            "message": "Review deleted",
        }
    )


# ---------------------------------------------------------------------------
# Health check (no auth required)
# ---------------------------------------------------------------------------

@app.get("/health", include_in_schema=False)
async def health() -> JSONResponse:
    return JSONResponse(content={"status": "ok", "service": "uc05-history-review"})
