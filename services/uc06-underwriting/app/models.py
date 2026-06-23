"""
Pydantic v2 models for UC-06 Multi-Agent Risk Underwriting service.
PHI note: applicant_name and pet_name are present on the request but MUST NOT appear in logs.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class CoverageType(str, Enum):
    BASIC = "BASIC"
    STANDARD = "STANDARD"
    PREMIUM = "PREMIUM"
    COMPREHENSIVE = "COMPREHENSIVE"


class UnderwritingDecision(str, Enum):
    APPROVE = "APPROVE"
    DECLINE = "DECLINE"
    REFER = "REFER"


class FraudRiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class JobState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Sub-models: request
# ---------------------------------------------------------------------------


class VetRecord(BaseModel):
    clinic_name: str = Field(..., min_length=1)
    visit_date: str = Field(..., description="ISO date string, e.g. 2024-03-15")
    summary: str = Field(..., min_length=1)

    @field_validator("visit_date")
    @classmethod
    def validate_visit_date(cls, v: str) -> str:
        # Validate parsable ISO date; store as-is (string) to avoid PHI enrichment
        date.fromisoformat(v)
        return v


class UnderwritingRequest(BaseModel):
    application_id: str = Field(..., min_length=1)
    applicant_name: str = Field(..., min_length=1)
    pet_name: str = Field(..., min_length=1)
    species: str = Field(..., min_length=1)
    breed: str = Field(..., min_length=1)
    date_of_birth: str = Field(..., description="Pet date of birth, ISO date string")
    vet_records: list[VetRecord] = Field(default_factory=list)
    requested_coverage_type: CoverageType
    requested_annual_benefit: float = Field(..., gt=0)
    application_date: str = Field(..., description="ISO date string")

    @field_validator("date_of_birth", "application_date")
    @classmethod
    def validate_iso_dates(cls, v: str) -> str:
        date.fromisoformat(v)
        return v


# ---------------------------------------------------------------------------
# Agent result models
# ---------------------------------------------------------------------------


class VetTechResult(BaseModel):
    conditions_found: list[str] = Field(default_factory=list)
    pre_existing_count: int = Field(default=0, ge=0)
    severity_score: float = Field(default=0.0, ge=0.0, le=10.0)
    flags: list[str] = Field(default_factory=list)


class FraudRiskResult(BaseModel):
    fraud_risk_level: FraudRiskLevel = FraudRiskLevel.LOW
    signals: list[str] = Field(default_factory=list)
    fraud_score: float = Field(default=0.0, ge=0.0, le=10.0)


class ActuarialResult(BaseModel):
    risk_score: float = Field(..., ge=0.0, le=10.0)
    breed_factor: float = Field(..., gt=0.0)
    age_factor: float = Field(..., gt=0.0)
    health_factor: float = Field(..., gt=0.0)
    estimated_premium: float = Field(..., ge=0.0)
    recommended_deductible: float = Field(..., ge=0.0)


class UnderwriterResult(BaseModel):
    preliminary_decision: UnderwritingDecision
    conditions: list[str] = Field(default_factory=list)
    coverage_modifications: list[str] = Field(default_factory=list)
    rationale: str = Field(default="")


class ComplianceResult(BaseModel):
    is_compliant: bool
    gdpr_explanation_required: bool
    explanation_for_adverse: str | None = None
    compliance_notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Override request
# ---------------------------------------------------------------------------


class OverrideRequest(BaseModel):
    underwriter_id: str = Field(..., min_length=1)
    new_decision: UnderwritingDecision
    rationale: str = Field(..., min_length=10)
    override_conditions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Final underwriting package
# ---------------------------------------------------------------------------


class UnderwritingPackage(BaseModel):
    job_id: str
    application_id: str
    underwriting_decision: UnderwritingDecision
    overall_verdict: UnderwritingDecision
    risk_score: float
    estimated_premium: float | None = None
    recommended_deductible: float | None = None
    pre_existing_exclusions: list[str] = Field(default_factory=list)
    coverage_modifications: list[str] = Field(default_factory=list)
    conditions_for_approval: list[str] = Field(default_factory=list)
    decline_codes: list[str] = Field(default_factory=list)
    explanation_for_adverse: str | None = None  # MANDATORY for DECLINE/REFER — GDPR Art.22
    compliance_validation: ComplianceResult
    agent_outputs: dict[str, Any] = Field(default_factory=dict)
    processing_time_ms: int = 0
    requires_manual_review: bool = False
    override_history: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Job status (polling response)
# ---------------------------------------------------------------------------


class JobStatus(BaseModel):
    job_id: str
    state: JobState
    progress_pct: int = Field(default=0, ge=0, le=100)
    current_phase: str = ""
    error: str | None = None
    result: UnderwritingPackage | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
