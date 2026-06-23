"""
LangGraph-style DAG orchestrator for UC-06 underwriting pipeline.

DAG phases:
  Phase 1 (PARALLEL):  VetTechAgent + FraudRiskAgent
  Phase 2 (SEQUENTIAL): ActuarialAgent
  Phase 3 (SEQUENTIAL): UnderwriterAgent
  Phase 4 (SEQUENTIAL): ComplianceAgent
  Phase 5 (FINAL):     Orchestrator assembles package + applies UR-01..UR-12 rules

PHI LOGGING RULE: Log ONLY job_id, species, breed, risk_score, decision, duration_ms.
                  NEVER log applicant_name, pet_name, or vet record content.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import textwrap
import time
from datetime import datetime
from typing import Any

import httpx

from app.agents import (
    run_actuarial_agent,
    run_compliance_agent,
    run_fraud_risk_agent,
    run_underwriter_agent,
    run_vet_tech_agent,
    _call_ollama,
    _extract_json,
)
from app.models import (
    ActuarialResult,
    ComplianceResult,
    FraudRiskResult,
    JobState,
    UnderwriterResult,
    UnderwritingDecision,
    UnderwritingPackage,
    UnderwritingRequest,
    VetTechResult,
)

logger = logging.getLogger(__name__)

ORCHESTRATOR_MODEL = os.getenv("ORCHESTRATOR_MODEL", "llama3.3:70b-instruct-q4_K_M")
SPECIALIST_MODEL = os.getenv("SPECIALIST_MODEL", "qwen3:32b-q4_K_M")

# ---------------------------------------------------------------------------
# Approved decline codes (UR-11)
# ---------------------------------------------------------------------------

APPROVED_DECLINE_CODES: dict[str, str] = {
    "DC-001": "Excluded breed under policy terms",
    "DC-002": "Pet age exceeds maximum insurable age for species",
    "DC-003": "Risk score exceeds maximum threshold (>= 8.5/10)",
    "DC-004": "Exotic species — specialist underwriting required",
    "DC-005": "Fraudulent application indicators detected",
    "DC-006": "Multiple serious pre-existing conditions (3+)",
    "DC-007": "Policy exclusion — incomplete application",
    "DC-008": "Duplicate policy detected for same pet",
}

# Excluded breeds (UR-01) — partial list; extend via config as needed
EXCLUDED_BREEDS: set[str] = {
    "pit bull",
    "pitbull",
    "american pit bull terrier",
    "american pit bull",
    "staffordshire bull terrier",  # only excluded in certain jurisdictions
    "tosa inu",
    "dogo argentino",
    "fila brasileiro",
    "presa canario",
}

# Maximum insurable ages by species in years (UR-02)
MAX_INSURABLE_AGE: dict[str, int] = {
    "dog": 10,
    "cat": 12,
    "rabbit": 6,
    "reptile": 99,  # handled by UR-09 (REFER) before age check is relevant
    "bird": 99,
}

# Exotic species requiring specialist referral (UR-09)
EXOTIC_SPECIES: set[str] = {"reptile", "bird", "lizard", "snake", "tortoise", "parrot", "gecko"}


# ---------------------------------------------------------------------------
# Progress event helper
# ---------------------------------------------------------------------------


async def _emit_progress(
    store: dict,
    job_id: str,
    phase: str,
    pct: int,
    message: str,
) -> None:
    """Push a progress event dict onto the job's asyncio.Queue."""
    event = {
        "phase": phase,
        "progress_pct": pct,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
    }
    try:
        store[job_id]["progress_queue"].put_nowait(event)
        # Also update the top-level status fields for GET polling
        store[job_id]["current_phase"] = phase
        store[job_id]["progress_pct"] = pct
    except asyncio.QueueFull:
        logger.warning("job=%s progress queue full, dropping event", job_id)


# ---------------------------------------------------------------------------
# Deterministic rule engine: UR-01 .. UR-12
# ---------------------------------------------------------------------------


def _apply_underwriting_rules(
    application: UnderwritingRequest,
    vet: VetTechResult,
    fraud: FraudRiskResult,
    actuarial: ActuarialResult,
    underwriter: UnderwriterResult,
    compliance: ComplianceResult,
) -> tuple[UnderwritingDecision, list[str], list[str], list[str], bool]:
    """
    Applies rules UR-01..UR-12 deterministically in Python (not LLM).
    Returns:
        (final_decision, decline_codes, conditions_for_approval, pre_existing_exclusions, requires_manual_review)
    """
    decision = underwriter.preliminary_decision
    decline_codes: list[str] = []
    conditions_for_approval: list[str] = list(underwriter.conditions)
    pre_existing_exclusions: list[str] = list(vet.conditions_found)
    requires_manual_review = False

    breed_lower = application.breed.lower()
    species_lower = application.species.lower()

    # UR-01: Excluded breeds → automatic DECLINE
    if any(excl in breed_lower for excl in EXCLUDED_BREEDS):
        decision = UnderwritingDecision.DECLINE
        decline_codes.append("DC-001")

    # UR-02: Age > max insurable age → DECLINE
    try:
        dob = datetime.fromisoformat(application.date_of_birth).date()
        app_date = datetime.fromisoformat(application.application_date).date()
        age_years = (app_date - dob).days / 365.25
        max_age = MAX_INSURABLE_AGE.get(species_lower, 10)
        if age_years > max_age:
            decision = UnderwritingDecision.DECLINE
            decline_codes.append("DC-002")
    except (ValueError, TypeError):
        logger.warning("job: age calculation failed for species=%s", application.species)

    # UR-03: 3+ serious pre-existing conditions → REFER
    if vet.pre_existing_count >= 3 and decision == UnderwritingDecision.APPROVE:
        decision = UnderwritingDecision.REFER
        requires_manual_review = True

    # UR-04: risk_score >= 8.5 → DECLINE
    if actuarial.risk_score >= 8.5:
        decision = UnderwritingDecision.DECLINE
        if "DC-003" not in decline_codes:
            decline_codes.append("DC-003")

    # UR-05: risk_score 6.0-8.4 → REFER (only if not already DECLINE)
    elif 6.0 <= actuarial.risk_score < 8.5 and decision == UnderwritingDecision.APPROVE:
        decision = UnderwritingDecision.REFER
        conditions_for_approval.append(
            f"Risk score {actuarial.risk_score:.1f} requires additional review"
        )

    # UR-06: risk_score < 6.0 → APPROVE (standard/preferred) — already captured in underwriter
    # No override needed here; only downgrade if rules above triggered

    # UR-07: Fraud flag → REFER + mandatory manual review
    if fraud.fraud_risk_level.value == "HIGH":
        if decision == UnderwritingDecision.APPROVE:
            decision = UnderwritingDecision.REFER
        requires_manual_review = True
        conditions_for_approval.append("Mandatory manual review: high fraud risk signal")

    # UR-08: No vet records → APPROVE with 12-month waiting period on pre-existing
    if len(application.vet_records) == 0 and decision == UnderwritingDecision.APPROVE:
        conditions_for_approval.append(
            "12-month waiting period applies to all pre-existing conditions (no vet records submitted)"
        )

    # UR-09: Exotic species → REFER
    if species_lower in EXOTIC_SPECIES:
        if decision == UnderwritingDecision.APPROVE:
            decision = UnderwritingDecision.REFER
        requires_manual_review = True
        conditions_for_approval.append("Exotic species: specialist underwriting required")

    # UR-09b: duplicate policy check is external; flag in conditions if application_id suggests it
    # (full duplicate check would require DB; note in conditions for external reconciliation)

    # UR-11: DECLINE decisions must have at least one decline code
    if decision == UnderwritingDecision.DECLINE and not decline_codes:
        decline_codes.append("DC-003")  # fallback: risk score

    # UR-12: override logging is handled in the API layer

    return decision, decline_codes, conditions_for_approval, pre_existing_exclusions, requires_manual_review


# ---------------------------------------------------------------------------
# Orchestrator final assembly
# ---------------------------------------------------------------------------


async def _orchestrate_final_package(
    job_id: str,
    application: UnderwritingRequest,
    vet: VetTechResult,
    fraud: FraudRiskResult,
    actuarial: ActuarialResult,
    underwriter: UnderwriterResult,
    compliance: ComplianceResult,
    start_time_ms: float,
) -> UnderwritingPackage:
    """
    Calls the orchestrator LLM (llama3.3:70b) to verify the assembled package,
    then applies deterministic UR-01..UR-12 rules.
    The LLM is advisory only; rule engine always wins on final decision.
    """
    # Build a summary for the orchestrator
    summary_prompt = textwrap.dedent(f"""
        You are the chief underwriting orchestrator. Review the specialist agent outputs below
        and confirm the final underwriting decision. Return ONLY valid JSON.

        SPECIES: {application.species}
        BREED: {application.breed}
        COVERAGE: {application.requested_coverage_type}
        ANNUAL BENEFIT: {application.requested_annual_benefit}

        AGENT SUMMARY:
        - VetTech: {vet.pre_existing_count} pre-existing conditions, severity {vet.severity_score}/10
        - Fraud: {fraud.fraud_risk_level} risk, score {fraud.fraud_score}/10
        - Actuarial: risk_score={actuarial.risk_score}/10, premium={actuarial.estimated_premium}
        - Underwriter: {underwriter.preliminary_decision} — {underwriter.rationale[:200]}
        - Compliance: compliant={compliance.is_compliant}

        Confirm the final decision and provide a brief overall_summary.

        {{
          "confirmed_decision": "APPROVE" | "DECLINE" | "REFER",
          "overall_summary": "<1-2 sentence summary>"
        }}
    """).strip()

    try:
        raw = await _call_ollama(summary_prompt, ORCHESTRATOR_MODEL)
        orch_data = _extract_json(raw)
        # Orchestrator suggestion logged but rules engine overrides
        logger.debug(
            "job=%s orchestrator_suggestion=%s",
            job_id,
            orch_data.get("confirmed_decision"),
        )
    except Exception as exc:
        logger.warning("job=%s orchestrator LLM failed, proceeding with rules: %s", job_id, exc)

    # Apply deterministic rules (always authoritative)
    final_decision, decline_codes, conditions, pre_existing_exclusions, requires_manual = (
        _apply_underwriting_rules(
            application, vet, fraud, actuarial, underwriter, compliance
        )
    )

    # Ensure GDPR Art.22 explanation for adverse decisions
    explanation = compliance.explanation_for_adverse
    if final_decision in (UnderwritingDecision.DECLINE, UnderwritingDecision.REFER):
        if not explanation:
            explanation = (
                f"Your application has received a {final_decision.value} decision. "
                f"The primary factors considered were: risk score of {actuarial.risk_score:.1f}/10.0, "
                f"{vet.pre_existing_count} pre-existing condition(s) identified in veterinary records, "
                f"fraud risk level of {fraud.fraud_risk_level.value}, "
                f"and breed/species classification. "
                f"Underwriter rationale: {underwriter.rationale}"
            )

    elapsed_ms = int((time.time() * 1000) - start_time_ms)

    package = UnderwritingPackage(
        job_id=job_id,
        application_id=application.application_id,
        underwriting_decision=final_decision,
        overall_verdict=final_decision,
        risk_score=actuarial.risk_score,
        estimated_premium=actuarial.estimated_premium if final_decision == UnderwritingDecision.APPROVE else None,
        recommended_deductible=actuarial.recommended_deductible if final_decision == UnderwritingDecision.APPROVE else None,
        pre_existing_exclusions=pre_existing_exclusions,
        coverage_modifications=list(underwriter.coverage_modifications),
        conditions_for_approval=conditions,
        decline_codes=decline_codes,
        explanation_for_adverse=explanation,
        compliance_validation=compliance,
        agent_outputs={
            "vet_tech": vet.model_dump(),
            "fraud_risk": fraud.model_dump(),
            "actuarial": actuarial.model_dump(),
            "underwriter": underwriter.model_dump(),
            "compliance": compliance.model_dump(),
        },
        processing_time_ms=elapsed_ms,
        requires_manual_review=requires_manual,
        override_history=[],
        completed_at=datetime.utcnow(),
    )
    return package


# ---------------------------------------------------------------------------
# Main pipeline entry point
# ---------------------------------------------------------------------------


async def run_underwriting_pipeline(
    job_id: str,
    application: UnderwritingRequest,
    store: dict,
) -> None:
    """
    Execute the full underwriting DAG for a given application.
    Updates store[job_id] with progress events and final result.

    DAG:
      Phase 1 (parallel): VetTechAgent + FraudRiskAgent
      Phase 2:            ActuarialAgent
      Phase 3:            UnderwriterAgent
      Phase 4:            ComplianceAgent
      Phase 5:            Orchestrator assembles final package
    """
    start_ms = time.time() * 1000

    try:
        store[job_id]["state"] = JobState.RUNNING

        # ------------------------------------------------------------------
        # Phase 1: Parallel — VetTech + FraudRisk
        # ------------------------------------------------------------------
        await _emit_progress(
            store, job_id,
            phase="Phase 1: VetTech + FraudRisk (parallel)",
            pct=5,
            message="Launching VetTech and FraudRisk agents in parallel",
        )

        vet_result: VetTechResult
        fraud_result: FraudRiskResult
        vet_result, fraud_result = await asyncio.gather(
            run_vet_tech_agent(application, SPECIALIST_MODEL),
            run_fraud_risk_agent(application, SPECIALIST_MODEL),
        )

        await _emit_progress(
            store, job_id,
            phase="Phase 1 complete",
            pct=30,
            message=(
                f"VetTech: {vet_result.pre_existing_count} pre-existing, "
                f"severity={vet_result.severity_score:.1f}. "
                f"Fraud: {fraud_result.fraud_risk_level.value} risk."
            ),
        )

        # ------------------------------------------------------------------
        # Phase 2: Actuarial
        # ------------------------------------------------------------------
        await _emit_progress(
            store, job_id,
            phase="Phase 2: ActuarialAgent",
            pct=35,
            message="Running actuarial risk calculation",
        )

        actuarial_result: ActuarialResult = await run_actuarial_agent(
            application, vet_result, SPECIALIST_MODEL
        )

        await _emit_progress(
            store, job_id,
            phase="Phase 2 complete",
            pct=55,
            message=f"Risk score: {actuarial_result.risk_score:.2f}/10.0, premium: {actuarial_result.estimated_premium:.2f}",
        )

        # ------------------------------------------------------------------
        # Phase 3: Underwriter
        # ------------------------------------------------------------------
        await _emit_progress(
            store, job_id,
            phase="Phase 3: UnderwriterAgent",
            pct=58,
            message="Running preliminary underwriting decision",
        )

        underwriter_result: UnderwriterResult = await run_underwriter_agent(
            application, actuarial_result, vet_result, SPECIALIST_MODEL
        )

        await _emit_progress(
            store, job_id,
            phase="Phase 3 complete",
            pct=75,
            message=f"Preliminary decision: {underwriter_result.preliminary_decision.value}",
        )

        # ------------------------------------------------------------------
        # Phase 4: Compliance
        # ------------------------------------------------------------------
        await _emit_progress(
            store, job_id,
            phase="Phase 4: ComplianceAgent",
            pct=78,
            message="Running regulatory compliance validation",
        )

        compliance_result: ComplianceResult = await run_compliance_agent(
            application,
            underwriter_result.preliminary_decision.value,
            underwriter_result,
            SPECIALIST_MODEL,
        )

        await _emit_progress(
            store, job_id,
            phase="Phase 4 complete",
            pct=88,
            message=f"Compliance: {'PASS' if compliance_result.is_compliant else 'FAIL'}",
        )

        # ------------------------------------------------------------------
        # Phase 5: Orchestrator final assembly
        # ------------------------------------------------------------------
        await _emit_progress(
            store, job_id,
            phase="Phase 5: Final assembly",
            pct=90,
            message="Orchestrator assembling final underwriting package",
        )

        package = await _orchestrate_final_package(
            job_id=job_id,
            application=application,
            vet=vet_result,
            fraud=fraud_result,
            actuarial=actuarial_result,
            underwriter=underwriter_result,
            compliance=compliance_result,
            start_time_ms=start_ms,
        )

        # Store result
        async with store["_lock"]:
            store[job_id]["result"] = package
            store[job_id]["state"] = JobState.COMPLETED
            store[job_id]["progress_pct"] = 100
            store[job_id]["current_phase"] = "Completed"

        await _emit_progress(
            store, job_id,
            phase="Completed",
            pct=100,
            message=f"Underwriting complete. Decision: {package.underwriting_decision.value}",
        )

        # Signal SSE stream to close
        store[job_id]["progress_queue"].put_nowait({"__done__": True})

        # PHI-safe log — only non-PHI fields
        elapsed_ms = int((time.time() * 1000) - start_ms)
        logger.info(
            "underwriting_complete job_id=%s species=%s breed=%s risk_score=%.2f "
            "decision=%s duration_ms=%d",
            job_id,
            application.species,
            application.breed,
            package.risk_score,
            package.underwriting_decision.value,
            elapsed_ms,
        )

    except Exception as exc:
        elapsed_ms = int((time.time() * 1000) - start_ms)
        logger.error(
            "underwriting_failed job_id=%s species=%s breed=%s duration_ms=%d error=%s",
            job_id,
            application.species,
            application.breed,
            elapsed_ms,
            str(exc),
        )
        async with store["_lock"]:
            store[job_id]["state"] = JobState.FAILED
            store[job_id]["error"] = str(exc)
            store[job_id]["current_phase"] = "Failed"

        store[job_id]["progress_queue"].put_nowait(
            {"__done__": True, "error": str(exc)}
        )
