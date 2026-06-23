"""
Pydantic v2 models for UC-03 Automated Medical Coding service.
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field, computed_field, model_validator


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

class CodingRequest(BaseModel):
    """Incoming request to code a clinical note."""

    note_id: Optional[str] = Field(default=None, description="Caller-supplied note identifier")
    clinical_note: str = Field(..., description="Full clinical note text to be coded")
    species: str = Field(
        ...,
        description="Animal species: canine | feline | avian | other (required for CR-04/CR-10)",
    )
    patient_age_years: Optional[float] = Field(default=None, ge=0.0)
    visit_date: Optional[str] = Field(default=None, description="ISO-8601 date, e.g. 2024-03-15")
    request_id: Optional[str] = Field(default=None, description="Caller-supplied idempotency ID")

    @model_validator(mode="after")
    def _normalise_species(self) -> "CodingRequest":
        self.species = self.species.strip().lower()
        return self


# ---------------------------------------------------------------------------
# Coded finding (one row per clinical concept)
# ---------------------------------------------------------------------------

def _tier_from_score(score: float) -> str:
    """CR-07: HIGH >= 0.85 | MEDIUM 0.60-0.84 | LOW < 0.60"""
    if score >= 0.85:
        return "HIGH"
    if score >= 0.60:
        return "MEDIUM"
    return "LOW"


class CodedFinding(BaseModel):
    """A single clinical concept with its coded representation."""

    concept: str = Field(..., description="Extracted clinical concept text")

    # SNOMED-CT
    snomed_code: str = Field(
        ...,
        description='SNOMED-CT concept ID, e.g. "73211009", or "UNMAPPED" (CR-08)',
    )
    snomed_display: str = Field(..., description="Human-readable SNOMED-CT display name")

    # ICD-10-CM
    icd10_code: str = Field(
        ...,
        description='ICD-10-CM code, e.g. "E11.9", or "UNMAPPED" (CR-08)',
    )
    icd10_display: str = Field(..., description="Human-readable ICD-10-CM display name")

    # Confidence
    confidence_score: float = Field(..., ge=0.0, le=1.0)

    @computed_field  # type: ignore[misc]
    @property
    def confidence_tier(self) -> str:
        """CR-07: derived automatically from confidence_score."""
        return _tier_from_score(self.confidence_score)

    # Diagnosis flags
    is_primary_diagnosis: bool = Field(
        default=False,
        description="CR-05: True if this is the primary diagnosis for the visit",
    )
    is_pre_existing: bool = Field(
        default=False,
        description="CR-06: True when note indicates pre-existing / prior history",
    )
    is_negated: bool = Field(
        default=False,
        description="CR-01: True for conditions ruled out or absent",
    )
    is_historical: bool = Field(
        default=False,
        description='CR-02: True for "history of" / "previously treated" conditions',
    )
    is_suspected: bool = Field(
        default=False,
        description='CR-03: True for "possible" / "suspected" conditions (confidence capped at 0.70)',
    )

    # Context
    body_system: Optional[str] = Field(
        default=None,
        description='Anatomical system, e.g. "endocrine", "musculoskeletal"',
    )
    procedure_codes: list[str] = Field(
        default_factory=list,
        description="CR-09: CPT-style procedure codes where applicable",
    )

    @model_validator(mode="after")
    def _enforce_suspected_cap(self) -> "CodedFinding":
        """CR-03: confidence_score must not exceed 0.70 for suspected findings."""
        if self.is_suspected and self.confidence_score > 0.70:
            self.confidence_score = 0.70
        return self


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------

class CodingResponse(BaseModel):
    """Full coding response returned to the caller."""

    request_id: str
    note_id: Optional[str] = None
    species: str

    coded_findings: list[CodedFinding] = Field(default_factory=list)
    primary_diagnosis: Optional[CodedFinding] = Field(
        default=None,
        description="CR-05: The single primary diagnosis, if identified",
    )
    unmapped_concepts: list[str] = Field(
        default_factory=list,
        description="CR-08: Concepts where snomed_code or icd10_code is UNMAPPED",
    )

    processing_time_ms: int = Field(..., ge=0)
    model_used: str
    pass1_concepts_found: int = Field(..., ge=0, description="Number of concepts extracted in Pass 1")
    pass2_codes_mapped: int = Field(
        ..., ge=0, description="Number of concepts successfully mapped in Pass 2"
    )
