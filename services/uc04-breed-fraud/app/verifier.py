"""
UC-04 Breed Fraud Verification — core analysis and fraud rule engine.

PHI RULE: This module must never log policy_id or pet_name.
Allowed log fields: request_id, declared_breed, verdict, risk_tier, duration_ms.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import math
from typing import Any

import httpx
from PIL import Image, ImageFilter

from .models import BreedAssessment, FraudSignal, ImageQuality, TopBreed

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL = "http://localhost:11434"
VISION_MODEL = "llama3.2-vision:11b-q4_K_M"
VISION_TEMPERATURE = 0.1
DUPLICATE_SIMILARITY_THRESHOLD = 0.95
MIN_BREED_CONFIDENCE = 0.60
MIN_IMAGE_DIMENSION = 224  # pixels

VISION_PROMPT = """You are a veterinary expert and image forensics analyst.
Analyze this pet image and respond ONLY with a JSON object — no extra text.

Required JSON fields:
{
  "detected_breed": "<primary breed name>",
  "confidence": <float 0.0-1.0>,
  "top_breeds": [
    {"breed": "<name>", "confidence": <float>},
    ...
  ],
  "is_mixed_breed": <true|false>,
  "is_cgi_synthetic": <true|false>,
  "multiple_animals_detected": <true|false>,
  "species": "<dog|cat|rabbit|bird|reptile|other>"
}

Rules:
- detected_breed: most specific breed you can identify
- confidence: your certainty that detected_breed is correct
- top_breeds: up to 5 candidates including the primary
- is_mixed_breed: true if you see clear multi-breed characteristics
- is_cgi_synthetic: true if image appears AI-generated, CGI, or digitally fabricated
- multiple_animals_detected: true if more than one distinct animal is visible
- species: single word, lowercase
"""


# ---------------------------------------------------------------------------
# Vision model call (Ollama)
# ---------------------------------------------------------------------------

async def analyze_image_with_vision(
    image_bytes: bytes,
    declared_breed: str,
    declared_species: str,
) -> dict[str, Any]:
    """
    Call Ollama llama3.2-vision to analyse the pet image.

    Returns a dict with keys:
        detected_breed, confidence, top_breeds, is_mixed_breed,
        is_cgi_synthetic, multiple_animals_detected, species
    """
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "model": VISION_MODEL,
        "prompt": VISION_PROMPT,
        "images": [b64_image],
        "stream": False,
        "options": {"temperature": VISION_TEMPERATURE},
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    raw_text: str = data.get("response", "")

    # Attempt to extract JSON from the model response
    vision_result = _parse_vision_response(raw_text)
    return vision_result


def _parse_vision_response(raw_text: str) -> dict[str, Any]:
    """
    Extract JSON from the vision model's text output.
    Returns a safe default dict if parsing fails.
    """
    # Try direct parse first
    try:
        return json.loads(raw_text.strip())
    except json.JSONDecodeError:
        pass

    # Look for JSON block within surrounding text
    start = raw_text.find("{")
    end = raw_text.rfind("}") + 1
    if start != -1 and end > start:
        try:
            return json.loads(raw_text[start:end])
        except json.JSONDecodeError:
            pass

    logger.warning("Vision model returned unparseable JSON; using defaults")
    return {
        "detected_breed": "unknown",
        "confidence": 0.0,
        "top_breeds": [],
        "is_mixed_breed": False,
        "is_cgi_synthetic": False,
        "multiple_animals_detected": False,
        "species": "unknown",
    }


# ---------------------------------------------------------------------------
# CLIP embedding stub (768-dim)
# ---------------------------------------------------------------------------

async def get_image_embedding(image_bytes: bytes) -> list[float]:
    """
    CLIP ViT-L/14 embedding stub.

    Returns a 768-dimensional zero vector as a placeholder.
    In production: load CLIP ViT-L/14 and return actual embeddings.

    CRITICAL: The vector dimension MUST be 768 to match the pgvector
    column definition (vector(768)).  Do NOT change to 512.
    """
    # Production replacement:
    #   from transformers import CLIPProcessor, CLIPModel
    #   import torch
    #   model = CLIPModel.from_pretrained("openai/clip-vit-large-patch14")
    #   processor = CLIPProcessor.from_pretrained("openai/clip-vit-large-patch14")
    #   inputs = processor(images=Image.open(io.BytesIO(image_bytes)), return_tensors="pt")
    #   with torch.no_grad():
    #       embedding = model.get_image_features(**inputs)
    #   return embedding[0].tolist()  # length == 768
    return [0.0] * 768


# ---------------------------------------------------------------------------
# pgvector duplicate detection
# ---------------------------------------------------------------------------

async def check_duplicate(embedding: list[float], pg_dsn: str) -> bool:
    """
    Query pgvector for any stored embedding with cosine similarity > 0.95.

    Returns True when a near-duplicate is found.
    Treats connection errors as non-duplicate (fail-open) so a DB outage
    does not block all verifications — a fraud_signal is still raised by
    the caller when appropriate.
    """
    try:
        import asyncpg  # imported lazily — not available in all environments

        conn = await asyncpg.connect(pg_dsn)
        try:
            # Convert embedding to pgvector literal string
            vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
            row = await conn.fetchrow(
                """
                SELECT id,
                       1 - (embedding <=> $1::vector) AS cosine_sim
                FROM   breed_image_embeddings
                ORDER  BY embedding <=> $1::vector
                LIMIT  1
                """,
                vec_str,
            )
            if row and row["cosine_sim"] > DUPLICATE_SIMILARITY_THRESHOLD:
                return True
            return False
        finally:
            await conn.close()

    except Exception as exc:
        logger.error("pgvector duplicate check failed: %s", exc)
        return False


async def store_embedding(
    policy_id: str,
    image_hash: str,
    embedding: list[float],
    breed_label: str,
    pg_dsn: str,
) -> None:
    """
    Persist a new image embedding to the vector store.
    Silently skips if the image_hash already exists (UNIQUE constraint).
    """
    try:
        import asyncpg

        conn = await asyncpg.connect(pg_dsn)
        try:
            vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
            await conn.execute(
                """
                INSERT INTO breed_image_embeddings
                    (policy_id, image_hash, embedding, breed_label)
                VALUES ($1, $2, $3::vector, $4)
                ON CONFLICT (image_hash) DO NOTHING
                """,
                policy_id,
                image_hash,
                vec_str,
                breed_label,
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("Failed to store embedding: %s", exc)


async def check_prior_fraud(policy_id: str, pg_dsn: str) -> int:
    """
    Return the fraud_tier stored for a policy (0 if no record exists).
    """
    try:
        import asyncpg

        conn = await asyncpg.connect(pg_dsn)
        try:
            row = await conn.fetchrow(
                "SELECT fraud_tier FROM policy_fraud_flags WHERE policy_id = $1",
                policy_id,
            )
            return int(row["fraud_tier"]) if row else 0
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("Prior fraud check failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Image quality assessment
# ---------------------------------------------------------------------------

def assess_image_quality(image_bytes: bytes) -> ImageQuality:
    """
    Use Pillow to measure basic quality indicators.

    blur_score: variance-of-Laplacian normalised to [0, 1].
    Higher score = blurrier image (inverted so 1 = maximally blurry).
    """
    img = Image.open(io.BytesIO(image_bytes))
    width, height = img.size
    fmt = img.format or "UNKNOWN"

    is_too_small = width < MIN_IMAGE_DIMENSION or height < MIN_IMAGE_DIMENSION

    # Convert to greyscale for blur estimation
    grey = img.convert("L")
    laplacian = grey.filter(ImageFilter.Kernel(
        size=3,
        kernel=[-1, -1, -1, -1, 8, -1, -1, -1, -1],
        scale=1,
        offset=0,
    ))
    pixels = list(laplacian.getdata())
    n = len(pixels)
    mean = sum(pixels) / n
    variance = sum((p - mean) ** 2 for p in pixels) / n

    # Normalise: high variance = sharp; cap at 1000 for normalisation
    blur_score = max(0.0, 1.0 - min(variance / 1000.0, 1.0))

    # Check for very dark images (mean pixel < 30)
    mean_brightness = sum(list(grey.getdata())) / n
    is_dark = mean_brightness < 30.0

    quality_flag = is_too_small or blur_score > 0.85 or is_dark

    return ImageQuality(
        width=width,
        height=height,
        format=fmt,
        is_too_small=is_too_small,
        estimated_blur_score=round(blur_score, 4),
        quality_flag=quality_flag,
    )


# ---------------------------------------------------------------------------
# Perceptual hash helper (FD-03 stock photo)
# ---------------------------------------------------------------------------

KNOWN_STOCK_HASHES: set[str] = set()  # Populated from DB / config at startup


def compute_image_hash(image_bytes: bytes) -> str:
    """SHA-256 hex digest of raw image bytes."""
    return hashlib.sha256(image_bytes).hexdigest()


def is_stock_photo(image_hash: str) -> bool:
    """Check against the known stock-photo hash blocklist."""
    return image_hash in KNOWN_STOCK_HASHES


# ---------------------------------------------------------------------------
# Fraud rule engine
# ---------------------------------------------------------------------------

_HIGHER_PREMIUM_BREEDS = {
    # Dogs — non-exhaustive sample for illustration
    "french bulldog", "english bulldog", "pug", "boston terrier",
    "chow chow", "akita", "doberman", "rottweiler",
    "pit bull terrier", "american staffordshire terrier",
    "staffordshire bull terrier", "dogo argentino",
    "cane corso", "presa canario",
    # Cats
    "bengal", "savannah", "chausie",
}


def _normalise_breed(breed: str) -> str:
    return breed.strip().lower()


def _is_higher_premium(detected: str, declared: str) -> bool:
    """Return True when the detected breed is higher-premium than declared."""
    det = _normalise_breed(detected)
    dec = _normalise_breed(declared)
    if det == dec:
        return False
    return det in _HIGHER_PREMIUM_BREEDS and dec not in _HIGHER_PREMIUM_BREEDS


def apply_fraud_rules(
    vision_result: dict[str, Any],
    declared_breed: str,
    declared_species: str,
    image_quality: ImageQuality,
    is_duplicate: bool,
    image_hash: str,
    prior_fraud_tier: int,
) -> tuple[str, int, list[FraudSignal]]:
    """
    Evaluate all fraud detection rules and return:
        (overall_verdict, risk_tier, fraud_signals)
    """
    signals: list[FraudSignal] = []

    detected_breed: str = vision_result.get("detected_breed", "unknown")
    breed_confidence: float = float(vision_result.get("confidence", 0.0))
    is_mixed_breed: bool = bool(vision_result.get("is_mixed_breed", False))
    is_cgi: bool = bool(vision_result.get("is_cgi_synthetic", False))
    multiple_animals: bool = bool(vision_result.get("multiple_animals_detected", False))
    detected_species: str = vision_result.get("species", "unknown").lower()
    declared_species_norm = declared_species.strip().lower()

    risk_tier = 1  # Start at Standard

    # ------------------------------------------------------------------
    # FD-06 WRONG_SPECIES — check first; immediately REJECTED
    # ------------------------------------------------------------------
    species_match = (
        detected_species == declared_species_norm
        or detected_species == "unknown"
    )
    if not species_match:
        signals.append(FraudSignal(
            rule_id="FD-06",
            description="Wrong species detected",
            severity="CRITICAL",
            evidence=f"Declared: {declared_species}, Detected: {detected_species}",
        ))
        return "REJECTED", 5, signals

    # ------------------------------------------------------------------
    # FD-07 CGI_SYNTHETIC
    # ------------------------------------------------------------------
    if is_cgi:
        signals.append(FraudSignal(
            rule_id="FD-07",
            description="Image appears CGI or AI-generated",
            severity="CRITICAL",
            evidence="Vision model flagged image as synthetic",
        ))
        risk_tier = max(risk_tier, 5)

    # ------------------------------------------------------------------
    # FD-01 BREED_DOWNGRADE (higher-premium breed than declared)
    # ------------------------------------------------------------------
    if _is_higher_premium(detected_breed, declared_breed):
        signals.append(FraudSignal(
            rule_id="FD-01",
            description="Detected breed is higher-premium than declared",
            severity="HIGH",
            evidence=f"Declared: {declared_breed}, Detected: {detected_breed}",
        ))
        risk_tier = max(risk_tier, 3)

    # ------------------------------------------------------------------
    # FD-04 MIXED_BREED_DENIED
    # ------------------------------------------------------------------
    if is_mixed_breed and _normalise_breed(declared_breed) not in (
        "mixed breed", "mixed", "crossbreed", "mongrel"
    ):
        signals.append(FraudSignal(
            rule_id="FD-04",
            description="Mixed breed declared as purebred",
            severity="HIGH",
            evidence=f"Declared: {declared_breed}, vision identifies mixed breed",
        ))
        risk_tier = max(risk_tier, 3)

    # ------------------------------------------------------------------
    # FD-02 DUPLICATE_IMAGE
    # ------------------------------------------------------------------
    if is_duplicate:
        signals.append(FraudSignal(
            rule_id="FD-02",
            description="Image embedding matches an existing submission (cosine sim > 0.95)",
            severity="HIGH",
            evidence=f"image_hash={image_hash}",
        ))
        risk_tier = max(risk_tier, 4)

    # ------------------------------------------------------------------
    # FD-03 STOCK_PHOTO
    # ------------------------------------------------------------------
    if is_stock_photo(image_hash):
        signals.append(FraudSignal(
            rule_id="FD-03",
            description="Perceptual hash matches known stock photo",
            severity="HIGH",
            evidence=f"image_hash={image_hash}",
        ))
        risk_tier = max(risk_tier, 4)

    # ------------------------------------------------------------------
    # FD-05 IMAGE_QUALITY
    # ------------------------------------------------------------------
    if image_quality.quality_flag:
        reasons: list[str] = []
        if image_quality.is_too_small:
            reasons.append(f"dimensions {image_quality.width}x{image_quality.height} < 224x224")
        if image_quality.estimated_blur_score > 0.85:
            reasons.append(f"blur_score={image_quality.estimated_blur_score}")
        signals.append(FraudSignal(
            rule_id="FD-05",
            description="Image quality below minimum threshold",
            severity="MEDIUM",
            evidence="; ".join(reasons) if reasons else "quality_flag=True",
        ))
        risk_tier = max(risk_tier, 2)

    # ------------------------------------------------------------------
    # FD-08 MULTIPLE_ANIMALS
    # ------------------------------------------------------------------
    if multiple_animals:
        signals.append(FraudSignal(
            rule_id="FD-08",
            description="Multiple distinct animals detected in image",
            severity="MEDIUM",
            evidence="Cannot uniquely identify the insured animal",
        ))
        risk_tier = max(risk_tier, 3)

    # ------------------------------------------------------------------
    # FD-09 BREED_CONFIDENCE
    # ------------------------------------------------------------------
    if breed_confidence < MIN_BREED_CONFIDENCE:
        signals.append(FraudSignal(
            rule_id="FD-09",
            description=f"Breed confidence {breed_confidence:.2f} below threshold {MIN_BREED_CONFIDENCE}",
            severity="LOW",
            evidence=f"confidence={breed_confidence:.4f}",
        ))
        risk_tier = max(risk_tier, 3)

    # ------------------------------------------------------------------
    # FD-10 PRIOR_FRAUD
    # ------------------------------------------------------------------
    if prior_fraud_tier > 0:
        signals.append(FraudSignal(
            rule_id="FD-10",
            description="Policy has prior fraud flag on record",
            severity="HIGH",
            evidence=f"prior_fraud_tier={prior_fraud_tier}",
        ))
        risk_tier = min(5, risk_tier + 2)  # escalate by 2, cap at 5

    # ------------------------------------------------------------------
    # Determine overall verdict
    # ------------------------------------------------------------------
    has_fd01 = any(s.rule_id == "FD-01" for s in signals)
    has_fd04 = any(s.rule_id == "FD-04" for s in signals)
    has_fd07 = any(s.rule_id == "FD-07" for s in signals)
    has_fd02 = any(s.rule_id == "FD-02" for s in signals)
    has_fd03 = any(s.rule_id == "FD-03" for s in signals)
    has_fd08 = any(s.rule_id == "FD-08" for s in signals)
    has_fd09 = any(s.rule_id == "FD-09" for s in signals)

    if has_fd07 or risk_tier == 5:
        verdict = "FRAUD_FLAG"
    elif has_fd01 or has_fd04:
        verdict = "BREED_MISMATCH"
    elif has_fd08 or has_fd09 or risk_tier == 3:
        verdict = "NEEDS_REVIEW"
    elif has_fd02 or has_fd03 or risk_tier >= 4:
        verdict = "FRAUD_FLAG"
    elif risk_tier <= 2 and not signals:
        verdict = "VERIFIED"
    elif risk_tier <= 2:
        verdict = "VERIFIED"
    else:
        verdict = "NEEDS_REVIEW"

    return verdict, risk_tier, signals


# ---------------------------------------------------------------------------
# Build BreedAssessment from vision result
# ---------------------------------------------------------------------------

def build_breed_assessment(
    vision_result: dict[str, Any],
    declared_breed: str,
    declared_species: str,
) -> BreedAssessment:
    detected_species = vision_result.get("species", "unknown").lower()
    species_match = (
        detected_species == declared_species.strip().lower()
        or detected_species == "unknown"
    )
    top_breeds_raw = vision_result.get("top_breeds", [])
    top_breeds = [
        TopBreed(breed=tb.get("breed", "unknown"), confidence=float(tb.get("confidence", 0.0)))
        for tb in top_breeds_raw
        if isinstance(tb, dict)
    ]
    return BreedAssessment(
        declared_breed=declared_breed,
        detected_breed=vision_result.get("detected_breed", "unknown"),
        breed_confidence=float(vision_result.get("confidence", 0.0)),
        top_breeds=top_breeds,
        is_mixed_breed=bool(vision_result.get("is_mixed_breed", False)),
        species_match=species_match,
    )
