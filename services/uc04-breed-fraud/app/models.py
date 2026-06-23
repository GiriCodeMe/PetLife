"""
Pydantic v2 models for UC-04 Breed Fraud Verification service.
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class TopBreed(BaseModel):
    breed: str
    confidence: float = Field(ge=0.0, le=1.0)


class BreedAssessment(BaseModel):
    declared_breed: str
    detected_breed: str
    breed_confidence: float = Field(ge=0.0, le=1.0)
    top_breeds: list[TopBreed]
    is_mixed_breed: bool
    species_match: bool


class FraudSignal(BaseModel):
    rule_id: str
    description: str
    severity: str  # LOW | MEDIUM | HIGH | CRITICAL
    evidence: Optional[str] = None


class ImageQuality(BaseModel):
    width: int
    height: int
    format: str
    is_too_small: bool
    estimated_blur_score: float = Field(ge=0.0, le=1.0)
    quality_flag: bool


class VerificationResponse(BaseModel):
    request_id: str
    policy_id: str
    overall_verdict: str
    risk_tier: int = Field(ge=1, le=5)
    breed_assessment: BreedAssessment
    fraud_signals: list[FraudSignal]
    image_quality: ImageQuality
    requires_manual_review: bool
    processing_time_ms: int
    model_used: str
