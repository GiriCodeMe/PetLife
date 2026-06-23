"""
Specialist agent implementations for UC-06 underwriting pipeline.

Each agent:
  - Builds a structured prompt with application context
  - Calls Ollama qwen3:32b-q4_K_M via httpx (temperature=0.0)
  - Parses JSON response into the appropriate Pydantic v2 model
  - Timeout: 120 seconds per agent call

PHI RULE: Applicant name and pet name are intentionally excluded from prompts
          sent to the LLM to minimise PHI exposure.
"""

from __future__ import annotations

import json
import logging
import os
import textwrap

import httpx

from app.models import (
    ActuarialResult,
    ComplianceResult,
    FraudRiskResult,
    UnderwriterResult,
    UnderwritingRequest,
    VetTechResult,
)

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
SPECIALIST_MODEL = os.getenv("SPECIALIST_MODEL", "qwen3:32b-q4_K_M")
AGENT_TIMEOUT_SECONDS = float(os.getenv("AGENT_TIMEOUT_SECONDS", "120"))

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _call_ollama(prompt: str, model: str) -> str:
    """
    POST to Ollama /api/generate with the given prompt.
    Returns the raw text content of the response.
    Raises httpx.HTTPError on transport failure.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.0,
            "seed": 42,
        },
    }
    async with httpx.AsyncClient(timeout=AGENT_TIMEOUT_SECONDS) as client:
        resp = await client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "")


def _extract_json(text: str) -> dict:
    """
    Extract the first JSON object from a model response that may contain
    markdown fences or explanatory prose.
    """
    # Strip markdown code fences if present
    cleaned = text.strip()
    if "```" in cleaned:
        # Grab content between first ``` and last ```
        start = cleaned.find("```")
        end = cleaned.rfind("```")
        if start != end:
            inner = cleaned[start + 3 : end]
            # Strip optional language tag (e.g. "json\n")
            if inner.startswith("json"):
                inner = inner[4:]
            cleaned = inner.strip()

    # Find outermost braces
    brace_start = cleaned.find("{")
    brace_end = cleaned.rfind("}")
    if brace_start == -1 or brace_end == -1:
        raise ValueError(f"No JSON object found in model response: {cleaned[:200]}")
    return json.loads(cleaned[brace_start : brace_end + 1])


# ---------------------------------------------------------------------------
# Agent A: VetTechAgent
# ---------------------------------------------------------------------------


async def run_vet_tech_agent(
    application: UnderwritingRequest, model: str = SPECIALIST_MODEL
) -> VetTechResult:
    """
    Reviews submitted vet records, identifies health conditions,
    flags pre-existing conditions, and assigns a severity score.
    """
    records_text = "\n".join(
        f"- [{r.visit_date}] {r.clinic_name}: {r.summary}"
        for r in application.vet_records
    ) or "No vet records submitted."

    prompt = textwrap.dedent(f"""
        You are a veterinary clinical data analyst performing insurance underwriting review.

        TASK: Analyse the following veterinary records for a {application.species}
        (breed: {application.breed}, DOB: {application.date_of_birth}).
        Coverage requested: {application.requested_coverage_type}.

        VET RECORDS:
        {records_text}

        INSTRUCTIONS:
        1. List all health conditions identified.
        2. Count how many are clearly pre-existing (diagnosed before the application date: {application.application_date}).
        3. Assign a severity_score from 0.0 (no issues) to 10.0 (critical / life-limiting).
        4. List any flags (e.g., "chronic_condition", "genetic_disorder", "recent_surgery").

        Respond ONLY with a valid JSON object matching this schema — no prose, no markdown:
        {{
          "conditions_found": ["<condition1>", ...],
          "pre_existing_count": <integer>,
          "severity_score": <float 0.0-10.0>,
          "flags": ["<flag1>", ...]
        }}
    """).strip()

    raw = await _call_ollama(prompt, model)
    parsed = _extract_json(raw)
    return VetTechResult.model_validate(parsed)


# ---------------------------------------------------------------------------
# Agent B: FraudRiskAgent
# ---------------------------------------------------------------------------


async def run_fraud_risk_agent(
    application: UnderwritingRequest, model: str = SPECIALIST_MODEL
) -> FraudRiskResult:
    """
    Analyses the application for fraud signals such as mismatched data,
    suspicious patterns, or inconsistencies.
    PHI: applicant_name excluded from prompt; only structural metadata used.
    """
    has_vet_records = len(application.vet_records) > 0
    clinic_names = list({r.clinic_name for r in application.vet_records})
    visit_dates = [r.visit_date for r in application.vet_records]

    prompt = textwrap.dedent(f"""
        You are a fraud risk analyst for a pet insurance underwriting team.

        TASK: Assess fraud risk for the following application metadata.

        APPLICATION DETAILS (no PII included):
        - application_id: {application.application_id}
        - species: {application.species}
        - breed: {application.breed}
        - pet_date_of_birth: {application.date_of_birth}
        - application_date: {application.application_date}
        - vet_records_submitted: {has_vet_records}
        - number_of_vet_records: {len(application.vet_records)}
        - distinct_clinics: {clinic_names}
        - visit_dates: {visit_dates}
        - requested_coverage_type: {application.requested_coverage_type}
        - requested_annual_benefit: {application.requested_annual_benefit}

        FRAUD SIGNALS TO CHECK:
        - Application submitted immediately before or after a vet visit (< 7 days)
        - Unusually high benefit amount for species/breed
        - Multiple vet records from different clinics in a short period
        - Application date inconsistencies
        - Benefit amount grossly disproportionate to typical costs

        Respond ONLY with a valid JSON object matching this schema — no prose, no markdown:
        {{
          "fraud_risk_level": "LOW" | "MEDIUM" | "HIGH",
          "signals": ["<signal description>", ...],
          "fraud_score": <float 0.0-10.0>
        }}
    """).strip()

    raw = await _call_ollama(prompt, model)
    parsed = _extract_json(raw)
    return FraudRiskResult.model_validate(parsed)


# ---------------------------------------------------------------------------
# Agent C: ActuarialAgent
# ---------------------------------------------------------------------------


async def run_actuarial_agent(
    application: UnderwritingRequest,
    vet_result: VetTechResult,
    model: str = SPECIALIST_MODEL,
) -> ActuarialResult:
    """
    Calculates risk score and premium estimate using breed, age, species,
    and identified health conditions from the VetTech agent.
    Formula: base_premium * breed_factor * age_factor * health_factor
    """
    prompt = textwrap.dedent(f"""
        You are an actuarial analyst specialising in pet insurance risk modelling.

        TASK: Calculate the insurance risk score and premium estimate.

        APPLICATION DATA:
        - species: {application.species}
        - breed: {application.breed}
        - pet_date_of_birth: {application.date_of_birth}
        - application_date: {application.application_date}
        - requested_coverage_type: {application.requested_coverage_type}
        - requested_annual_benefit: {application.requested_annual_benefit}

        VETERINARY ASSESSMENT:
        - conditions_found: {vet_result.conditions_found}
        - pre_existing_count: {vet_result.pre_existing_count}
        - severity_score: {vet_result.severity_score}
        - flags: {vet_result.flags}

        INSTRUCTIONS:
        1. Calculate breed_factor (1.0 = baseline; higher = riskier breed)
        2. Calculate age_factor (1.0 = young adult; increases with age)
        3. Calculate health_factor (1.0 = healthy; increases with conditions)
        4. Derive overall risk_score (0.0-10.0); 10.0 = highest risk
        5. Estimate annual premium: base rate for coverage type * breed_factor * age_factor * health_factor
        6. Recommend a deductible amount

        Base rates by coverage type:
          BASIC=200, STANDARD=400, PREMIUM=700, COMPREHENSIVE=1100 (GBP per year)

        Respond ONLY with a valid JSON object matching this schema — no prose, no markdown:
        {{
          "risk_score": <float 0.0-10.0>,
          "breed_factor": <float>,
          "age_factor": <float>,
          "health_factor": <float>,
          "estimated_premium": <float>,
          "recommended_deductible": <float>
        }}
    """).strip()

    raw = await _call_ollama(prompt, model)
    parsed = _extract_json(raw)
    return ActuarialResult.model_validate(parsed)


# ---------------------------------------------------------------------------
# Agent D: UnderwriterAgent
# ---------------------------------------------------------------------------


async def run_underwriter_agent(
    application: UnderwritingRequest,
    actuarial: ActuarialResult,
    vet_result: VetTechResult,
    model: str = SPECIALIST_MODEL,
) -> UnderwriterResult:
    """
    Makes a preliminary underwriting decision (APPROVE/DECLINE/REFER)
    based on actuarial risk score, vet findings, and coverage request.
    Note: deterministic rule checks (UR-01..UR-12) are applied AFTER
    this agent in the orchestrator; this agent provides LLM reasoning.
    """
    prompt = textwrap.dedent(f"""
        You are a senior pet insurance underwriter.

        TASK: Make a preliminary underwriting decision.

        APPLICATION:
        - species: {application.species}
        - breed: {application.breed}
        - pet_date_of_birth: {application.date_of_birth}
        - coverage_type: {application.requested_coverage_type}
        - annual_benefit: {application.requested_annual_benefit}
        - vet_records_count: {len(application.vet_records)}

        VETERINARY FINDINGS:
        - conditions: {vet_result.conditions_found}
        - pre_existing_count: {vet_result.pre_existing_count}
        - severity_score: {vet_result.severity_score}
        - flags: {vet_result.flags}

        ACTUARIAL ASSESSMENT:
        - risk_score: {actuarial.risk_score} / 10.0
        - breed_factor: {actuarial.breed_factor}
        - age_factor: {actuarial.age_factor}
        - health_factor: {actuarial.health_factor}
        - estimated_premium: {actuarial.estimated_premium}
        - recommended_deductible: {actuarial.recommended_deductible}

        DECISION GUIDELINES:
        - APPROVE: risk_score < 6.0 AND no serious fraud signals AND breed not excluded
        - REFER: risk_score 6.0-8.4 OR 3+ pre-existing conditions OR fraud signal
        - DECLINE: risk_score >= 8.5 OR excluded breed OR age exceeds maximum

        List any conditions for approval (e.g., waiting periods, exclusions).
        List any coverage modifications needed.
        Provide a clear rationale.

        Respond ONLY with a valid JSON object matching this schema — no prose, no markdown:
        {{
          "preliminary_decision": "APPROVE" | "DECLINE" | "REFER",
          "conditions": ["<condition>", ...],
          "coverage_modifications": ["<modification>", ...],
          "rationale": "<clear explanation>"
        }}
    """).strip()

    raw = await _call_ollama(prompt, model)
    parsed = _extract_json(raw)
    return UnderwriterResult.model_validate(parsed)


# ---------------------------------------------------------------------------
# Agent E: ComplianceAgent
# ---------------------------------------------------------------------------


async def run_compliance_agent(
    application: UnderwritingRequest,
    preliminary_decision: str,
    underwriter: UnderwriterResult,
    model: str = SPECIALIST_MODEL,
) -> ComplianceResult:
    """
    Validates the underwriting decision against regulatory rules.
    GDPR Art.22: explanation_for_adverse MUST be populated for DECLINE or REFER.
    """
    gdpr_required = preliminary_decision in ("DECLINE", "REFER")

    prompt = textwrap.dedent(f"""
        You are a regulatory compliance specialist for pet insurance.

        TASK: Validate this underwriting decision for regulatory compliance.

        DECISION DETAILS:
        - species: {application.species}
        - breed: {application.breed}
        - coverage_type: {application.requested_coverage_type}
        - preliminary_decision: {preliminary_decision}
        - underwriter_rationale: {underwriter.rationale}
        - conditions: {underwriter.conditions}
        - coverage_modifications: {underwriter.coverage_modifications}

        COMPLIANCE REQUIREMENTS:
        1. GDPR Article 22: Any DECLINE or REFER decision based on automated processing
           MUST include a clear, human-readable explanation of the specific reasons,
           the logic involved, and the significance of the decision for the applicant.
           gdpr_explanation_required = {gdpr_required}

        2. FCA ICOBS rules: Decisions must be fair, clear, and not misleading.
        3. Breed exclusions must reference approved exclusion codes.
        4. Pre-existing condition exclusions must be specifically listed.

        INSTRUCTIONS:
        - Determine if the decision is compliant (is_compliant: true/false)
        - If gdpr_explanation_required is true, write a clear explanation_for_adverse
          (minimum 50 words) that explains WHY the decision was made and what factors
          were considered. This MUST be populated — it cannot be null or empty.
        - List any compliance notes or required corrections

        Respond ONLY with a valid JSON object matching this schema — no prose, no markdown:
        {{
          "is_compliant": true | false,
          "gdpr_explanation_required": {str(gdpr_required).lower()},
          "explanation_for_adverse": "<explanation string, or null if not required>",
          "compliance_notes": ["<note>", ...]
        }}
    """).strip()

    raw = await _call_ollama(prompt, model)
    parsed = _extract_json(raw)
    result = ComplianceResult.model_validate(parsed)

    # Hard guarantee: if decision is DECLINE/REFER and explanation is missing, flag non-compliance
    if gdpr_required and not result.explanation_for_adverse:
        result.is_compliant = False
        result.explanation_for_adverse = (
            "GDPR Art.22 explanation required but not generated. "
            "Manual review is mandatory before communicating this decision."
        )
        result.compliance_notes.append(
            "CRITICAL: GDPR Art.22 explanation was absent — flagged for mandatory manual review."
        )

    return result
