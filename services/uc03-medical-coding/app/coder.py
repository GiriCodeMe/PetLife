"""
Two-pass LLM medical coding logic for UC-03.

Pass 1 — extract_concepts : identify clinical concepts in the note.
Pass 2 — map_codes         : assign SNOMED-CT + ICD-10-CM codes to each concept.
apply_coding_rules         : enforce CR-01 through CR-10 post-LLM.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from .models import CodedFinding

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ollama call helper
# ---------------------------------------------------------------------------

OLLAMA_BASE_URL = "http://localhost:11434"
_TEMPERATURE = 0.1
_TIMEOUT_S = 120.0  # coding can take a while for long notes


async def _ollama_generate(
    prompt: str,
    model: str,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """
    Call Ollama /api/generate with format=json and return the response string.
    Creates a one-shot client if none is provided.
    """
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": _TEMPERATURE,
            "num_predict": 4096,
        },
    }

    async def _post(client: httpx.AsyncClient) -> str:
        resp = await client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
            timeout=_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "")

    if http_client is not None:
        return await _post(http_client)

    async with httpx.AsyncClient() as client:
        return await _post(client)


def _parse_json_response(raw: str, context: str = "") -> Any:
    """
    Safely parse a JSON string returned by the LLM.
    The model is asked to return JSON directly (format=json), but may still
    wrap its answer in markdown fences — strip those first.
    """
    # Strip markdown code fences if present
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("JSON parse error [%s]: %s | raw=%r", context, exc, raw[:400])
        raise ValueError(f"LLM returned invalid JSON [{context}]: {exc}") from exc


# ---------------------------------------------------------------------------
# Pass 1 — concept extraction
# ---------------------------------------------------------------------------

_PASS1_SYSTEM = """\
You are a veterinary clinical coding specialist.
Your task is to extract every clinical concept from the provided note.
Return ONLY a valid JSON array — no prose, no markdown.
Each element must follow this exact schema:
{
  "concept": "<the clinical concept as stated>",
  "body_system": "<anatomical/physiological system or null>",
  "is_negated": <true if the concept is explicitly denied or ruled out>,
  "is_historical": <true if described as past history>,
  "is_suspected": <true if described as possible/suspected>,
  "is_primary": <true if this appears to be the primary reason for the visit>
}
Rules:
- Include conditions, diagnoses, procedures, symptoms, findings, and body-system references.
- Negated examples: "no evidence of X", "ruled out X" -> is_negated: true
- Historical examples: "history of X", "previously treated for X" -> is_historical: true
- Suspected examples: "possible X", "suspected X", "r/o X" -> is_suspected: true
- Do NOT include irrelevant administrative text.
- The species of the patient is: {species}
"""


async def extract_concepts(
    note: str,
    species: str,
    model: str,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """
    Pass 1: extract clinical concepts from *note*.

    PHI RULE: this function never logs *note* content.
    Returns a list of raw concept dicts from the LLM.
    """
    prompt = (
        _PASS1_SYSTEM.format(species=species)
        + "\n\n--- CLINICAL NOTE ---\n"
        + note
        + "\n--- END NOTE ---\n\n"
        "Return the JSON array now:"
    )

    raw = await _ollama_generate(prompt, model, http_client)
    parsed = _parse_json_response(raw, context="pass1")

    # Normalise: the LLM might return {"concepts": [...]} instead of [...]
    if isinstance(parsed, dict):
        for key in ("concepts", "findings", "results", "data"):
            if key in parsed and isinstance(parsed[key], list):
                parsed = parsed[key]
                break

    if not isinstance(parsed, list):
        logger.error("Pass 1 returned non-list type: %s", type(parsed))
        return []

    # Sanitise each entry
    safe: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict) or "concept" not in item:
            continue
        safe.append(
            {
                "concept": str(item.get("concept", "")),
                "body_system": item.get("body_system") or None,
                "is_negated": bool(item.get("is_negated", False)),
                "is_historical": bool(item.get("is_historical", False)),
                "is_suspected": bool(item.get("is_suspected", False)),
                "is_primary": bool(item.get("is_primary", False)),
            }
        )

    return safe


# ---------------------------------------------------------------------------
# Pass 2 — code mapping
# ---------------------------------------------------------------------------

_PASS2_SYSTEM = """\
You are a veterinary medical coding specialist with expertise in SNOMED-CT and ICD-10-CM.
The patient species is: {species}

IMPORTANT SPECIES-SPECIFIC RULES:
- For DIABETES MELLITUS in FELINE patients -> use ICD-10 code E11.9 (Type 2 diabetes) and SNOMED 73211009
- For DIABETES MELLITUS in CANINE patients -> use ICD-10 code E10.9 (Type 1 diabetes) and SNOMED 73211009
- Always apply the correct species-specific diabetes code (CR-04).

Your task is to map the following clinical concepts to SNOMED-CT and ICD-10-CM codes.
Return ONLY a valid JSON array — no prose, no markdown.
Each element must follow this exact schema:
{{
  "concept": "<the concept text, unchanged>",
  "snomed_code": "<SNOMED-CT numeric code or UNMAPPED>",
  "snomed_display": "<SNOMED-CT preferred display name>",
  "icd10_code": "<ICD-10-CM code or UNMAPPED>",
  "icd10_display": "<ICD-10-CM description>",
  "confidence_score": <float 0.0-1.0>,
  "procedure_codes": [<CPT-style code strings if applicable, else empty array>]
}}

Rules:
- Use "UNMAPPED" (not null, not empty string) if a code cannot be determined with confidence (CR-08).
- Procedure concepts should include CPT codes in procedure_codes (CR-09).
- confidence_score reflects your certainty in the code assignment, not the clinical certainty.
- For suspected conditions, cap confidence_score at 0.70 (CR-03 — handled downstream, but be accurate).

CONCEPTS TO MAP:
{concepts_json}
"""


async def map_codes(
    concepts: list[dict[str, Any]],
    species: str,
    model: str,
    http_client: httpx.AsyncClient | None = None,
) -> list[CodedFinding]:
    """
    Pass 2: map each concept to SNOMED-CT + ICD-10-CM codes.

    Merges Pass 1 flags (is_negated, is_historical, is_suspected, is_primary)
    with the coding output from the LLM.
    PHI RULE: concept text is from the note but is limited to short phrases,
    not logged here.
    """
    if not concepts:
        return []

    # Build a clean list for the LLM (only what it needs to code)
    concepts_for_llm = [
        {
            "concept": c["concept"],
            "body_system": c.get("body_system"),
            "is_negated": c.get("is_negated", False),
            "is_historical": c.get("is_historical", False),
            "is_suspected": c.get("is_suspected", False),
        }
        for c in concepts
    ]

    prompt = _PASS2_SYSTEM.format(
        species=species,
        concepts_json=json.dumps(concepts_for_llm, indent=2),
    )

    raw = await _ollama_generate(prompt, model, http_client)
    parsed = _parse_json_response(raw, context="pass2")

    # Normalise wrapper object
    if isinstance(parsed, dict):
        for key in ("findings", "codes", "results", "data", "mappings"):
            if key in parsed and isinstance(parsed[key], list):
                parsed = parsed[key]
                break

    if not isinstance(parsed, list):
        logger.error("Pass 2 returned non-list type: %s", type(parsed))
        return []

    # Build a lookup from concept text -> Pass 1 flags
    p1_flags: dict[str, dict[str, Any]] = {c["concept"]: c for c in concepts}

    findings: list[CodedFinding] = []
    for item in parsed:
        if not isinstance(item, dict) or "concept" not in item:
            continue

        concept_text = str(item.get("concept", ""))
        p1 = p1_flags.get(concept_text, {})

        raw_score = float(item.get("confidence_score", 0.5))
        raw_score = max(0.0, min(1.0, raw_score))

        try:
            finding = CodedFinding(
                concept=concept_text,
                snomed_code=str(item.get("snomed_code") or "UNMAPPED").strip() or "UNMAPPED",
                snomed_display=str(item.get("snomed_display") or ""),
                icd10_code=str(item.get("icd10_code") or "UNMAPPED").strip() or "UNMAPPED",
                icd10_display=str(item.get("icd10_display") or ""),
                confidence_score=raw_score,
                is_primary_diagnosis=bool(p1.get("is_primary", False)),
                is_pre_existing=False,  # determined by apply_coding_rules
                is_negated=bool(p1.get("is_negated", False)),
                is_historical=bool(p1.get("is_historical", False)),
                is_suspected=bool(p1.get("is_suspected", False)),
                body_system=p1.get("body_system") or None,
                procedure_codes=[
                    str(c) for c in item.get("procedure_codes", []) if c
                ],
            )
            findings.append(finding)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping malformed finding for concept=%r: %s", concept_text, exc)

    return findings


# ---------------------------------------------------------------------------
# CR-01 to CR-10 post-processing rules
# ---------------------------------------------------------------------------

_DIABETES_KEYWORDS = re.compile(r"\b(diabetes|diabetic|DM)\b", re.IGNORECASE)
_SUSPECTED_KEYWORDS = re.compile(
    r"\b(possible|suspected|probable|r/o|rule\s+out|query|?)\b", re.IGNORECASE
)
_HISTORICAL_KEYWORDS = re.compile(
    r"\b(history\s+of|previously\s+treated|prior\s+history|h/o|past\s+history)\b",
    re.IGNORECASE,
)
_NEGATED_KEYWORDS = re.compile(
    r"\b(no\s+evidence\s+of|ruled\s+out|absent|negative\s+for|denies|without)\b",
    re.IGNORECASE,
)
_PREEXISTING_KEYWORDS = re.compile(
    r"\b(pre-?existing|prior\s+history|pre-?existing\s+condition)\b", re.IGNORECASE
)

# SNOMED for diabetes (species-neutral concept; ICD code is species-specific per CR-04)
_DIABETES_SNOMED = "73211009"
_DIABETES_SNOMED_DISPLAY = "Diabetes mellitus"


def apply_coding_rules(
    findings: list[CodedFinding],
    species: str,
) -> list[CodedFinding]:
    """
    Apply coding rules CR-01 through CR-10.

    CR-01: is_negated concepts -> do not change codes but flag clearly
    CR-02: is_historical -> flag is_historical=True
    CR-03: is_suspected -> cap confidence at 0.70
    CR-04: species-specific diabetes ICD code
    CR-05: mark is_primary_diagnosis (highest-confidence non-negated finding if none set)
    CR-06: keyword-based is_pre_existing detection
    CR-07: confidence_tier is computed on the model (via @computed_field)
    CR-08: UNMAPPED sentinel already enforced in CodedFinding construction
    CR-09: procedure_codes already populated in Pass 2
    CR-10: species included in prompts (enforced in extract_concepts / map_codes)
    """
    updated: list[CodedFinding] = []

    for f in findings:
        # Work with a mutable copy via model_copy
        d = f.model_dump()

        concept_lower = d["concept"].lower()

        # CR-01: re-check negation keywords against concept text
        if _NEGATED_KEYWORDS.search(concept_lower):
            d["is_negated"] = True

        # CR-02: re-check historical keywords
        if _HISTORICAL_KEYWORDS.search(concept_lower):
            d["is_historical"] = True

        # CR-03: re-check suspected keywords and cap confidence
        if _SUSPECTED_KEYWORDS.search(concept_lower):
            d["is_suspected"] = True
        if d["is_suspected"] and d["confidence_score"] > 0.70:
            d["confidence_score"] = 0.70

        # CR-04: species-specific diabetes codes
        if _DIABETES_KEYWORDS.search(concept_lower):
            d["snomed_code"] = _DIABETES_SNOMED
            d["snomed_display"] = _DIABETES_SNOMED_DISPLAY
            if species == "feline":
                d["icd10_code"] = "E11.9"
                d["icd10_display"] = "Type 2 diabetes mellitus without complications"
            elif species == "canine":
                d["icd10_code"] = "E10.9"
                d["icd10_display"] = "Type 1 diabetes mellitus without complications"
            # other species: keep whatever the LLM assigned

        # CR-06: pre-existing detection
        if _PREEXISTING_KEYWORDS.search(concept_lower):
            d["is_pre_existing"] = True

        # Re-construct to trigger validators (e.g. CR-03 cap via model_validator)
        updated.append(CodedFinding.model_validate(d))

    # CR-05: ensure exactly one primary diagnosis is marked.
    # If the LLM already flagged one, respect it.
    # If none, promote the highest-confidence non-negated, non-suspected finding.
    primary_count = sum(1 for f in updated if f.is_primary_diagnosis)
    if primary_count == 0:
        candidates = [
            f for f in updated if not f.is_negated and not f.is_suspected
        ]
        if candidates:
            best = max(candidates, key=lambda f: f.confidence_score)
            rebuilt: list[CodedFinding] = []
            for f in updated:
                if f.concept == best.concept and not f.is_negated:
                    d2 = f.model_dump()
                    d2["is_primary_diagnosis"] = True
                    rebuilt.append(CodedFinding.model_validate(d2))
                else:
                    rebuilt.append(f)
            updated = rebuilt
    elif primary_count > 1:
        # Disambiguate: keep only the highest-confidence one
        primary_candidates = [f for f in updated if f.is_primary_diagnosis]
        best_primary = max(primary_candidates, key=lambda f: f.confidence_score)
        rebuilt = []
        for f in updated:
            if f.is_primary_diagnosis and f.concept != best_primary.concept:
                d2 = f.model_dump()
                d2["is_primary_diagnosis"] = False
                rebuilt.append(CodedFinding.model_validate(d2))
            else:
                rebuilt.append(f)
        updated = rebuilt

    return updated
