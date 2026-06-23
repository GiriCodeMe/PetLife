# Use Case: Receipt & Invoice Parsing

_Generated: 2026-06-19_

---

## 1. Overview

Extract structured data from clean veterinary receipts and invoices (PDF format) using a locally-hosted LLM, enabling downstream automation for pet insurance claims, expense tracking, and reimbursement workflows.

---

## 2. Business Context

| Attribute | Detail |
|---|---|
| Domain | Pet health / LifeGroup insurance or vet billing |
| Stakeholder Role | Business Analyst |
| Trigger | User uploads a vet receipt or invoice PDF |
| Outcome | Structured JSON record ready for claims processing or reimbursement |
| Volume Expectation | Low-to-medium (individual claim submissions, not bulk) |
| Data Sensitivity | PII present (pet owner name, clinic address, dates) |

---

## 3. AI Task Definition

**Task type:** Document Information Extraction (structured output generation)

**Input:** Clean, text-layer PDF — vet receipt or itemized invoice (no handwriting, no scanned images)

**Output:** Structured JSON containing all extracted fields (see Section 5)

**Processing mode:** Local inference — no cloud API calls, data stays on-device

---

## 4. Recommended Models

| Model | Quant | VRAM / RAM | Notes |
|---|---|---|---|
| Llama 3.1 8B Instruct | Q4_K_M | ~6 GB RAM | Best speed/accuracy balance for structured extraction |
| Llama 3.1 8B Instruct | Q8_0 | ~9 GB RAM | Higher fidelity; preferred when hardware allows |
| Mistral 7B Instruct v0.3 | Q4_K_M | ~5 GB RAM | Lighter fallback; good instruction-following |
| Mistral 7B Instruct v0.3 | Q8_0 | ~8 GB RAM | Fallback with better precision |

**Recommendation:** Start with `Llama 3.1 8B Q4_K_M` — best general instruction-following for extraction tasks at this weight class.

**Runtime:** Ollama (local) or llama.cpp server

---

## 5. Extraction Fields

### 5a. Clinic / Provider Fields

| Field | Type | Required | Example |
|---|---|---|---|
| `clinic_name` | string | Yes | "Greenfield Animal Hospital" |
| `clinic_address` | string | No | "123 Main St, Austin TX 78701" |
| `clinic_phone` | string | No | "(512) 555-0192" |
| `invoice_number` | string | Yes | "INV-20240318-004" |
| `invoice_date` | date (ISO 8601) | Yes | "2024-03-18" |
| `visit_date` | date (ISO 8601) | Yes | "2024-03-17" |

### 5b. Patient Fields

| Field | Type | Required | Example |
|---|---|---|---|
| `patient_name` | string | Yes | "Biscuit" |
| `patient_species` | string | Yes | "Canine" |
| `patient_breed` | string | No | "Golden Retriever" |
| `owner_name` | string | Yes | "Jane Doe" |

### 5c. Line Items (array)

Each item in `line_items[]`:

| Field | Type | Required | Example |
|---|---|---|---|
| `description` | string | Yes | "Annual wellness exam" |
| `category` | string | No | "Exam", "Medication", "Lab", "Procedure", "Vaccine" |
| `quantity` | number | Yes | 1 |
| `unit_price` | number | Yes | 85.00 |
| `line_total` | number | Yes | 85.00 |

### 5d. Totals

| Field | Type | Required | Example |
|---|---|---|---|
| `subtotal` | number | Yes | 210.00 |
| `tax_rate` | number | No | 0.0825 |
| `tax_amount` | number | No | 17.33 |
| `discount_amount` | number | No | 0.00 |
| `total_due` | number | Yes | 227.33 |
| `amount_paid` | number | No | 227.33 |
| `balance_due` | number | No | 0.00 |
| `currency` | string | Yes | "USD" |

---

## 6. Prompt Strategy

**Approach:** Structured extraction with JSON schema enforcement

**Prompt pattern:**
```
You are a document parser. Extract all fields from the following veterinary invoice text.
Return ONLY valid JSON matching this schema: {schema}.
If a field is not present in the document, return null for that field.
Do not infer or hallucinate values.

Document text:
{extracted_pdf_text}
```

**Schema enforcement options:**
- Ollama: use `format: "json"` parameter + schema in system prompt
- llama.cpp: use grammar-constrained decoding (GBNF grammar file for the JSON schema)

**Temperature:** 0.0 — deterministic output required for financial data

---

## 7. Pipeline Architecture

```
PDF Upload
    │
    ▼
PDF Text Extraction  ──── pdfplumber or pypdf (Python)
    │
    ▼
Text Cleaning        ──── Remove headers/footers, normalize whitespace
    │
    ▼
LLM Inference        ──── Ollama local endpoint (localhost:11434)
    │                      Model: llama3.1:8b-instruct-q4_K_M
    ▼
JSON Validation      ──── Pydantic model or jsonschema
    │
    ▼
Structured Output    ──── JSON record → claims DB / UI / export
```

---

## 8. Constraints & Assumptions

| Constraint | Detail |
|---|---|
| PDF quality | Text layer must be present — scanned/image PDFs are out of scope |
| Language | English only (v1 scope) |
| Page length | Single or multi-page invoices; max ~10 pages per document |
| Currency | USD assumed unless explicitly stated |
| Privacy | No PII leaves the local machine; no cloud LLM calls |
| Hallucination risk | Model must return `null` for missing fields, not inferred values |

---

## 9. Acceptance Criteria

| # | Criterion |
|---|---|
| AC-01 | All required fields extracted from a standard vet invoice with ≥ 95% accuracy |
| AC-02 | `invoice_date` and `visit_date` always returned in ISO 8601 format |
| AC-03 | `line_items` array correctly reflects each row on the invoice |
| AC-04 | `total_due` matches sum of `line_items` + `tax_amount` − `discount_amount` (within $0.02 rounding) |
| AC-05 | Missing optional fields return `null`, not empty string or fabricated value |
| AC-06 | Inference completes in under 10 seconds on CPU for a 2-page PDF |
| AC-07 | Output passes JSON schema validation with zero required-field violations |

---

## 10. Non-Functional Requirements

### 10a. Performance

| NFR | Target | Notes |
|---|---|---|
| Inference latency (p95) | ≤ 10 s per document (2-page) | CPU-only baseline; GPU inference target ≤ 3 s |
| API response time (p95) | ≤ 15 s end-to-end (PDF upload → JSON response) | Includes PDF text extraction + LLM inference |
| API response time (p99) | ≤ 30 s | For large multi-page invoices (up to 10 pages) |
| Throughput | ≥ 10 concurrent requests | Requests queued; model runs single-threaded per instance |

### 10b. Scalability

| NFR | Target | Notes |
|---|---|---|
| Horizontal scaling | Stateless API — scale by adding instances behind a load balancer | Each instance runs its own local model replica |
| Request queue depth | ≤ 100 queued requests before 503 is returned | Prevents unbounded queue growth |
| Model load time | ≤ 5 s cold start | Model pre-loaded on container startup, not per-request |

### 10c. Availability & Reliability

| NFR | Target | Notes |
|---|---|---|
| Uptime SLA | 99.5% monthly | Planned maintenance windows excluded |
| Graceful degradation | Return HTTP 503 with `Retry-After` header when overloaded | Never silently drop requests |
| Error rate | < 1% of requests return a 5xx error under normal load | |
| Model fallback | If primary model fails to produce valid JSON after 2 retries, return structured error | Do not return partial/malformed output |
| Data durability | Uploaded PDFs deleted from server within 60 s of processing | No persistent storage of PII |

### 10d. Security

| NFR | Requirement |
|---|---|
| Authentication | All API calls require a valid API key (Bearer token in `Authorization` header) |
| Transport | HTTPS only — TLS 1.2 minimum, TLS 1.3 preferred |
| Input validation | PDF file size capped at 10 MB; MIME type validated server-side |
| PII handling | No PDF content or extracted PII logged to application logs |
| Data isolation | Multi-tenant: one SaaS customer's data must never appear in another's response |
| API key scope | Keys are per-tenant; rotation supported without downtime |
| Rate limiting | Per API key (see Section 11c) |
| Audit trail | Log `tenant_id`, `request_id`, `timestamp`, `page_count`, `status` only — no document content |

### 10e. Observability

| NFR | Requirement |
|---|---|
| Health endpoint | `GET /health` returns 200 + model load status within 500 ms |
| Metrics | Expose Prometheus metrics: request count, latency histogram, queue depth, error rate |
| Distributed tracing | Each request tagged with `request_id` (UUID v4); propagated through all internal steps |
| Alerting thresholds | Alert if p95 latency > 20 s or error rate > 2% over a 5-minute window |
| Log format | Structured JSON logs; no free-text; no PII |

### 10f. Maintainability & Portability

| NFR | Requirement |
|---|---|
| Model swap | Swapping LLM model requires only a config change + container restart — no code change |
| Containerization | Deployable as a Docker image; GGUF model file mounted as a volume |
| Config-driven | Model path, quantization, max tokens, temperature all set via environment variables |
| Backward compatibility | API v1 schema changes are additive only; breaking changes require a new major version |

---

## 11. API Specification

### 11a. Endpoint

```
POST /api/v1/invoices/parse
```

**Content-Type:** `multipart/form-data`
**Authentication:** `Authorization: Bearer <api_key>`

### 11b. Request

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file` | binary (PDF) | Yes | The invoice or receipt PDF file. Max 10 MB. |
| `tenant_id` | string | Yes | Caller's tenant identifier for audit logging |
| `request_id` | string (UUID) | No | Caller-supplied idempotency key; auto-generated if omitted |
| `hints.clinic_name` | string | No | Optional pre-known clinic name to improve extraction accuracy |

**Example cURL:**
```bash
curl -X POST https://api.lifegroup.io/api/v1/invoices/parse \
  -H "Authorization: Bearer $API_KEY" \
  -F "file=@invoice-2024-03-18.pdf" \
  -F "tenant_id=tenant_abc123"
```

### 11c. Response — Success (HTTP 200)

```json
{
  "request_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "status": "success",
  "processing_time_ms": 4821,
  "data": {
    "clinic_name": "Greenfield Animal Hospital",
    "clinic_address": "123 Main St, Austin TX 78701",
    "clinic_phone": "(512) 555-0192",
    "invoice_number": "INV-20240318-004",
    "invoice_date": "2024-03-18",
    "visit_date": "2024-03-17",
    "patient_name": "Biscuit",
    "patient_species": "Canine",
    "patient_breed": "Golden Retriever",
    "owner_name": "Jane Doe",
    "line_items": [
      {
        "description": "Annual wellness exam",
        "category": "Exam",
        "quantity": 1,
        "unit_price": 85.00,
        "line_total": 85.00
      },
      {
        "description": "Rabies vaccine",
        "category": "Vaccine",
        "quantity": 1,
        "unit_price": 32.00,
        "line_total": 32.00
      }
    ],
    "subtotal": 210.00,
    "tax_rate": 0.0825,
    "tax_amount": 17.33,
    "discount_amount": 0.00,
    "total_due": 227.33,
    "amount_paid": 227.33,
    "balance_due": 0.00,
    "currency": "USD"
  }
}
```

### 11d. Response — Error Codes

| HTTP Status | Code | Meaning |
|---|---|---|
| 400 | `INVALID_FILE_TYPE` | Uploaded file is not a valid PDF |
| 400 | `FILE_TOO_LARGE` | PDF exceeds 10 MB limit |
| 400 | `NO_TEXT_LAYER` | PDF appears to be a scanned image; text extraction failed |
| 401 | `UNAUTHORIZED` | Missing or invalid API key |
| 422 | `EXTRACTION_FAILED` | Model returned output that failed JSON schema validation after retries |
| 429 | `RATE_LIMIT_EXCEEDED` | Tenant has exceeded their request quota |
| 503 | `SERVICE_OVERLOADED` | Request queue full; retry after indicated seconds |
| 500 | `INTERNAL_ERROR` | Unexpected server error; contact support with `request_id` |

**Error response shape:**
```json
{
  "request_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "status": "error",
  "error": {
    "code": "NO_TEXT_LAYER",
    "message": "The uploaded PDF does not contain extractable text. Scanned documents are not supported in v1.",
    "docs_url": "https://docs.lifegroup.io/api/errors#NO_TEXT_LAYER"
  }
}
```

### 11e. Rate Limiting

| Tier | Requests / minute | Requests / day | Burst |
|---|---|---|---|
| Starter | 10 | 500 | 15 |
| Growth | 60 | 5,000 | 100 |
| Enterprise | 300 | 50,000 | 500 |

Rate limit headers returned on every response:
```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 43
X-RateLimit-Reset: 1710768060
```

### 11f. Versioning & Deprecation

| Policy | Detail |
|---|---|
| URL versioning | `/api/v1/` — major version in path |
| Additive changes | New optional response fields added without version bump |
| Breaking changes | Require new major version (`/api/v2/`); v1 supported for minimum 12 months after v2 GA |
| Sunset notice | Deprecation announced via `Deprecation` response header + email to tenant admins |

### 11g. SaaS Integration Notes

- API is **stateless** — callers must not assume session affinity between requests
- **Idempotency:** re-submitting the same `request_id` within 5 minutes returns the cached response without re-running inference
- **Webhook support (roadmap):** for async processing of large batches, a `POST /api/v1/invoices/parse/async` endpoint with callback URL is planned for v1.1
- **SDK availability (roadmap):** Python and Node.js SDKs planned post-GA

---

## 12. Out of Scope (v1)

- Scanned / image-only PDFs (requires OCR pre-processing — future iteration)
- Multi-currency invoices
- Non-English documents
- Automated submission to insurance portal
- Training or fine-tuning the model on domain data

---

## 13. Open Questions

| # | Question | Owner |
|---|---|---|
| OQ-01 | What downstream system consumes the JSON output (claims DB, spreadsheet, API)? | BA / Product |
| OQ-02 | Is a confidence score per field required for auditing purposes? | Stakeholder |
| OQ-03 | Should `category` be free-text or constrained to a fixed enum? | BA / Claims team |
| OQ-04 | Are there existing invoice templates from partner clinics to test against? | Operations |
