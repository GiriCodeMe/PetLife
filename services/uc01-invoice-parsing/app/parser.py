from __future__ import annotations

import io
import json
import logging
import os
from typing import Any

import httpx
import pdfplumber

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b-instruct-q4_K_M")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60"))

SYSTEM_PROMPT = """You are a veterinary invoice data extraction specialist.
Your task is to extract structured data from veterinary invoice text and return it as valid JSON.

CRITICAL RULES:
1. Return ONLY valid JSON. No markdown, no code fences, no preamble, no explanation.
2. Do not include any text before or after the JSON object.
3. If a field is not found in the text, use null for optional fields.
4. All monetary amounts must be numbers (float), not strings.
5. The invoice_date and visit_date must be in ISO format: YYYY-MM-DD.
6. patient_species must be one of: canine, feline, avian, other.
7. currency must be one of: USD, GBP, EUR, CAD, AUD.
8. extraction_confidence is a float between 0.0 and 1.0 reflecting how confident you are.
9. procedure_code, if present, must only contain uppercase letters, digits, and hyphens (3-15 chars).

Return a JSON object with exactly these fields:
{
  "clinic_name": "string",
  "clinic_address": "string or null",
  "clinic_phone": "string or null",
  "invoice_number": "string",
  "invoice_date": "YYYY-MM-DD",
  "visit_date": "YYYY-MM-DD or null",
  "patient_name": "string",
  "patient_species": "canine|feline|avian|other",
  "patient_breed": "string or null",
  "owner_name": "string",
  "line_items": [
    {
      "description": "string",
      "procedure_code": "string or null",
      "quantity": 1.0,
      "unit_price": 0.0,
      "amount": 0.0
    }
  ],
  "subtotal": 0.0,
  "tax_rate": 0.0,
  "tax_amount": 0.0,
  "discount_amount": 0.0,
  "total_due": 0.0,
  "amount_paid": 0.0,
  "balance_due": 0.0,
  "currency": "USD",
  "extraction_confidence": 0.95
}"""

USER_PROMPT_TEMPLATE = """Extract all invoice data from the following veterinary invoice text.
Return ONLY valid JSON with no other text.

INVOICE TEXT:
{text}"""


async def extract_text(pdf_bytes: bytes) -> str:
    """Extract all text from a PDF using pdfplumber.

    PHI NOTE: The extracted text is NEVER logged.
    """
    loop_text_parts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                loop_text_parts.append(page_text)
    return "\n".join(loop_text_parts)


async def parse_invoice(text: str, request_id: str) -> dict[str, Any]:
    """Call Ollama to extract structured invoice data from plain text.

    PHI NOTE: The raw text and extracted data are NEVER logged. Only request_id
    and success/fail status are logged.
    """
    payload = {
        "model": OLLAMA_MODEL,
        "system": SYSTEM_PROMPT,
        "prompt": USER_PROMPT_TEMPLATE.format(text=text),
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.0,
            "num_predict": 2048,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json=payload,
            )
            response.raise_for_status()
    except httpx.ConnectError as exc:
        logger.error(
            "request_id=%s ollama_connect_failed url=%s",
            request_id,
            OLLAMA_BASE_URL,
        )
        raise OllamaUnavailableError(
            f"Cannot connect to Ollama at {OLLAMA_BASE_URL}"
        ) from exc
    except httpx.HTTPStatusError as exc:
        logger.error(
            "request_id=%s ollama_http_error status=%s",
            request_id,
            exc.response.status_code,
        )
        raise OllamaUnavailableError(
            f"Ollama returned HTTP {exc.response.status_code}"
        ) from exc
    except httpx.TimeoutException as exc:
        logger.error(
            "request_id=%s ollama_timeout timeout_s=%s",
            request_id,
            OLLAMA_TIMEOUT,
        )
        raise OllamaUnavailableError(
            f"Ollama request timed out after {OLLAMA_TIMEOUT}s"
        ) from exc

    body = response.json()
    raw_text: str = body.get("response", "")

    # Strip any accidental markdown fences the model might add despite instructions
    raw_text = _strip_markdown_fences(raw_text).strip()

    try:
        parsed: dict[str, Any] = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.error(
            "request_id=%s ollama_json_parse_failed",
            request_id,
        )
        raise ExtractionParseError(
            "Ollama response was not valid JSON"
        ) from exc

    # PHI: never log parsed content
    logger.info("request_id=%s ollama_extraction_ok", request_id)
    return parsed


def _strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` wrappers if the model added them despite instructions."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # Remove first line (``` or ```json) and last line (```)
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        elif lines[0].strip().startswith("```"):
            lines = lines[1:]
        stripped = "\n".join(lines).strip()
    return stripped


async def check_ollama_reachable() -> bool:
    """Ping Ollama health endpoint. Returns True if reachable."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            return r.status_code == 200
    except Exception:
        return False


class OllamaUnavailableError(Exception):
    """Raised when Ollama is not reachable or returns an error."""


class ExtractionParseError(Exception):
    """Raised when the Ollama response cannot be parsed as JSON."""
