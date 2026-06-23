# Use Case: Automated Medical Coding

_Generated: 2026-06-19_

---

## 1. Overview

Translate unstructured veterinary clinical notes (SOAP notes, discharge summaries, surgical reports, specialist consultations) into structured clinical codes using SNOMED-CT and ICD-10 terminology. The coded output enriches claim records with standardised diagnostic and procedure identifiers, enabling precise policy matching, claims adjudication, and longitudinal health analytics for pet insurance.

This UC sits **between** UC-01 (Receipt & Invoice Parsing) and UC-02 (Basic Claims Adjudication) in the LifeGroup processing pipeline. Coded diagnoses produced here feed directly into the exclusion and benefit-limit lookups performed during adjudication.

---

## 2. Business Context

| Attribute | Detail |
|---|---|
| Domain | Pet (dog & cat) health insurance / LifeGroup clinical data enrichment |
| Stakeholder Role | Business Analyst |
| Trigger | Vet clinical note attached to a claim submission, or uploaded independently for pre-authorisation |
| Outcome | Structured list of SNOMED-CT diagnosis codes + ICD-10 procedure codes attached to the claim record |
| Downstream Consumers | Claims adjudication engine (UC-02), member health history, underwriting risk scoring |
| Volume Expectation | Medium — one or more notes per claim; some members submit multiple notes per visit |
| Data Sensitivity | PHI-equivalent (clinical records, diagnoses, treatment details); treat with highest data protection |

---

## 3. AI Task Definition

**Task type:** Named-entity recognition + clinical code mapping (structured output generation)

**Inputs:**
1. Raw veterinary clinical note text (extracted from PDF or submitted as plain text)
2. Optional context: patient species, breed, age — to disambiguate species-specific conditions

**AI role:** Identify clinical concepts (diagnoses, symptoms, procedures, medications) in the note text and map each to the closest matching SNOMED-CT concept and ICD-10 code. Flag low-confidence mappings for human coder review.

**Output:** Structured JSON containing a list of coded findings, each with code system, code value, display name, confidence score, and source text span.

**Processing mode:** Local inference — no PHI leaves the on-premises environment.

---

## 4. Recommended Models

| Model | Quant | VRAM / RAM | Strength |
|---|---|---|---|
| Qwen2.5 14B Instruct | Q4_K_M | ~10 GB RAM | Strong multilingual medical NER; good SNOMED recall |
| Qwen2.5 14B Instruct | Q8_0 | ~16 GB RAM | Higher code mapping precision; preferred when hardware allows |
| Qwen2.5 32B Instruct | Q4_K_M | ~22 GB RAM | Best-in-class for complex multi-diagnosis notes |
| Qwen2.5 32B Instruct | Q8_0 | ~38 GB RAM | Maximum fidelity; use on GPU-equipped inference nodes |

**Recommendation:** Start with `Qwen2.5 14B Q4_K_M` for CPU/light-GPU deployments. Upgrade to `32B Q4_K_M` for production where multi-condition specialist notes are common.

**Runtime:** Ollama local or llama.cpp server | **Temperature:** 0.1 (slight variation acceptable; coding has some valid ambiguity unlike financial data)

---

## 5. Coding Systems

### 5a. SNOMED-CT (Systematized Nomenclature of Medicine — Clinical Terms)

| Attribute | Detail |
|---|---|
| Purpose | Diagnoses, clinical findings, morphology, body site, organisms |
| Veterinary extension | VetSCT (SNOMED Veterinary Extension) — covers species-specific concepts |
| Code format | Numeric SCTID (e.g. `302866003` = Hypoglycaemia) |
| Hierarchy used | Clinical finding, Procedure, Observable entity, Organism |
| Scope for UC | Diagnosis codes only (not anatomy or organism codes, unless clinically relevant) |

### 5b. ICD-10-CM (adapted for veterinary use)

| Attribute | Detail |
|---|---|
| Purpose | Procedure classification + secondary diagnosis coding for insurance billing |
| Veterinary mapping | ICD-10-CM adapted — human codes reused where applicable; "Z" and "V" codes used for wellness/preventive |
| Code format | Alphanumeric (e.g. `K29.0` = Acute haemorrhagic gastritis, `Z00.00` = General adult exam) |
| Scope for UC | Primary and secondary diagnosis codes; procedure codes from ICD-10-PCS where relevant |

### 5c. Code Confidence Tiers

| Tier | Confidence Range | Meaning | Action |
|---|---|---|---|
| HIGH | ≥ 0.85 | Direct match — explicit term in note | Auto-accept |
| MEDIUM | 0.60 – 0.84 | Probable match — inferred from context | Flag for coder spot-check |
| LOW | < 0.60 | Uncertain — ambiguous or incomplete description | Route to human coder |

---

## 6. Input Schema

### 6a. Coding Request

| Field | Type | Required | Description |
|---|---|---|---|
| `coding_request_id` | string (UUID) | Yes | Unique request identifier |
| `claim_id` | string | No | Associated claim (if available) |
| `member_id` | string | Yes | Policyholder member identifier |
| `note_type` | string (enum) | Yes | `SOAP` / `DISCHARGE_SUMMARY` / `SURGICAL_REPORT` / `CONSULTATION` / `FOLLOW_UP` / `RADIOLOGY` |
| `note_text` | string | Yes | Full unstructured clinical note text |
| `note_date` | date (ISO 8601) | Yes | Date of clinical encounter |
| `patient_species` | string | Yes | `Canine` / `Feline` |
| `patient_breed` | string | No | Breed for disambiguation of species-specific conditions |
| `patient_age_years` | number | No | Age in years (assists age-related condition disambiguation) |
| `attending_vet` | string | No | Attending veterinarian name |
| `clinic_name` | string | No | Practice name for audit trail |
| `target_code_systems` | array | Yes | e.g. `["SNOMED-CT", "ICD-10-CM"]` |

### 6b. Note Type Characteristics

| Note Type | Typical Content | Avg. Length |
|---|---|---|
| `SOAP` | Subjective findings, objective exam, assessment, plan | 200–600 words |
| `DISCHARGE_SUMMARY` | Admission reason, treatment performed, discharge instructions | 400–900 words |
| `SURGICAL_REPORT` | Pre-op diagnosis, procedure narrative, post-op findings | 300–700 words |
| `CONSULTATION` | Referral reason, specialist findings, differential diagnoses | 500–1,000 words |
| `FOLLOW_UP` | Progress against previous diagnosis, ongoing conditions | 150–400 words |
| `RADIOLOGY` | Imaging modality, findings, impression | 100–300 words |

---

## 7. Output Schema

### 7a. Coding Response

| Field | Type | Description |
|---|---|---|
| `coding_request_id` | string | Echo of input ID |
| `claim_id` | string \| null | Associated claim ID if provided |
| `coded_date` | date (ISO 8601) | Date coding was performed |
| `overall_confidence` | string | `HIGH` / `MEDIUM` / `LOW` — worst-case tier across all codes |
| `requires_review` | boolean | True if any code is LOW confidence |
| `coded_findings[]` | array | List of coded clinical concepts (see 7b) |
| `unresolved_terms[]` | array | Text spans the model could not map to any code |
| `adjudicator` | string | `AI` / `HUMAN` |
| `coder_notes` | string \| null | Free-text summary of key coding decisions |

### 7b. Coded Finding Object

| Field | Type | Description |
|---|---|---|
| `finding_id` | string | Sequential ID within this response (F-01, F-02, …) |
| `category` | string | `DIAGNOSIS` / `PROCEDURE` / `MEDICATION` / `SYMPTOM` / `PREVENTIVE` |
| `source_text` | string | Verbatim phrase from the note that triggered this code |
| `snomed_code` | string \| null | SNOMED-CT SCTID |
| `snomed_display` | string \| null | SNOMED-CT preferred term |
| `icd10_code` | string \| null | ICD-10-CM code |
| `icd10_display` | string \| null | ICD-10-CM description |
| `confidence_score` | number | 0.00–1.00 |
| `confidence_tier` | string | `HIGH` / `MEDIUM` / `LOW` |
| `laterality` | string \| null | `LEFT` / `RIGHT` / `BILATERAL` where anatomically relevant |
| `body_site` | string \| null | Anatomical site if specified in note |
| `is_primary_diagnosis` | boolean | True for the main reason for visit |
| `is_pre_existing` | boolean | True if note indicates chronic / prior condition |

---

## 8. Coding Logic & Disambiguation Rules

| Rule | Logic |
|---|---|
| CR-01 | Extract all clinical concepts before mapping — do not code inline during reading |
| CR-02 | Assign `is_primary_diagnosis: true` to the condition most prominently described in the Assessment section |
| CR-03 | If a concept appears in both Subjective (symptom) and Assessment (diagnosis), code once as DIAGNOSIS only |
| CR-04 | Species context must be applied — `Diabetes mellitus` in a feline context maps to `E11.9` (type 2); in a canine context maps to `E10.9` (type 1 analogue) |
| CR-05 | Medications in the Plan section are coded as `MEDICATION` only if a diagnosis cannot be inferred — avoid double-coding drug + condition |
| CR-06 | Preventive procedures (vaccines, wellness exams) always map to `PREVENTIVE` category regardless of section |
| CR-07 | If laterality is mentioned (left ear, right forelimb), capture in `laterality` field |
| CR-08 | Terms that are negated ("no signs of infection", "ruled out parvovirus") must NOT be coded as diagnoses |
| CR-09 | Differential diagnoses listed in the Assessment are coded as MEDIUM confidence until confirmed |
| CR-10 | If confidence < 0.60 on any finding, set `requires_review: true` on the entire response |

---

## 9. Prompt Strategy

**Approach:** Two-pass extraction — concept identification then code mapping

**Pass 1 — Concept Extraction:**
```
You are a veterinary medical coder. Read the following clinical note and extract all
clinical concepts: diagnoses, symptoms, procedures, medications, and preventive care.
For each concept, record: the verbatim source phrase, the section it appeared in
(Subjective/Objective/Assessment/Plan/Other), and whether it was negated.
Return ONLY a JSON array of concept objects.

Note type: {note_type}
Patient species: {patient_species} | Breed: {patient_breed} | Age: {patient_age_years} years

Clinical note:
{note_text}
```

**Pass 2 — Code Mapping:**
```
You are a certified veterinary medical coder. Map each extracted concept to its best
matching SNOMED-CT and ICD-10-CM code. Apply species-specific coding rules where relevant.
Assign a confidence score (0.00–1.00) and flag negated or differential concepts.
Return ONLY valid JSON matching this schema: {output_schema}.
Do not invent codes. If no code maps with confidence > 0.40, leave the code field null
and set confidence_tier to LOW.

Concepts to code:
{concepts_json}

Species context rules:
{species_rules}
```

**Temperature:** 0.1 | **Grammar enforcement:** GBNF grammar or Pydantic model for output JSON

---

## 10. Pipeline Architecture

```
Clinical Note Input (PDF or plain text)
        │
        ├── PDF path: pdfplumber → text extraction → cleaning
        └── Text path: whitespace normalisation
                │
                ▼
        Patient Context Enrichment ──── species, breed, age from claim record
                │
                ▼
        Pass 1: Concept Extraction ───── Qwen2.5 (local Ollama)
                │                         Extract: diagnoses, symptoms,
                │                         procedures, medications, negations
                ▼
        Pass 2: Code Mapping ──────────── Qwen2.5 (local Ollama)
                │                         SNOMED-CT + ICD-10-CM assignment
                │                         Confidence scoring per finding
                ▼
        Output Validation ─────────────── Pydantic schema + code format regex
                │                         (SCTID: numeric only | ICD-10: [A-Z][0-9]+)
                ▼
        Confidence Gate ───────────────── Any LOW confidence finding?
                │                              YES → requires_review = true
                │                              NO  → auto-accept all codes
                ▼
        Claim Record Enrichment ─────── Attach coded_findings[] to claim
                │                        Update claim.has_pre_existing flag
                │                        if any is_pre_existing == true
                ▼
        Structured Output ─────────────── JSON → adjudication engine / coder UI
```

---

## 11. Non-Functional Requirements

### 11a. Performance

| NFR | Target | Notes |
|---|---|---|
| Two-pass inference latency (p95) | ≤ 30 s per note | 14B model, CPU inference, avg SOAP note (~400 words) |
| Two-pass inference latency (p99) | ≤ 60 s | For long consultation notes (≥ 800 words) |
| API end-to-end response (p95) | ≤ 35 s | Includes PDF extraction + both passes + validation |
| Throughput | ≥ 3 concurrent coding requests | Model is heavier than UC-01/02; constrained by 14B weight |

### 11b. Scalability

| NFR | Target | Notes |
|---|---|---|
| Horizontal scaling | Stateless API — scale by adding instances | Each instance carries own model replica |
| Note length handling | Notes up to 2,000 words supported without chunking | Chunking strategy required for longer surgical/specialist reports (roadmap) |
| Queue depth | ≤ 30 queued requests before 503 returned | Smaller than UC-01/02 due to higher per-request cost |
| Model cold start | ≤ 10 s | 14B model load time on first request |

### 11c. Accuracy & Quality

| NFR | Target | Notes |
|---|---|---|
| Code recall (HIGH confidence) | ≥ 90% of clinically significant findings coded | Validated against 300-note golden dataset |
| Code precision | ≥ 92% of auto-accepted codes correct | Human coder validation on random 5% sample monthly |
| NEEDS_REVIEW rate | < 15% of notes | Higher acceptable rate than UC-02 due to inherent note ambiguity |
| Negation accuracy | ≥ 98% of negated terms correctly excluded | Critical — false-positive diagnoses affect policy decisions |
| Pre-existing flag accuracy | ≥ 95% | Drives underwriting and exclusion lookups |

### 11d. Availability & Reliability

| NFR | Target | Notes |
|---|---|---|
| Uptime SLA | 99.5% monthly | |
| Graceful degradation | On model failure, return `requires_review: true` with empty codes — never return partial/hallucinated codes | |
| Idempotency | Same `coding_request_id` within 24 hours returns cached response | |
| PHI deletion | Note text purged from inference memory immediately after response; not persisted in application logs | |

### 11e. Security

| NFR | Requirement |
|---|---|
| Authentication | Bearer token (`Authorization` header); scope: `coding:write` |
| Transport | HTTPS only — TLS 1.2 minimum |
| PHI handling | Note text treated as PHI — never logged, never stored beyond transaction lifetime |
| Tenant isolation | Coded findings strictly scoped to submitting tenant |
| Audit log | `coding_request_id`, `tenant_id`, `note_type`, `overall_confidence`, `requires_review`, `timestamp` only |
| Code library | SNOMED-CT and ICD-10 code reference tables stored locally — no outbound lookup calls |

### 11f. Observability

| NFR | Requirement |
|---|---|
| Health endpoint | `GET /health` — model load status + code library version |
| Metrics | Prometheus: request count, latency, `requires_review` rate, per-`note_type` confidence distribution |
| Tracing | `coding_request_id` propagated through both inference passes |
| Alerting | Alert if `requires_review` rate > 25% or p95 latency > 50 s over 10-minute window |

### 11g. Maintainability

| NFR | Requirement |
|---|---|
| Code library updates | SNOMED-CT and ICD-10 reference tables updated independently of model; hot-reload without restart |
| Model swap | Model path and parameters in config only — no code change |
| Species rule config | Species-specific coding rules (CR-04 etc.) in a YAML config file — editable without deployment |
| Backward compatibility | Response schema: additive changes only without version bump |

---

## 12. API Specification

### 12a. Endpoint

```
POST /api/v1/coding/notes
```

**Content-Type:** `application/json` (plain text note) OR `multipart/form-data` (PDF attachment)
**Authentication:** `Authorization: Bearer <api_key>` (scope: `coding:write`)

### 12b. Request Body (JSON mode)

```json
{
  "coding_request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "claim_id": "CLM-20240320-0081",
  "member_id": "MEM-00412",
  "note_type": "SOAP",
  "note_date": "2024-03-18",
  "patient_species": "Canine",
  "patient_breed": "Golden Retriever",
  "patient_age_years": 5,
  "attending_vet": "Dr. Sarah Okonkwo",
  "clinic_name": "Greenfield Animal Hospital",
  "target_code_systems": ["SNOMED-CT", "ICD-10-CM"],
  "note_text": "S: Owner reports Biscuit has been lethargic for 2 days..."
}
```

### 12c. Response — Success (HTTP 200)

```json
{
  "coding_request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "claim_id": "CLM-20240320-0081",
  "coded_date": "2024-03-20",
  "overall_confidence": "HIGH",
  "requires_review": false,
  "adjudicator": "AI",
  "coder_notes": "Annual wellness visit. All findings mapped with high confidence. No pre-existing conditions noted.",
  "coded_findings": [
    {
      "finding_id": "F-01",
      "category": "PREVENTIVE",
      "source_text": "annual wellness examination",
      "snomed_code": "410620009",
      "snomed_display": "Well examination (procedure)",
      "icd10_code": "Z00.00",
      "icd10_display": "Encounter for general adult medical examination without abnormal findings",
      "confidence_score": 0.97,
      "confidence_tier": "HIGH",
      "laterality": null,
      "body_site": null,
      "is_primary_diagnosis": true,
      "is_pre_existing": false
    }
  ],
  "unresolved_terms": []
}
```

### 12d. Response — Error Codes

| HTTP Status | Code | Meaning |
|---|---|---|
| 400 | `INVALID_NOTE_TYPE` | `note_type` value not in allowed enum |
| 400 | `NOTE_TOO_SHORT` | Note text under 20 words — insufficient for coding |
| 400 | `NOTE_TOO_LONG` | Note text exceeds 2,000 words (chunking not yet supported) |
| 400 | `UNSUPPORTED_CODE_SYSTEM` | Requested code system not in `["SNOMED-CT", "ICD-10-CM"]` |
| 400 | `MISSING_SPECIES` | `patient_species` not provided |
| 401 | `UNAUTHORIZED` | Missing or invalid API key |
| 403 | `INSUFFICIENT_SCOPE` | API key lacks `coding:write` scope |
| 409 | `DUPLICATE_REQUEST` | `coding_request_id` already processed; returns cached response |
| 422 | `CODING_FAILED` | Model failed to produce valid output after retries |
| 429 | `RATE_LIMIT_EXCEEDED` | Tenant quota exceeded |
| 503 | `SERVICE_OVERLOADED` | Queue full; retry after indicated seconds |
| 500 | `INTERNAL_ERROR` | Unexpected error; include `coding_request_id` when contacting support |

### 12e. Rate Limiting

| Tier | Requests / minute | Requests / day | Burst |
|---|---|---|---|
| Starter | 3 | 100 | 5 |
| Growth | 15 | 1,000 | 30 |
| Enterprise | 60 | 10,000 | 120 |

Rate limit headers:
```
X-RateLimit-Limit: 15
X-RateLimit-Remaining: 11
X-RateLimit-Reset: 1710768180
```

### 12f. Versioning & Deprecation

| Policy | Detail |
|---|---|
| URL versioning | `/api/v1/` |
| Additive changes | New optional response fields without version bump |
| SNOMED / ICD-10 edition upgrades | Communicated via `X-CodeLibrary-Version` response header; no breaking change |
| Breaking changes | New major version (`/api/v2/`); v1 supported minimum 12 months post-v2 GA |

### 12g. SaaS Integration Notes

- Stateless — no session affinity required
- **Idempotency** — same `coding_request_id` within 24 hours returns cached response
- **PDF upload mode** — use `multipart/form-data` with `file` field (PDF) + all other fields as form params; max 10 MB
- **Webhook (roadmap v1.1)** — async endpoint for long consultation notes; result POSTed to callback URL
- **Batch (roadmap v1.2)** — `POST /api/v1/coding/notes/batch` — up to 20 notes per request

---

## 13. Pipeline Position (UC Integration Map)

```
[UC-01] Receipt & Invoice Parsing
        │  structured invoice JSON
        ▼
[UC-03] Automated Medical Coding  ◄─── vet clinical note (PDF or text)
        │  coded_findings[] attached to claim record
        ▼
[UC-02] Basic Claims Adjudication
        │  adjudication decision + reimbursement amount
        ▼
    Payment Engine / Member Portal
```

---

## 14. Out of Scope (v1)

- Radiograph or imaging file analysis (DICOM, JPEG) — text notes only
- Free-hand or handwritten note OCR
- VeNom (Veterinary Nomenclature) coding — SNOMED-CT and ICD-10 only
- Exotic species (rabbits, birds, reptiles) — dog and cat only
- Real-time coding during vet consultation
- ICD-11 (not yet adopted in target markets)
- Training or fine-tuning on proprietary clinical corpus

---

## 15. Open Questions

| # | Question | Owner |
|---|---|---|
| OQ-01 | Is SNOMED-CT VetSCT extension licensed for use, or will standard SNOMED-CT suffice? | Legal / Compliance |
| OQ-02 | Should pre-existing condition flags feed back into underwriting in real-time, or batch? | Product / Actuarial |
| OQ-03 | What is the human coder SLA for NEEDS_REVIEW notes — same-day or 48-hour turnaround? | Operations |
| OQ-04 | Are radiology reports submitted as separate notes or embedded in SOAP / discharge documents? | Clinical ops |
| OQ-05 | Should the API return a `previous_codes[]` field showing codes from prior visits for context? | Product |
| OQ-06 | Which ICD-10 edition is mandated — ICD-10-CM (US), ICD-10-PCS, or another regional variant? | Compliance |
