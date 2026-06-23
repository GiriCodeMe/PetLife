# Use Case: Longitudinal Medical History Review

_Generated: 2026-06-19_

---

## 1. Overview

Ingest and analyse multi-page bundles of historical veterinary records (30+ pages, spanning multiple years and clinics) to construct a chronological condition timeline, identify pre-existing conditions relative to a policy start date, detect chronic disease progression, and surface latent risk patterns. The output directly governs underwriting exclusions at enrolment and challenges to claims filed for conditions that pre-date the policy.

This UC handles the most data-intensive task in the LifeGroup pipeline. Unlike UC-03 (single-note coding), this UC reasons **across** a corpus of documents to draw longitudinal conclusions no single note can reveal.

---

## 2. Business Context

| Attribute | Detail |
|---|---|
| Domain | Pet (dog & cat) health insurance / LifeGroup underwriting & claims investigation |
| Stakeholder Role | Business Analyst |
| Trigger | New policy application with medical history attached OR claim challenge requiring history review |
| Outcome | Structured condition timeline + pre-existing flags + underwriting recommendation |
| Downstream Consumers | Underwriting engine (exclusion list), claims adjudication UC-02, fraud investigation |
| Volume Expectation | Low-medium — triggered selectively (not every claim); high per-request cost |
| Data Sensitivity | PHI-equivalent — full clinical history; highest data protection required |

---

## 3. AI Task Definition

**Task type:** Long-document multi-hop reasoning + event extraction + temporal synthesis

### Sub-task A — Document Parsing & Ordering

Extract individual clinical events (diagnoses, symptoms, lab results, medications, procedures) from each page of the record bundle. Associate each event with its date and source document.

### Sub-task B — Condition Timeline Construction

Aggregate events across all documents into a unified, chronologically ordered condition timeline. For each condition, identify:
- First documented occurrence (date + source)
- Subsequent mentions, recurrences, or progression notes
- Resolution date (if applicable)
- Whether the condition was explicitly named or only implied by symptoms or treatments

### Sub-task C — Pre-Existing Condition Determination

For each condition on the timeline, determine its status relative to the policy start date:
- **Pre-existing confirmed**: Explicitly diagnosed before policy start date
- **Pre-existing probable**: Symptoms documented before policy start but diagnosis came after
- **Pre-existing possible**: Single ambiguous mention; requires underwriter judgement
- **Post-policy onset**: No evidence prior to policy start date
- **Indeterminate**: Insufficient records to determine onset timing

### Sub-task D — Chronic Disease & Pattern Detection

Identify:
- Recurring conditions suggesting chronicity (≥ 3 mentions across ≥ 6-month span)
- Disease progression markers (e.g. CKD Stage 1 → Stage 2 → Stage 3 across visits)
- Latent risk patterns: repeated symptoms without a named diagnosis (e.g. recurring vomiting prior to an IBD diagnosis)
- Hereditary / breed-linked conditions that warrant pre-policy dating scrutiny

**Processing mode:** Local inference — no PHI transmitted externally. All processing on-premises.

---

## 4. Recommended Models

| Model | Quant | Context Window | VRAM / RAM | Notes |
|---|---|---|---|---|
| Llama 3.3 70B Instruct | Q4_K_M | 128K tokens | ~42 GB RAM | Primary — best long-context reasoning at open-weight scale |
| Llama 3.3 70B Instruct | Q8_0 | 128K tokens | ~80 GB RAM | Maximum fidelity; GPU-server deployments only |
| Llama 3.1 70B Instruct | Q4_K_M | 128K tokens | ~42 GB RAM | Alternative if 3.3 unavailable |
| Qwen2.5 72B Instruct | Q4_K_M | 128K tokens | ~44 GB RAM | Strong alternative; excellent instruction following |

**Recommendation:** `Llama 3.3 70B Q4_K_M` — 30+ page bundles easily fit within 128K context when text-extracted. Chunking only needed for extreme edge cases (> 150 pages).

**Runtime:** Ollama local or llama.cpp server | **Temperature:** 0.0

**Why 70B for this UC:** Pre-existing condition determination requires multi-hop temporal reasoning — matching symptoms in one note to a diagnosis confirmed months later in a different document. Smaller models (8B–14B) produce high false-negative rates on this task.

---

## 5. Input Schema

### 5a. Review Request

| Field | Type | Required | Description |
|---|---|---|---|
| `review_id` | string (UUID) | Yes | Unique request identifier |
| `context` | string (enum) | Yes | `UNDERWRITING` / `CLAIM_CHALLENGE` / `RENEWAL` |
| `policy_id` | string | No | Required for `CLAIM_CHALLENGE` and `RENEWAL` |
| `member_id` | string | Yes | Policyholder identifier |
| `policy_start_date` | date (ISO 8601) | Yes | Policy inception date — the pre-existing condition boundary |
| `claim_id` | string | No | Associated claim (for `CLAIM_CHALLENGE` context) |
| `patient_name` | string | Yes | Pet name |
| `patient_species` | string | Yes | `Canine` / `Feline` |
| `patient_breed` | string | No | Assists breed-linked condition interpretation |
| `patient_dob` | date (ISO 8601) | No | Used to compute age at each clinical event |
| `record_bundle` | binary (PDF) | Yes | Historical records bundle. Max 50 MB. Min 1 page. |
| `known_conditions[]` | array | No | Conditions already on the policy exclusion list — skip re-analysis |
| `focus_conditions[]` | array | No | Optional targeted list — prioritise finding evidence for these specific conditions |
| `submission_date` | date (ISO 8601) | Yes | Date of review request |

### 5b. Record Bundle Expectations

| Attribute | Expectation |
|---|---|
| Typical page count | 10–80 pages; modelled for up to 150 pages |
| Document types included | SOAP notes, discharge summaries, lab reports, radiology reports, vaccination records, referral letters |
| Date range | May span the pet's entire life; records from before the policy period are most relevant |
| Multi-clinic records | Common — records from multiple practices may be merged into one PDF |
| Format | Text-layer PDF required; scanned/image PDFs handled via OCR pre-processing (see pipeline) |

---

## 6. Pre-Existing Condition Classification Rules

| Rule ID | Rule | Classification |
|---|---|---|
| PE-01 | Condition explicitly diagnosed (named diagnosis) before `policy_start_date` | `PRE_EXISTING_CONFIRMED` |
| PE-02 | Symptoms of a condition documented ≥ 2 times before `policy_start_date`, even without a named diagnosis | `PRE_EXISTING_PROBABLE` |
| PE-03 | Single symptom mention or ambiguous reference before `policy_start_date` | `PRE_EXISTING_POSSIBLE` |
| PE-04 | First documented mention is ≥ 30 days after `policy_start_date`; no prior symptom evidence | `POST_POLICY_ONSET` |
| PE-05 | Condition mentioned in records but all occurrences lack dates or dates are illegible | `INDETERMINATE` |
| PE-06 | Condition resolved ≥ 12 months before `policy_start_date` with no recurrence noted | `RESOLVED_PRE_POLICY` — may still be noted as a risk factor |
| PE-07 | Breed-linked hereditary condition (e.g. hip dysplasia in a Labrador) documented any time before policy | Escalate to `PRE_EXISTING_PROBABLE` regardless of explicit diagnosis |
| PE-08 | Medication prescribed before `policy_start_date` for a condition not explicitly named | Infer condition from drug class; classify as `PRE_EXISTING_PROBABLE` |

---

## 7. Chronic Disease & Pattern Detection Rules

| Rule ID | Rule |
|---|---|
| CD-01 | Same condition mentioned in ≥ 3 separate notes spanning ≥ 6 months — classify as `CHRONIC` |
| CD-02 | Condition has documented staging that advances across visits (e.g. CKD Stage 1 → 2 → 3) — flag `PROGRESSION_DETECTED` |
| CD-03 | Same symptom cluster appears ≥ 3 times without a resolved diagnosis — flag `PATTERN_WITHOUT_DIAGNOSIS` |
| CD-04 | Weight loss > 10% from baseline documented across 2+ visits — flag as significant clinical finding |
| CD-05 | Lab value consistently outside reference range across ≥ 2 panels — flag as `PERSISTENT_LAB_ABNORMALITY` |
| CD-06 | Same medication class prescribed ≥ 3 times — infer recurring condition; flag if not otherwise diagnosed |
| CD-07 | Gap > 18 months between record dates — flag `RECORD_GAP` with date range; note records may be missing |

---

## 8. Output Schema

### 8a. Review Response

| Field | Type | Description |
|---|---|---|
| `review_id` | string | Echo of input ID |
| `completed_date` | date (ISO 8601) | Date review was completed |
| `overall_verdict` | string | `CLEAN` / `PRE_EXISTING_FOUND` / `CHRONIC_PATTERN_FOUND` / `NEEDS_REVIEW` |
| `underwriting_recommendation` | string | `APPROVE_STANDARD` / `APPROVE_WITH_EXCLUSIONS` / `REPRICE` / `MANUAL_UNDERWRITE` / `DECLINE` |
| `policy_start_date` | date | Echo of input — the classification boundary |
| `record_span` | object | `{ "earliest_date": "...", "latest_date": "...", "total_pages": n, "source_clinics": [...] }` |
| `condition_timeline[]` | array | Chronological list of all conditions found (see 8b) |
| `pre_existing_conditions[]` | array | Filtered subset — conditions classified PRE_EXISTING_CONFIRMED or PROBABLE |
| `chronic_patterns[]` | array | Conditions or patterns flagged under chronic detection rules (see 8c) |
| `record_gaps[]` | array | Date ranges where record continuity is broken (CD-07) |
| `unresolvable_items[]` | array | Events the model could not date or classify with any confidence |
| `adjudicator` | string | `AI` / `HUMAN` |
| `summary` | string | 100–300 word plain-language summary of key findings for underwriter |

### 8b. Condition Timeline Entry

| Field | Type | Description |
|---|---|---|
| `condition_id` | string | Sequential ID (C-01, C-02, …) |
| `condition_name` | string | Standardised condition name |
| `icd10_code` | string \| null | ICD-10-CM code from UC-03 (if available) |
| `snomed_code` | string \| null | SNOMED-CT code (if available) |
| `classification` | string | `PRE_EXISTING_CONFIRMED` / `PRE_EXISTING_PROBABLE` / `PRE_EXISTING_POSSIBLE` / `POST_POLICY_ONSET` / `INDETERMINATE` / `RESOLVED_PRE_POLICY` |
| `first_occurrence_date` | date \| null | Date of earliest documented mention |
| `first_occurrence_source` | string | Source document description (e.g. "SOAP note p.4, Greenfield AH, 2022-06-14") |
| `latest_occurrence_date` | date \| null | Most recent mention in the bundle |
| `occurrence_count` | integer | Total number of separate mentions |
| `is_chronic` | boolean | True if CD-01 rule triggered |
| `progression_notes` | string \| null | Description of disease staging progression if CD-02 triggered |
| `rule_applied` | string | Primary PE / CD rule that determined classification |
| `confidence` | string | `HIGH` / `MEDIUM` / `LOW` |
| `supporting_evidence[]` | array | Up to 3 verbatim quotes from records with source page and date |

### 8c. Chronic Pattern Entry

| Field | Type | Description |
|---|---|---|
| `pattern_id` | string | Sequential ID (P-01, P-02, …) |
| `pattern_type` | string | `CHRONIC_CONDITION` / `PATTERN_WITHOUT_DIAGNOSIS` / `PROGRESSION` / `PERSISTENT_LAB_ABNORMALITY` / `MEDICATION_RECURRENCE` |
| `description` | string | Plain-language description of the pattern |
| `first_signal_date` | date | Earliest date the pattern was present |
| `span_months` | integer | Duration of the pattern in months |
| `pre_policy` | boolean | True if the pattern began before `policy_start_date` |

---

## 9. Prompt Strategy

**Approach:** Three-pass pipeline to manage context and accuracy

### Pass 1 — Per-Document Event Extraction (parallelisable)

Each document section (identified by page range or clinic/date header) is processed independently:
```
You are a veterinary medical records analyst. Extract all clinical events from this
veterinary record excerpt.

For each event record: condition_name, event_type (diagnosis/symptom/procedure/lab/medication),
date (ISO 8601 if inferable, else null), verbatim_quote (the sentence that contains this event),
source_page, and whether the condition was negated.

Patient: {species}, {breed}, DOB: {dob}
Policy start date: {policy_start_date}

Record excerpt (pages {start}–{end}):
{text}

Return ONLY a JSON array of clinical event objects.
```

### Pass 2 — Timeline Synthesis (single call, full corpus)

All extracted events from Pass 1 are merged and passed in a single call:
```
You are a senior veterinary insurance underwriter. Given the following extracted clinical
events from a pet's complete medical history, construct a unified condition timeline.

For each distinct condition:
1. Aggregate all mentions into a single timeline entry.
2. Determine the classification relative to policy start date {policy_start_date} using these rules: {pe_rules}
3. Identify chronic patterns using these rules: {cd_rules}
4. Flag any conditions that are ambiguous or require human underwriter review.

Apply species-specific knowledge: {species_context}
Known excluded conditions (do not re-analyse): {known_conditions}

Extracted events (chronologically ordered):
{events_json}

Return ONLY valid JSON matching this schema: {output_schema}.
```

### Pass 3 — Summary Generation (optional, lightweight)

A short plain-language summary for the underwriter UI, run after Pass 2 validates:
```
Summarise the following veterinary history review findings in 150–200 words for a
non-clinical insurance underwriter. Highlight: number of pre-existing conditions found,
most significant conditions, policy recommendation, and any items needing human review.

Findings: {timeline_json}
```

**Temperature:** 0.0 for Pass 1 and 2 | 0.3 for Pass 3 (summary allows slight paraphrase)

---

## 10. Chunking Strategy (Records > 60 Pages)

| Scenario | Strategy |
|---|---|
| ≤ 60 pages (~90K tokens) | Single-pass: all text fits in 128K context; use 3-pass pipeline above |
| 61–150 pages | Chunked Pass 1: split into 30-page chunks, extract events per chunk in parallel; single Pass 2 synthesis |
| > 150 pages | Chunked Pass 1 + hierarchical Pass 2: synthesise per-decade-of-life first, then final synthesis |
| Scanned / image pages | OCR pre-processing (Tesseract) before text extraction; flag OCR confidence < 80% |

---

## 11. Pipeline Architecture

```
Record Bundle Upload (PDF)
        │
        ▼
OCR Check ───────────────── Text-layer PDF? → direct extraction
        │                    Image PDF? → Tesseract OCR → flag low-confidence pages
        ▼
Page Segmentation ─────────── Split into logical sections by date headers / clinic name
        │                       Assign page numbers and source labels
        ▼
Chronological Ordering ──────── Sort sections by extracted dates (handle out-of-order bundles)
        │                         Flag RECORD_GAP if > 18-month break between sections (CD-07)
        ▼
Pass 1: Event Extraction ────── Parallel per section (Llama 3.3 70B, local)
        │                         Each section → array of clinical event objects
        ▼
Event Deduplication ────────── Merge duplicate events (same condition + same date ± 3 days)
        │                        from overlapping or repeated records
        ▼
Pass 2: Timeline Synthesis ──── Single call: all events → unified condition timeline
        │                         PE rules + CD rules applied
        │                         JSON schema-constrained output
        ▼
Output Validation ──────────── Pydantic schema check
        │                        Date logic: first_occurrence ≤ latest_occurrence
        │                        Pre-existing conditions all have first_occurrence < policy_start_date
        ▼
Pass 3: Summary Generation ──── Plain-language underwriter summary (optional)
        │
        ▼
Structured Response ─────────── JSON → underwriting engine / claims adjudication / UI
```

---

## 12. Non-Functional Requirements

### 12a. Performance

| NFR | Target | Notes |
|---|---|---|
| Processing time — 30-page bundle (p95) | ≤ 5 min | Pass 1 parallelised; Pass 2 single call on ~30K tokens |
| Processing time — 80-page bundle (p95) | ≤ 12 min | Chunked extraction + synthesis |
| Processing time — 150-page bundle (p99) | ≤ 25 min | Hierarchical chunking mode |
| API async response | Immediate `202 Accepted` + `review_id`; result via webhook or polling | Synchronous response impractical at this latency |
| Pass 1 parallelism | Up to 10 sections processed concurrently per request | Bounded by model instance availability |

### 12b. Scalability

| NFR | Target | Notes |
|---|---|---|
| Concurrent reviews | ≥ 2 simultaneous (70B model is resource-intensive) | Queue depth ≤ 10 before 503 |
| Queue depth | ≤ 10 pending reviews | Each review blocks significant RAM; strict limit |
| Result caching | Completed reviews cached for 7 days by `review_id` | Avoids re-processing same bundle |
| Model cold start | ≤ 20 s for 70B Q4_K_M | Pre-loaded at server startup |

### 12c. Accuracy & Quality

| NFR | Target | Notes |
|---|---|---|
| Pre-existing detection recall | ≥ 95% | Missing a pre-existing condition is a critical underwriting failure |
| Pre-existing detection precision | ≥ 90% | False positives unjustly deny coverage; tolerance slightly lower than recall |
| Chronic pattern detection recall | ≥ 88% | Validated against 200-bundle golden dataset |
| Date extraction accuracy | ≥ 98% | Wrong dates on conditions invalidate pre-existing determination |
| Negation accuracy | ≥ 98% | Ruled-out conditions must not appear as pre-existing |
| Hallucination rate | < 0.5% of condition entries | Conditions not present in source records; verified by citation tracing |
| Supporting evidence citation | 100% of HIGH-confidence findings must cite verbatim source quote + page | Non-negotiable — underwriters must verify |

### 12d. Availability & Reliability

| NFR | Target | Notes |
|---|---|---|
| Uptime SLA | 99.0% monthly | Lower than other UCs due to batch/async nature |
| On failure | Return `FAILED` status with error detail; preserve uploaded bundle for retry | Do not silently lose records |
| Idempotency | Re-submitting same `review_id` with same bundle returns cached result | Hash-based bundle deduplication |
| PHI handling | Record text held in memory only during processing; not persisted in logs | Bundle optionally retained in encrypted storage per tenant config |

### 12e. Security

| NFR | Requirement |
|---|---|
| Authentication | Bearer token; scope: `history:review` |
| Transport | HTTPS only — TLS 1.2 minimum |
| PHI handling | Full clinical history — highest sensitivity; audit all access |
| Tenant isolation | Reviews and timelines strictly scoped to submitting tenant |
| Audit log | `review_id`, `tenant_id`, `context`, `page_count`, `overall_verdict`, `timestamp` only |
| Bundle encryption | Uploaded bundles encrypted at rest (AES-256) if retained; purged after 30 days unless legal hold |
| Result access | Review results accessible only by authenticated users with `history:read` scope |

### 12f. Observability

| NFR | Requirement |
|---|---|
| Health endpoint | `GET /health` — model load + queue depth |
| Metrics | Prometheus: queue depth, review duration histogram, pre-existing detection rate, pages-per-review histogram |
| Tracing | `review_id` propagated through all passes; Pass 1 child spans labelled by page range |
| Alerting | Alert if queue depth > 8 or p95 review time > 20 min over 30-minute window |
| Progress events | Server-Sent Events (SSE) stream on `GET /api/v1/history/reviews/{review_id}/progress` — emits pass-level status updates |

### 12g. Maintainability

| NFR | Requirement |
|---|---|
| PE / CD rule config | Rules in `pre_existing_rules.yaml` and `chronic_rules.yaml` — editable without deployment |
| Model swap | Model path in config; 3-pass prompts in separate prompt template files |
| OCR engine | Tesseract version pinned in container image; upgrade via rebuild |
| Backward compatibility | Result schema: additive changes only without version bump |

---

## 13. API Specification

### 13a. Endpoints

```
POST   /api/v1/history/reviews          — Submit a new review (async; returns 202)
GET    /api/v1/history/reviews/{id}     — Poll review status and retrieve result
GET    /api/v1/history/reviews/{id}/progress  — SSE stream of live progress updates
DELETE /api/v1/history/reviews/{id}     — Cancel a pending review or purge a completed result
```

**Content-Type (submit):** `multipart/form-data`
**Authentication:** `Authorization: Bearer <api_key>` (scope: `history:review`)

### 13b. Submit Request (POST)

| Parameter | Type | Required | Description |
|---|---|---|---|
| `record_bundle` | binary (PDF) | Yes | Historical records. Max 50 MB. |
| `review_id` | string (UUID) | Yes | Caller-supplied idempotency key |
| `context` | string | Yes | `UNDERWRITING` / `CLAIM_CHALLENGE` / `RENEWAL` |
| `member_id` | string | Yes | Policyholder ID |
| `policy_id` | string | No | Required for `CLAIM_CHALLENGE` / `RENEWAL` |
| `policy_start_date` | date | Yes | ISO 8601 |
| `patient_name` | string | Yes | |
| `patient_species` | string | Yes | `Canine` / `Feline` |
| `patient_breed` | string | No | |
| `patient_dob` | date | No | ISO 8601 |
| `focus_conditions` | string (JSON array) | No | e.g. `["diabetes mellitus","hip dysplasia"]` |
| `known_conditions` | string (JSON array) | No | Already excluded conditions to skip |

### 13c. Submit Response (HTTP 202 Accepted)

```json
{
  "review_id": "a9b8c7d6-e5f4-3210-abcd-ef9876543210",
  "status": "QUEUED",
  "estimated_minutes": 8,
  "poll_url": "/api/v1/history/reviews/a9b8c7d6-e5f4-3210-abcd-ef9876543210",
  "progress_stream_url": "/api/v1/history/reviews/a9b8c7d6-e5f4-3210-abcd-ef9876543210/progress"
}
```

### 13d. Poll Response — Completed (HTTP 200)

```json
{
  "review_id": "a9b8c7d6-e5f4-3210-abcd-ef9876543210",
  "status": "COMPLETED",
  "completed_date": "2024-03-20",
  "overall_verdict": "PRE_EXISTING_FOUND",
  "underwriting_recommendation": "APPROVE_WITH_EXCLUSIONS",
  "policy_start_date": "2024-01-15",
  "record_span": {
    "earliest_date": "2021-04-10",
    "latest_date":   "2024-01-08",
    "total_pages":   34,
    "source_clinics": ["Greenfield Animal Hospital", "BluePaw Emergency Center"]
  },
  "pre_existing_conditions": [
    {
      "condition_id":           "C-03",
      "condition_name":         "Atopic dermatitis",
      "icd10_code":             "L20.9",
      "classification":         "PRE_EXISTING_CONFIRMED",
      "first_occurrence_date":  "2022-06-14",
      "first_occurrence_source":"SOAP note p.8, Greenfield AH, 2022-06-14",
      "occurrence_count":       6,
      "is_chronic":             true,
      "rule_applied":           "PE-01",
      "confidence":             "HIGH",
      "supporting_evidence": [
        {"quote": "Mild atopic dermatitis diagnosed — seasonal pedal pruritus", "page": 8, "date": "2022-06-14"},
        {"quote": "Atopic dermatitis — ongoing, Cytopoint administered", "page": 14, "date": "2023-03-22"}
      ]
    }
  ],
  "summary": "34-page record bundle reviewed spanning April 2021 to January 2024..."
}
```

### 13e. Poll Response — In Progress (HTTP 200)

```json
{
  "review_id": "a9b8c7d6-e5f4-3210-abcd-ef9876543210",
  "status": "PROCESSING",
  "current_pass": "PASS_1_EVENT_EXTRACTION",
  "pages_processed": 18,
  "pages_total": 34,
  "estimated_minutes_remaining": 4
}
```

### 13f. Response — Error Codes

| HTTP Status | Code | Meaning |
|---|---|---|
| 400 | `BUNDLE_TOO_LARGE` | PDF exceeds 50 MB |
| 400 | `BUNDLE_TOO_SHORT` | PDF has fewer than 1 extractable page |
| 400 | `INVALID_POLICY_DATE` | `policy_start_date` not a valid ISO 8601 date |
| 400 | `MISSING_POLICY_ID` | `policy_id` required for `CLAIM_CHALLENGE` context |
| 401 | `UNAUTHORIZED` | Missing or invalid API key |
| 403 | `INSUFFICIENT_SCOPE` | API key lacks `history:review` scope |
| 409 | `DUPLICATE_REVIEW` | `review_id` already submitted; returns existing status |
| 429 | `RATE_LIMIT_EXCEEDED` | Tenant quota exceeded |
| 503 | `QUEUE_FULL` | Review queue at capacity (> 10); retry after indicated minutes |
| 500 | `INTERNAL_ERROR` | Unexpected error |

### 13g. Rate Limiting

| Tier | Reviews / hour | Reviews / day | Max concurrent |
|---|---|---|---|
| Starter | 2 | 10 | 1 |
| Growth | 10 | 50 | 2 |
| Enterprise | 30 | 200 | 5 |

### 13h. Versioning & Deprecation

Same policy as all LifeGroup UCs — additive changes without version bump; breaking schema changes require `/api/v2/`; 12-month support window.

### 13i. SaaS Integration Notes

- **Always async** — this UC never returns synchronously; always use poll or SSE
- **Idempotency** — same `review_id` + same bundle hash → cached result; bundle not re-processed
- **Focus conditions** — pass `focus_conditions` on claim challenges to reduce review time by directing Pass 1 attention to specific conditions
- **Webhook (roadmap v1.1)** — POST callback on completion instead of polling
- **Partial results** — if processing fails mid-bundle, partial results returned with `status: PARTIAL`

---

## 14. Pipeline Position (UC Integration Map)

```
[Record Bundle Upload — Underwriting or Claim Challenge]
        │
        ▼
[UC-05] Longitudinal Medical History Review
        │
        ├── CLEAN ─────────────────────────────► Underwriting: APPROVE_STANDARD
        ├── PRE_EXISTING_FOUND ────────────────► Underwriting: APPROVE_WITH_EXCLUSIONS or DECLINE
        │                                         Exclusion list fed into UC-02 Claims Adjudication
        ├── CHRONIC_PATTERN_FOUND ─────────────► Underwriting: REPRICE or MANUAL_UNDERWRITE
        └── NEEDS_REVIEW ──────────────────────► Manual underwriter review queue
                │
                ▼ (exclusions confirmed)
[UC-04] Breed & Fraud Verification
        │
        ▼
[UC-01] Receipt & Invoice Parsing
        │
        ▼
[UC-03] Automated Medical Coding
        │
        ▼
[UC-02] Basic Claims Adjudication ◄── exclusion list from UC-05 gates coverage decisions
```

---

## 15. Out of Scope (v1)

- Scanned / handwritten records without OCR pre-processing
- Records in languages other than English
- Dental radiograph or DICOM image analysis
- Genetic test reports or DNA breed analysis
- Vaccine titre analysis
- Integration with external vet record systems (PMS APIs)
- Real-time record streaming from vet practices
- Automated exclusion clause drafting (output is flags + recommendations only)
- Records exceeding 150 pages (hierarchical chunking roadmap for v1.1)

---

## 16. Open Questions

| # | Question | Owner |
|---|---|---|
| OQ-01 | What is the authoritative definition of "pre-existing" under LifeGroup policy terms — any mention before policy start, or a confirmed diagnosis? This determines PE-02 vs PE-01 as the operative rule. | Legal / Product |
| OQ-02 | Should `PRE_EXISTING_POSSIBLE` (PE-03, single ambiguous mention) be grounds for exclusion or only for flagging? | Actuarial / Legal |
| OQ-03 | Is the 18-month gap threshold for `RECORD_GAP` (CD-07) calibrated to policy terms, or should it be configurable per plan? | Product |
| OQ-04 | Should `RESOLVED_PRE_POLICY` conditions (PE-06) be excluded from coverage or carried as a risk note only? | Actuarial |
| OQ-05 | How are records from exotic or holistic practitioners handled — are they weighted equally to AVMA-licensed vet records? | Underwriting |
| OQ-06 | What retention period applies to uploaded record bundles — patient consent and data minimisation requirements may conflict with underwriting audit needs? | Legal / Compliance |
| OQ-07 | Should the API support incremental updates — adding newly submitted records to an existing review without full reprocessing? | Engineering / Product |
