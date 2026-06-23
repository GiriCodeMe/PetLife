"""
Pydantic v2 models for UC-05 Longitudinal Medical History Review microservice.
"""

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field
from datetime import datetime


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

class ReviewRequest(BaseModel):
    """Fields parsed from multipart/form-data (non-file fields)."""

    policy_inception_date: str = Field(
        ...,
        description="ISO date string (YYYY-MM-DD) for the policy start date.",
        examples=["2023-06-01"],
    )
    species: str = Field(
        ...,
        description="Pet species, e.g. 'dog' or 'cat'.",
        examples=["dog"],
    )
    member_id: str = Field(
        ...,
        description="Member identifier for routing; never logged with clinical content.",
        examples=["MBR-00123"],
    )


# ---------------------------------------------------------------------------
# Job status
# ---------------------------------------------------------------------------

class JobStatus(BaseModel):
    """Lightweight status returned while processing is in progress."""

    review_id: str
    status: str = Field(
        ...,
        description="One of: queued | processing | completed | failed | cancelled",
    )
    progress_pct: int = Field(default=0, ge=0, le=100)
    current_pass: Optional[str] = None
    page_count: Optional[int] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Timeline / conditions
# ---------------------------------------------------------------------------

class TimelineEvent(BaseModel):
    """A single temporal medical event extracted from the record."""

    date: Optional[str] = None
    event_type: str
    description: str
    diagnoses: list[str] = Field(default_factory=list)
    treatments: list[str] = Field(default_factory=list)
    outcomes: list[str] = Field(default_factory=list)


class ConditionEntry(BaseModel):
    """A deduplicated medical condition with pre-existing and chronic flags."""

    condition_name: str
    first_noted_date: Optional[str] = None
    last_noted_date: Optional[str] = None
    is_pre_existing: bool = False
    pre_existing_rule: Optional[str] = None  # PE-01 .. PE-08
    is_chronic: bool = False
    chronic_rule: Optional[str] = None       # CD-01 .. CD-07
    icd10_code: Optional[str] = None
    occurrence_count: int = 1
    treatments: list[str] = Field(default_factory=list)
    current_status: str = Field(
        default="unknown",
        description="One of: active | resolved | monitoring | unknown",
    )


# ---------------------------------------------------------------------------
# Final review result
# ---------------------------------------------------------------------------

class ReviewResult(BaseModel):
    """Full result returned when status == 'completed'."""

    review_id: str
    status: str
    page_count: int
    date_range: dict[str, Any] = Field(
        default_factory=lambda: {"earliest_date": None, "latest_date": None},
        description="Keys: earliest_date, latest_date (ISO strings or None).",
    )
    timeline_events: list[TimelineEvent] = Field(default_factory=list)
    identified_conditions: list[ConditionEntry] = Field(default_factory=list)
    pre_existing_conditions: list[ConditionEntry] = Field(default_factory=list)
    chronic_conditions: list[ConditionEntry] = Field(default_factory=list)
    summary: str = Field(
        default="",
        description="Plain-language 3-5 paragraph customer-friendly summary.",
    )
    processing_time_ms: int = 0
    model_used: str = "llama3.3:70b-instruct-q4_K_M"
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_at: Optional[str] = None
