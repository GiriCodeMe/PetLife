"""
Pydantic v2 models for UC-02 Claims Adjudication service.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Invoice sub-models (mirroring UC-01 InvoiceData structure)
# ---------------------------------------------------------------------------

class LineItem(BaseModel):
    """A single line item on a veterinary invoice."""

    description: str = Field(..., description="Line item description")
    procedure_code: Optional[str] = Field(None, description="Procedure or billing code (e.g. CPT)")
    diagnosis_code: Optional[str] = Field(None, description="ICD-10 or veterinary diagnosis code")
    category: str = Field(..., description="Benefit category (e.g. 'dental', 'preventive', 'illness')")
    amount: float = Field(..., gt=0, description="Billed amount in USD")
    date_of_service: str = Field(..., description="ISO date of service (YYYY-MM-DD)")


class InvoiceData(BaseModel):
    """Full invoice from a veterinary provider (output of UC-01 extraction)."""

    invoice_number: Optional[str] = Field(None)
    provider_name: Optional[str] = Field(None)
    provider_npi: Optional[str] = Field(None)
    date_of_service: str = Field(..., description="Primary date of service (ISO date)")
    line_items: list[LineItem] = Field(..., min_length=1)
    total_amount: float = Field(..., gt=0)
    currency: str = Field(default="USD")


# ---------------------------------------------------------------------------
# Policy model (loaded from store or DB)
# ---------------------------------------------------------------------------

class PolicyRecord(BaseModel):
    """Policy configuration used during adjudication."""

    policy_id: str
    member_id: str
    pet_name: str
    species: str
    breed: str
    policy_start: str = Field(..., description="ISO date policy became effective")

    annual_benefit_max: float = Field(..., description="Maximum annual payout")
    annual_benefit_used: float = Field(default=0.0, description="Amount already paid this benefit year")

    deductible: float = Field(..., description="Annual deductible amount")
    deductible_met: float = Field(default=0.0, description="Portion of deductible already satisfied")

    coinsurance_pct: float = Field(..., ge=0.0, le=1.0, description="Insurer share, e.g. 0.80")

    per_incident_max: Optional[float] = Field(None, description="Per-incident benefit cap; None = unlimited")

    category_limits: dict[str, float] = Field(
        default_factory=dict,
        description="Annual limit per benefit category, e.g. {'dental': 500}",
    )
    category_spent: dict[str, float] = Field(
        default_factory=dict,
        description="Amount already paid per benefit category this year",
    )

    excluded_codes: list[str] = Field(
        default_factory=list,
        description="Procedure codes that are always excluded",
    )
    excluded_conditions: list[str] = Field(
        default_factory=list,
        description="Diagnosis codes or condition names that are always excluded",
    )
    waiting_periods: dict[str, int] = Field(
        default_factory=dict,
        description="Waiting period in days by category, e.g. {'dental': 14}",
    )


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class AdjudicationRequest(BaseModel):
    """Inbound adjudication request."""

    claim_id: str = Field(..., description="Unique identifier for this claim")
    member_id: str = Field(..., description="Policy-holder identifier")
    policy_id: str = Field(..., description="Policy to adjudicate against")
    submission_date: str = Field(..., description="ISO date the claim was submitted")
    invoice: dict = Field(..., description="Raw InvoiceData dict (from UC-01 extraction)")


class LineDecision(BaseModel):
    """Per-line adjudication outcome."""

    line_description: str
    original_amount: float
    eligible_amount: float = Field(..., description="Amount after category/incident/annual caps")
    approved_amount: float = Field(..., description="Final paid amount after coinsurance")
    denial_reason: Optional[str] = Field(None)
    applied_rules: list[str] = Field(default_factory=list)


class AdjudicationResponse(BaseModel):
    """Full adjudication decision returned to caller."""

    claim_id: str
    decision: str = Field(..., description="APPROVED | PARTIAL | DENIED")
    approved_amount: float
    denied_amount: float
    line_decisions: list[LineDecision]
    denial_reasons: list[str] = Field(default_factory=list)
    explanation: str = Field(
        default="",
        description="Plain-English customer-facing explanation (LLM-generated for DENIED/PARTIAL)",
    )
    processing_time_ms: int
    model_used: Optional[str] = Field(None)
