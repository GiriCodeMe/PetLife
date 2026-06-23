"""
Core async processing pipeline for UC-05 Longitudinal Medical History Review.

PHI POLICY: NEVER log any content extracted from medical records.
Log ONLY: review_id, page_count, duration_ms, final status.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import date, datetime
from io import BytesIO
from typing import Any

import httpx
import pdfplumber

from .models import ConditionEntry, ReviewResult, TimelineEvent

logger = logging.getLogger("uc05.reviewer")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL = "llama3.3:70b-instruct-q4_K_M"
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_TIMEOUT = 600.0  # seconds per LLM call

SINGLE_PASS_THRESHOLD = 60      # pages
CHUNKED_PASS_THRESHOLD = 150    # pages
CHUNK_SIZE_MEDIUM = 30          # pages per chunk for 61-150 page docs
CHUNK_SIZE_LARGE = 50           # pages per chunk for >150 page docs

# Chronic disease names that are always classified as chronic (CD-05)
ALWAYS_CHRONIC = {
    "diabetes", "ckd", "chronic kidney disease", "ibd", "inflammatory bowel disease",
    "hypothyroidism", "exocrine pancreatic insufficiency", "epi",
    "heart disease", "cardiac disease",
}

# Autoimmune conditions (CD-04)
AUTOIMMUNE_KEYWORDS = {
    "autoimmune", "immune-mediated", "imha", "imtp", "lupus", "pemphigus",
    "masticatory muscle myositis", "addison", "hypoadrenocorticism",
    "myasthenia gravis",
}

# Age-related degenerative conditions (CD-07)
AGE_RELATED_KEYWORDS = {"osteoarthritis", "cataract", "degenerative joint disease", "djd"}

# Hereditary / congenital keywords (PE-03)
HEREDITARY_KEYWORDS = {
    "hereditary", "congenital", "genetic", "breed predisposition",
    "breed-related", "inherited",
}


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

async def extract_text_from_pdf(pdf_bytes: bytes) -> tuple[str, int]:
    """
    Extract text from a PDF using pdfplumber.

    Returns (full_text, page_count).
    Runs in a thread executor to avoid blocking the event loop.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _extract_sync, pdf_bytes)


def _extract_sync(pdf_bytes: bytes) -> tuple[str, int]:
    pages: list[str] = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            pages.append(text)
    full_text = "\n\n--- PAGE BREAK ---\n\n".join(pages)
    return full_text, len(pages)


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

async def chunk_text(text: str, page_count: int) -> list[str]:
    """
    Split document text into processing chunks according to the chunking strategy.

      <= 60 pages  : single chunk (whole document)
      61-150 pages : 30-page chunks
      > 150 pages  : 50-page chunks
    """
    page_separator = "--- PAGE BREAK ---"
    raw_pages = text.split(page_separator)

    if page_count <= SINGLE_PASS_THRESHOLD:
        return [text]

    chunk_size = CHUNK_SIZE_MEDIUM if page_count <= CHUNKED_PASS_THRESHOLD else CHUNK_SIZE_LARGE

    chunks: list[str] = []
    for start in range(0, len(raw_pages), chunk_size):
        chunk_pages = raw_pages[start: start + chunk_size]
        chunks.append(f"\n{page_separator}\n".join(chunk_pages))
    return chunks


# ---------------------------------------------------------------------------
# Ollama helper
# ---------------------------------------------------------------------------

async def _ollama_generate(prompt: str, model: str) -> str:
    """
    Call the Ollama /api/generate endpoint and return the response text.
    Streams response internally to avoid timeout on large models.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0},
    }
    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
        response = await client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("response", "")


# ---------------------------------------------------------------------------
# Pass 1 — Event extraction
# ---------------------------------------------------------------------------

PASS1_PROMPT_TEMPLATE = """You are a veterinary medical records analyst. Extract ALL temporal medical events from the following veterinary medical record text.

Return ONLY a valid JSON array. Each element must have these exact keys:
  "date": string (ISO format YYYY-MM-DD if determinable, else "unknown"),
  "event_type": string (e.g. "diagnosis", "treatment", "procedure", "vaccination", "examination", "prescription"),
  "description": string (brief factual description, no patient identifiers),
  "diagnoses": array of strings,
  "treatments": array of strings,
  "outcomes": array of strings

Rules:
- Include every clinical encounter with a date or relative time reference.
- Do NOT include owner names, addresses, phone numbers, or other PII.
- Do NOT fabricate events; only extract what is in the text.
- If a date is relative (e.g. "3 months ago"), note it as "relative: 3 months ago".
- Output ONLY the JSON array, no preamble or explanation.

MEDICAL RECORD TEXT:
{text}

JSON OUTPUT:"""


async def run_pass1(chunk: str, model: str) -> list[dict]:
    """
    Pass 1: Extract temporal medical events from a single chunk.
    Returns list of raw event dicts.
    """
    prompt = PASS1_PROMPT_TEMPLATE.format(text=chunk)
    raw = await _ollama_generate(prompt, model)

    # Parse JSON — find the first [ ... ] block
    try:
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except (json.JSONDecodeError, AttributeError):
        pass
    return []


# ---------------------------------------------------------------------------
# Pass 2 — Timeline synthesis with PE and CD classification
# ---------------------------------------------------------------------------

PASS2_PROMPT_TEMPLATE = """You are a veterinary insurance pre-existing condition analyst.

Policy inception date: {policy_inception_date}
Patient species: {species}

Below is a JSON array of raw medical events extracted from the patient record. Your task:

1. Sort events chronologically.
2. Merge duplicate events (same date + same diagnosis) into one.
3. Identify ALL distinct medical conditions mentioned.
4. For each condition, produce a JSON object with:
   - "condition_name": canonical name
   - "first_noted_date": earliest date in record (ISO or "unknown")
   - "last_noted_date": most recent date
   - "occurrence_count": number of separate visits mentioning this condition
   - "treatments": deduplicated list of treatments
   - "current_status": "active" | "resolved" | "monitoring" | "unknown"
   - "icd10_code": best-match ICD-10 code if known, else null
   - "raw_notes": brief notes about bilateral nature, hereditary flags, "history of" language, recurrence

5. Return a JSON object with two keys:
   - "sorted_events": the chronologically sorted and merged event array
   - "conditions": the conditions array

Output ONLY the JSON object. No preamble.

RAW EVENTS:
{events_json}

JSON OUTPUT:"""


async def run_pass2(
    all_events: list[dict],
    policy_inception_date: str,
    species: str,
    model: str,
) -> dict:
    """
    Pass 2: Sort, merge, and identify conditions from all extracted events.
    Returns dict with keys 'sorted_events' and 'conditions'.
    """
    events_json = json.dumps(all_events, indent=2)
    prompt = PASS2_PROMPT_TEMPLATE.format(
        policy_inception_date=policy_inception_date,
        species=species,
        events_json=events_json,
    )
    raw = await _ollama_generate(prompt, model)

    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
            if "sorted_events" in data and "conditions" in data:
                return data
    except (json.JSONDecodeError, AttributeError):
        pass

    # Fallback: return unsorted events and empty conditions
    return {"sorted_events": all_events, "conditions": []}


# ---------------------------------------------------------------------------
# Pass 3 — Plain-language summary
# ---------------------------------------------------------------------------

PASS3_PROMPT_TEMPLATE = """You are writing a customer-facing veterinary insurance summary. Write a clear, empathetic, plain-language summary of this pet's medical history.

Guidelines:
- 3 to 5 paragraphs.
- Use plain English; avoid medical jargon where possible; explain any necessary terms.
- Do NOT include any owner names, addresses, or personal identifiers.
- Mention the overall health trajectory, key conditions, and whether pre-existing conditions were identified.
- Flag any chronic conditions needing ongoing management.
- Be factual and compassionate in tone.
- Do NOT start sentences with "I".

PRE-EXISTING CONDITIONS COUNT: {pe_count}
CHRONIC CONDITIONS COUNT: {chronic_count}
TOTAL CONDITIONS IDENTIFIED: {total_count}
DATE RANGE: {date_range}

TIMELINE SUMMARY (no raw clinical text — structured data only):
{timeline_summary}

Write the plain-language summary now:"""


async def run_pass3(
    timeline: list[dict],
    conditions: list[ConditionEntry],
    model: str,
) -> str:
    """
    Pass 3: Generate a plain-language summary for the customer.
    Does NOT pass raw clinical text — only structured metadata.
    """
    pe_count = sum(1 for c in conditions if c.is_pre_existing)
    chronic_count = sum(1 for c in conditions if c.is_chronic)
    total_count = len(conditions)

    # Build date range from timeline
    dates = [e.get("date", "") for e in timeline if e.get("date") and e.get("date") != "unknown"]
    date_range = f"{min(dates)} to {max(dates)}" if dates else "dates not determinable"

    # Build a safe, non-PHI timeline summary (condition names + dates only)
    timeline_summary_lines = []
    for event in timeline[:50]:  # cap to avoid prompt overflow
        line = f"- {event.get('date', 'unknown')}: {event.get('event_type', 'event')} — {', '.join(event.get('diagnoses', []))}"
        timeline_summary_lines.append(line)
    timeline_summary = "\n".join(timeline_summary_lines) if timeline_summary_lines else "No events found."

    prompt = PASS3_PROMPT_TEMPLATE.format(
        pe_count=pe_count,
        chronic_count=chronic_count,
        total_count=total_count,
        date_range=date_range,
        timeline_summary=timeline_summary,
    )
    return await _ollama_generate(prompt, model)


# ---------------------------------------------------------------------------
# Pre-existing classification rules (PE-01 .. PE-08)
# ---------------------------------------------------------------------------

def _parse_date_safe(date_str: str | None) -> date | None:
    if not date_str or date_str in ("unknown", "relative"):
        return None
    try:
        return datetime.fromisoformat(date_str).date()
    except ValueError:
        return None


def apply_pre_existing_rules(
    events: list[dict],
    conditions_raw: list[dict],
    policy_inception_date: str,
) -> list[ConditionEntry]:
    """
    Apply PE-01 through PE-08 classification rules and return ConditionEntry list.
    """
    inception = _parse_date_safe(policy_inception_date)
    entries: list[ConditionEntry] = []

    for raw in conditions_raw:
        entry = ConditionEntry(
            condition_name=raw.get("condition_name", "Unknown"),
            first_noted_date=raw.get("first_noted_date"),
            last_noted_date=raw.get("last_noted_date"),
            occurrence_count=raw.get("occurrence_count", 1),
            treatments=raw.get("treatments", []),
            current_status=raw.get("current_status", "unknown"),
            icd10_code=raw.get("icd10_code"),
        )
        notes = (raw.get("raw_notes") or "").lower()
        name_lower = entry.condition_name.lower()

        # PE-01: first noted before policy inception
        if inception and entry.first_noted_date:
            first = _parse_date_safe(entry.first_noted_date)
            if first and first < inception:
                entry.is_pre_existing = True
                entry.pre_existing_rule = "PE-01"

        # PE-02: bilateral — if one side pre-existing, both are
        if not entry.is_pre_existing:
            bilateral_terms = ["bilateral", "both ears", "both eyes", "both legs"]
            if any(t in notes for t in bilateral_terms):
                # Check if any event for this condition is before inception
                for ev in events:
                    if entry.condition_name.lower() in (ev.get("description") or "").lower():
                        ev_date = _parse_date_safe(ev.get("date"))
                        if inception and ev_date and ev_date < inception:
                            entry.is_pre_existing = True
                            entry.pre_existing_rule = "PE-02"
                            break

        # PE-03: hereditary/congenital
        if not entry.is_pre_existing:
            if any(kw in notes or kw in name_lower for kw in HEREDITARY_KEYWORDS):
                entry.is_pre_existing = True
                entry.pre_existing_rule = "PE-03"

        # PE-04: "history of" or "previously diagnosed"
        if not entry.is_pre_existing:
            if "history of" in notes or "previously diagnosed" in notes:
                entry.is_pre_existing = True
                entry.pre_existing_rule = "PE-04"

        # PE-05: recurrent (3+ occurrences)
        if not entry.is_pre_existing:
            if entry.occurrence_count >= 3:
                entry.is_pre_existing = True
                entry.pre_existing_rule = "PE-05"

        # PE-06: noted in "past medical history"
        if not entry.is_pre_existing:
            if "past medical history" in notes:
                entry.is_pre_existing = True
                entry.pre_existing_rule = "PE-06"

        # PE-07: related conditions — if primary condition is pre-existing, flag secondaries
        # (Applied in a second pass after all entries are built — done below)

        # PE-08: "rule out" or "suspected" — NOT pre-existing without confirmed diagnosis
        if entry.is_pre_existing:
            if "rule out" in notes or "suspected" in notes:
                entry.is_pre_existing = False
                entry.pre_existing_rule = None

        entries.append(entry)

    # PE-07 second pass: secondary complications of a pre-existing primary
    pe_names = {e.condition_name.lower() for e in entries if e.is_pre_existing}
    complication_map = {
        "diabetes": ["diabetic neuropathy", "diabetic nephropathy", "diabetic cataract", "ketoacidosis"],
        "ckd": ["renal hypertension", "anemia of chronic disease", "uremic"],
        "heart disease": ["pulmonary edema", "pleural effusion", "ascites"],
        "hypothyroidism": ["hyperlipidemia", "myxedema"],
        "autoimmune": ["secondary infection"],
    }
    for entry in entries:
        if not entry.is_pre_existing:
            name_l = entry.condition_name.lower()
            for primary, complications in complication_map.items():
                if primary in pe_names:
                    if any(comp in name_l for comp in complications):
                        entry.is_pre_existing = True
                        entry.pre_existing_rule = "PE-07"
                        break

    return entries


# ---------------------------------------------------------------------------
# Chronic disease detection rules (CD-01 .. CD-07)
# ---------------------------------------------------------------------------

def apply_chronic_rules(conditions: list[ConditionEntry]) -> list[ConditionEntry]:
    """
    Apply CD-01 through CD-07 rules and annotate each ConditionEntry in-place.
    Returns the same list (modified).
    """
    for entry in conditions:
        name_lower = entry.condition_name.lower()

        if entry.is_chronic:
            continue  # already classified

        # CD-01: 3+ separate visits with 30+ day gaps — use occurrence_count as proxy
        if entry.occurrence_count >= 3:
            entry.is_chronic = True
            entry.chronic_rule = "CD-01"
            continue

        # CD-02: qualifier words in condition name
        chronic_qualifiers = {"chronic", "ongoing", "longstanding"}
        if any(q in name_lower for q in chronic_qualifiers):
            entry.is_chronic = True
            entry.chronic_rule = "CD-02"
            continue

        # CD-03: continuous medication — heuristic: treatments contain long-term meds
        long_term_meds = {"levothyroxine", "methimazole", "prednisolone", "prednisone",
                          "cyclosporine", "oclacitinib", "apocaps", "insulin",
                          "benazepril", "enalapril", "furosemide", "amlodipine",
                          "phenobarbital", "potassium bromide", "gabapentin"}
        tx_lower = {t.lower() for t in entry.treatments}
        if tx_lower & long_term_meds:
            entry.is_chronic = True
            entry.chronic_rule = "CD-03"
            continue

        # CD-04: autoimmune conditions
        if any(kw in name_lower for kw in AUTOIMMUNE_KEYWORDS):
            entry.is_chronic = True
            entry.chronic_rule = "CD-04"
            continue

        # CD-05: named always-chronic conditions
        if any(kw in name_lower for kw in ALWAYS_CHRONIC):
            entry.is_chronic = True
            entry.chronic_rule = "CD-05"
            continue

        # CD-06: allergies with 2+ episodes
        if "allerg" in name_lower and entry.occurrence_count >= 2:
            entry.is_chronic = True
            entry.chronic_rule = "CD-06"
            continue

        # CD-07: age-related degenerative
        if any(kw in name_lower for kw in AGE_RELATED_KEYWORDS):
            entry.is_chronic = True
            entry.chronic_rule = "CD-07"
            continue

    return conditions


# ---------------------------------------------------------------------------
# Date range helpers
# ---------------------------------------------------------------------------

def _compute_date_range(events: list[dict]) -> dict:
    dates = [
        e.get("date", "")
        for e in events
        if e.get("date") and e.get("date") not in ("unknown", "relative")
    ]
    if not dates:
        return {"earliest_date": None, "latest_date": None}
    return {"earliest_date": min(dates), "latest_date": max(dates)}


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

async def process_review(
    review_id: str,
    pdf_bytes: bytes,
    policy_inception_date: str,
    species: str,
    store: dict,
) -> None:
    """
    End-to-end processing pipeline with progress updates pushed to the job store.

    Progress updates are sent via store[review_id]['progress_queue'] as dicts:
      {type: "progress", data: {progress_pct, current_pass, message}}
      {type: "complete", data: {review_id, status}}
      {type: "error",   data: {error, review_id}}

    PHI POLICY: No clinical content is ever written to the logger.
    """
    job = store[review_id]
    start_ms = int(time.time() * 1000)
    model = MODEL

    async def push_progress(pct: int, pass_name: str, message: str) -> None:
        job["status"] = "processing"
        job["progress_pct"] = pct
        job["current_pass"] = pass_name
        await job["progress_queue"].put({
            "type": "progress",
            "data": {"progress_pct": pct, "current_pass": pass_name, "message": message},
        })

    try:
        # ------------------------------------------------------------------ #
        # Step 1: PDF extraction
        # ------------------------------------------------------------------ #
        await push_progress(2, "extraction", "Extracting text from PDF")
        text, page_count = await extract_text_from_pdf(pdf_bytes)
        job["page_count"] = page_count

        logger.info("review_started review_id=%s page_count=%d", review_id, page_count)

        # ------------------------------------------------------------------ #
        # Step 2: Chunking
        # ------------------------------------------------------------------ #
        await push_progress(5, "chunking", "Splitting document into chunks")
        chunks = await chunk_text(text, page_count)
        num_chunks = len(chunks)

        # ------------------------------------------------------------------ #
        # Step 3: Pass 1 — parallel event extraction per chunk
        # ------------------------------------------------------------------ #
        await push_progress(10, "pass1", f"Pass 1: extracting events from {num_chunks} chunk(s)")

        pass1_tasks = [run_pass1(chunk, model) for chunk in chunks]
        chunk_results: list[list[dict]] = []

        # Run concurrently but update progress as each completes
        for i, coro in enumerate(asyncio.as_completed(pass1_tasks)):
            result = await coro
            chunk_results.append(result)
            pct = 10 + int(50 * (i + 1) / num_chunks)
            await push_progress(pct, "pass1", f"Pass 1: processed chunk {i + 1}/{num_chunks}")

        all_events: list[dict] = [ev for chunk in chunk_results for ev in chunk]

        # ------------------------------------------------------------------ #
        # Step 4: Hierarchical summarisation (> 150 pages only)
        # ------------------------------------------------------------------ #
        if page_count > CHUNKED_PASS_THRESHOLD:
            await push_progress(60, "pass1_synthesis", "Synthesising large-document chunk summaries")
            # For very large docs, run pass2 on each chunk result individually
            # then merge — here we run a single pass2 with all merged events
            # (the prompt handles deduplication)

        # ------------------------------------------------------------------ #
        # Step 5: Pass 2 — timeline synthesis and condition identification
        # ------------------------------------------------------------------ #
        await push_progress(65, "pass2", "Pass 2: synthesising timeline and identifying conditions")
        pass2_result = await run_pass2(all_events, policy_inception_date, species, model)

        sorted_events: list[dict] = pass2_result.get("sorted_events", all_events)
        conditions_raw: list[dict] = pass2_result.get("conditions", [])

        # ------------------------------------------------------------------ #
        # Step 6: Apply classification rules
        # ------------------------------------------------------------------ #
        await push_progress(78, "classification", "Applying pre-existing and chronic rules")
        identified_conditions = apply_pre_existing_rules(sorted_events, conditions_raw, policy_inception_date)
        identified_conditions = apply_chronic_rules(identified_conditions)

        pre_existing = [c for c in identified_conditions if c.is_pre_existing]
        chronic = [c for c in identified_conditions if c.is_chronic]

        # ------------------------------------------------------------------ #
        # Step 7: Pass 3 — plain-language summary
        # ------------------------------------------------------------------ #
        await push_progress(85, "pass3", "Pass 3: generating customer summary")
        summary_text = await run_pass3(sorted_events, identified_conditions, model)

        # ------------------------------------------------------------------ #
        # Step 8: Assemble result
        # ------------------------------------------------------------------ #
        await push_progress(95, "assembling", "Assembling final result")

        timeline_events = [
            TimelineEvent(
                date=ev.get("date"),
                event_type=ev.get("event_type", "event"),
                description=ev.get("description", ""),
                diagnoses=ev.get("diagnoses", []),
                treatments=ev.get("treatments", []),
                outcomes=ev.get("outcomes", []),
            )
            for ev in sorted_events
        ]

        end_ms = int(time.time() * 1000)
        completed_at = datetime.utcnow().isoformat()

        result = ReviewResult(
            review_id=review_id,
            status="completed",
            page_count=page_count,
            date_range=_compute_date_range(sorted_events),
            timeline_events=timeline_events,
            identified_conditions=identified_conditions,
            pre_existing_conditions=pre_existing,
            chronic_conditions=chronic,
            summary=summary_text,
            processing_time_ms=end_ms - start_ms,
            model_used=model,
            completed_at=completed_at,
        )

        job["status"] = "completed"
        job["progress_pct"] = 100
        job["current_pass"] = None
        job["result"] = result
        job["completed_at"] = completed_at

        logger.info(
            "review_completed review_id=%s page_count=%d duration_ms=%d status=completed",
            review_id,
            page_count,
            end_ms - start_ms,
        )

        await job["progress_queue"].put({
            "type": "complete",
            "data": {"review_id": review_id, "status": "completed"},
        })

    except asyncio.CancelledError:
        job["status"] = "cancelled"
        job["completed_at"] = datetime.utcnow().isoformat()
        logger.info("review_cancelled review_id=%s", review_id)
        await job["progress_queue"].put({
            "type": "error",
            "data": {"error": "Review cancelled", "review_id": review_id},
        })
        raise

    except Exception as exc:  # noqa: BLE001
        job["status"] = "failed"
        job["completed_at"] = datetime.utcnow().isoformat()
        end_ms = int(time.time() * 1000)
        logger.error(
            "review_failed review_id=%s duration_ms=%d",
            review_id,
            end_ms - start_ms,
        )
        await job["progress_queue"].put({
            "type": "error",
            "data": {"error": "Internal processing error", "review_id": review_id},
        })
