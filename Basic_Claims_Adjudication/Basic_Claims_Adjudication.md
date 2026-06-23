# Use Case: Basic Claims Adjudication

_Generated: 2026-06-19_

---

## 1. Overview

Apply rule-based policy logic to itemized veterinary claim data (sourced from parsed invoices) to determine per-line-item approval, denial, or partial coverage — producing a structured adjudication decision ready for payment processing or member notification.

This use case operates **downstream** of the Receipt & Invoice Parsing UC. It consumes the structured JSON output from that pipeline and enriches it with adjudication outcomes against a member's active policy.

---

## 2. Business Context

| Attribute | Detail |
|---|---|
| Domain | **Pet** (dog & cat) health insurance / LifeGroup claims processing |
| Stakeholder Role | Business Analyst |
| Trigger | Parsed invoice JSON + member policy ID submitted for adjudication |
| Outcome | Per-line adjudication decision (approved / partially approved / denied) + reimbursement amount |
| Downstream Consumer | Claims payment engine, member portal, adjuster review queue |
| Volume Expectation | Medium (batch and real-time submission from SaaS platform) |
| Data Sensitivity | PHI-equivalent (pet medical records + financial data); treat as PII |

---

## 3. AI Task Definition

**Task type:** Rule-based reasoning + structured decision generation

**Inputs:**
1. Parsed invoice JSON (output from Receipt & Invoice Parsing UC)
2. Member policy record (coverage limits, deductible status, benefit balance, exclusions)

**AI role:** Interpret each line item against policy rules and generate a per-item decision with reasoning. The LLM does NOT set policy limits — it applies pre-loaded rules deterministically and flags ambiguous cases for human review.

**Output:** Structured adjudication JSON with per-item decisions, reimbursement totals, and a claim-level summary verdict.

**Processing mode:** Local inference — no cloud API calls; policy and member data stay on-device.

---

## 4. Recommended Models

| Model | Quant | VRAM / RAM | Strength |
|---|---|---|---|
| Phi-4 14B Instruct | Q4_K_M | ~10 GB RAM | Best rule-following and structured reasoning at this weight class |
| Phi-4 14B Instruct | Q8_0 | ~16 GB RAM | Higher fidelity reasoning; preferred when hardware allows |
| Llama 3.1 8B Instruct | Q4_K_M | ~6 GB RAM | Lighter fallback; acceptable for simple rule sets |
| Llama 3.1 8B Instruct | Q8_0 | ~9 GB RAM | Fallback with better precision for multi-condition rules |

**Recommendation:** `Phi-4 14B Q4_K_M` — adjudication requires multi-condition reasoning (deductible + per-category limits + annual cap + exclusion list simultaneously); 14B outperforms 8B meaningfully on this task type.

**Runtime:** Ollama local or llama.cpp server. Temperature: **0.0**

---

## 5. Input Schema

### 5a. Claim Submission (request body)

| Field | Type | Required | Description |
|---|---|---|---|
| `claim_id` | string (UUID) | Yes | Unique claim submission identifier |
| `member_id` | string | Yes | Policyholder member identifier |
| `policy_id` | string | Yes | Active policy identifier |
| `invoice` | object | Yes | Full parsed invoice JSON (from UC: Receipt & Invoice Parsing) |
| `submission_date` | date (ISO 8601) | Yes | Date the claim was submitted |
| `submitted_by` | string | No | `member` or `clinic` |

### 5b. Policy Record (resolved server-side from `policy_id`)

| Field | Type | Description |
|---|---|---|
| `policy_id` | string | Policy identifier |
| `plan_name` | string | e.g. "Gold Wellness Plus" |
| `policy_year_start` | date | Policy year start date |
| `policy_year_end` | date | Policy year end date |
| `deductible_annual` | number | Annual deductible amount (e.g. 250.00) |
| `deductible_met` | number | Deductible already satisfied this policy year |
| `coinsurance_pct` | number | Member co-pay percentage after deductible (e.g. 0.20 = 20%) |
| `annual_benefit_limit` | number | Maximum reimbursement per policy year |
| `annual_benefit_used` | number | Reimbursement paid out so far this year |
| `category_limits[]` | array | Per-category annual sub-limits (see 5c) |
| `exclusions[]` | array | List of excluded service descriptions or category codes |
| `waiting_period_end` | date | Claims for services before this date are ineligible |

### 5c. Category Limit Object

| Field | Type | Description |
|---|---|---|
| `category` | string | Matches `line_items[].category` from invoice |
| `annual_limit` | number | Max reimbursement for this category per year |
| `used` | number | Amount already reimbursed in this category this year |
| `per_incident_limit` | number \| null | Max per single visit/incident (if applicable) |

---

## 6. Adjudication Rules

Rules are evaluated in priority order per line item:

| Rule # | Rule | Decision |
|---|---|---|
| R-01 | Service date is before `waiting_period_end` | `DENIED` — waiting period not satisfied |
| R-02 | Line item `category` or `description` matches `exclusions[]` | `DENIED` — excluded service |
| R-03 | Remaining `annual_benefit_limit` is $0.00 | `DENIED` — annual benefit exhausted |
| R-04 | Remaining category sub-limit is $0.00 | `DENIED` — category limit exhausted |
| R-05 | Deductible not yet fully met | Apply remaining deductible to line total; remainder eligible |
| R-06 | `line_total` exceeds remaining category sub-limit | `PARTIAL` — approve up to category limit remainder only |
| R-07 | Approved amount after deductible exceeds remaining annual benefit | Cap at remaining annual benefit |
| R-08 | Apply `coinsurance_pct` to eligible amount | Reduce approved amount by member co-pay percentage |
| R-09 | All rules pass | `APPROVED` — full eligible amount reimbursed |

**Ambiguity rule:** If the LLM cannot confidently match a line item description to a policy category, return status `NEEDS_REVIEW` and route to human adjuster.

---

## 7. Output Schema

### 7a. Adjudication Decision (response body)

| Field | Type | Description |
|---|---|---|
| `claim_id` | string | Echo of input `claim_id` |
| `adjudication_date` | date (ISO 8601) | Date adjudication was completed |
| `overall_status` | string | `APPROVED` / `PARTIALLY_APPROVED` / `DENIED` / `NEEDS_REVIEW` |
| `total_billed` | number | Sum of all invoice `line_total` values |
| `total_eligible` | number | Total after exclusions and limits applied |
| `deductible_applied` | number | Deductible amount consumed by this claim |
| `coinsurance_applied` | number | Member co-pay amount across all eligible items |
| `total_reimbursable` | number | Net amount LifeGroup will pay |
| `line_decisions[]` | array | Per-line adjudication (see 7b) |
| `updated_benefit_balance` | number | Remaining annual benefit after this claim |
| `adjudicator` | string | `AI` or `HUMAN` |
| `notes` | string \| null | Free-text summary of key decisions |

### 7b. Line Decision Object

| Field | Type | Description |
|---|---|---|
| `description` | string | Line item description (echoed from invoice) |
| `category` | string | Mapped category |
| `billed_amount` | number | Original `line_total` from invoice |
| `eligible_amount` | number | Amount eligible after exclusion/limit checks |
| `deductible_portion` | number | Deductible applied to this line |
| `coinsurance_portion` | number | Member co-pay on this line |
| `reimbursable_amount` | number | Net amount approved for this line |
| `status` | string | `APPROVED` / `PARTIAL` / `DENIED` / `NEEDS_REVIEW` |
| `denial_reason` | string \| null | Reason code if `DENIED` or `PARTIAL` (see 7c) |
| `rule_applied` | string \| null | Rule ID that determined the outcome (e.g. `R-06`) |

### 7c. Denial Reason Codes

| Code | Meaning |
|---|---|
| `WAITING_PERIOD` | Service date precedes policy waiting period end |
| `EXCLUDED_SERVICE` | Service or category is explicitly excluded by policy |
| `ANNUAL_BENEFIT_EXHAUSTED` | Policy year reimbursement limit already reached |
| `CATEGORY_LIMIT_EXHAUSTED` | Per-category sub-limit fully consumed |
| `CATEGORY_LIMIT_EXCEEDED` | Billed amount exceeds remaining category limit (partial) |
| `NOT_COVERED` | Service not listed in covered benefits |
| `AMBIGUOUS_CATEGORY` | LLM could not confidently map description to a policy category |

---

## 8. Prompt Strategy

**Approach:** Chain-of-thought reasoning with structured JSON output and rule injection

**Prompt pattern:**
```
You are a veterinary insurance claims adjudicator. Apply the policy rules below to each
line item in the claim. For each line, determine: status, eligible_amount,
deductible_portion, coinsurance_portion, reimbursable_amount, and denial_reason.

Policy Rules (apply in order):
{serialized_rules}

Member Policy Context:
{policy_summary}

Invoice Line Items:
{line_items_json}

Return ONLY valid JSON matching this schema: {output_schema}.
If you cannot confidently assign a category to a line item, set status to NEEDS_REVIEW.
Do not infer or hallucinate policy limits. Use only the values provided above.
```

**Chain-of-thought:** Include `"reasoning"` field in line decisions during development/testing; strip from production responses to reduce payload size.

**Temperature:** 0.0 — deterministic decisions required for financial/insurance outcomes

**Grammar enforcement:** Use GBNF grammar or Pydantic model to constrain JSON output at token level

---

## 9. Pipeline Architecture

```
Claim Submission (API)
        │
        ├── invoice JSON (from UC-01 output)
        └── member_id + policy_id
                │
                ▼
        Policy Lookup  ──── Internal policy DB / cache (Redis)
                │            Resolves category limits, deductible status,
                │            annual benefit balance, exclusion list
                ▼
        Rule Serialization ── Convert policy record → prompt-injectable rule block
                │
                ▼
        LLM Adjudication ──── Ollama local (phi4:14b-instruct-q4_K_M)
                │              Temperature: 0.0, grammar-constrained JSON
                ▼
        Output Validation ──── Pydantic schema check
                │               Math verification: sum(reimbursable) == total_reimbursable
                ▼
        Confidence Check ────  Any NEEDS_REVIEW items?
                │                   YES → route to human adjuster queue
                │                   NO  → auto-approve
                ▼
        Benefit Balance Update ── Write updated deductible_met + annual_benefit_used
                │                  back to policy DB (idempotent, keyed on claim_id)
                ▼
        Adjudication Response ── Structured JSON → payment engine / member portal
```

---

## 10. Non-Functional Requirements

### 10a. Performance

| NFR | Target | Notes |
|---|---|---|
| Adjudication latency (p95) | ≤ 20 s per claim | Phi-4 14B is slower than 8B; acceptable for insurance workflow |
| Adjudication latency (p99) | ≤ 45 s | For claims with 15+ line items |
| API end-to-end response (p95) | ≤ 25 s | Includes policy lookup + inference + validation |
| Throughput | ≥ 5 concurrent adjudications | Claims workload is bursty, not continuous |

### 10b. Scalability

| NFR | Target | Notes |
|---|---|---|
| Horizontal scaling | Stateless API — scale by instance; each carries own model replica | |
| Policy cache TTL | 5 minutes (Redis) | Reduces DB reads for repeated member submissions |
| Queue depth | ≤ 50 queued claims before 503 returned | |
| Model cold start | ≤ 8 s | Phi-4 14B loads slower than 8B |

### 10c. Availability & Reliability

| NFR | Target | Notes |
|---|---|---|
| Uptime SLA | 99.5% monthly | Planned maintenance excluded |
| Adjudication accuracy | ≥ 99% on rule-deterministic items | Validated against golden dataset of 500 labeled claims |
| NEEDS_REVIEW rate | < 5% of line items | Higher rate indicates prompt or category-mapping degradation |
| Idempotency | Re-submitting same `claim_id` returns cached decision without re-running inference | Prevents double-adjudication |
| Benefit balance updates | Exactly-once writes; rollback on validation failure | Prevents incorrect deduction from annual benefit |
| Fallback | If model fails after 2 retries, route entire claim to `NEEDS_REVIEW` queue | Never silently fail or partially persist |

### 10d. Security

| NFR | Requirement |
|---|---|
| Authentication | Bearer token (`Authorization` header) — same scheme as UC-01 |
| Authorization | API key must have `claims:adjudicate` scope; read-only keys rejected |
| Transport | HTTPS only — TLS 1.2 minimum |
| Tenant isolation | Policy records and claim decisions strictly scoped to `tenant_id`; cross-tenant reads rejected at DB layer |
| Data retention | Claim decisions retained per regulatory requirement (7 years); raw LLM prompt/response not stored |
| Audit log | Log `claim_id`, `tenant_id`, `adjudicator`, `overall_status`, `total_reimbursable`, `timestamp` — no PHI |
| Policy data | Policy limits and rules loaded server-side; never passed by client to prevent manipulation |

### 10e. Observability

| NFR | Requirement |
|---|---|
| Health endpoint | `GET /health` — returns model load status + policy DB connectivity |
| Metrics | Prometheus: request count, latency histogram, NEEDS_REVIEW rate, denial rate by reason code |
| Tracing | `claim_id` propagated through all pipeline steps as trace ID |
| Alerting | Alert if NEEDS_REVIEW rate > 8% or p95 latency > 40 s over 5-minute window |
| Audit trail | Immutable append-only log of every adjudication decision per claim |

### 10f. Maintainability

| NFR | Requirement |
|---|---|
| Rule updates | Policy rules updated via config/DB — no code change or model retraining required |
| Model swap | Swapping from Phi-4 to Llama requires only config change + restart |
| Backward compatibility | Additive response fields only; breaking schema changes require `/api/v2/` |
| Test coverage | Golden dataset of 500 labeled claims must pass regression on every rule or model change |

---

## 11. API Specification

### 11a. Endpoint

```
POST /api/v1/claims/adjudicate
```

**Content-Type:** `application/json`
**Authentication:** `Authorization: Bearer <api_key>` (scope: `claims:adjudicate`)

### 11b. Request Body

```json
{
  "claim_id": "c7a1f3d0-4e2b-4f9a-8c5e-1a2b3c4d5e6f",
  "member_id": "MEM-00412",
  "policy_id": "POL-GOLD-2024-00412",
  "submission_date": "2024-03-20",
  "submitted_by": "member",
  "invoice": {
    "invoice_number": "INV-20240318-001",
    "invoice_date": "2024-03-18",
    "visit_date": "2024-03-18",
    "clinic_name": "Greenfield Animal Hospital",
    "patient_name": "Biscuit",
    "patient_species": "Canine",
    "owner_name": "Jane Doe",
    "line_items": [
      {
        "description": "Annual wellness examination",
        "category": "Exam",
        "quantity": 1,
        "unit_price": 85.00,
        "line_total": 85.00
      }
    ],
    "subtotal": 85.00,
    "tax_amount": 0.00,
    "total_due": 85.00,
    "currency": "USD"
  }
}
```

### 11c. Response — Success (HTTP 200)

```json
{
  "claim_id": "c7a1f3d0-4e2b-4f9a-8c5e-1a2b3c4d5e6f",
  "adjudication_date": "2024-03-20",
  "overall_status": "APPROVED",
  "total_billed": 244.00,
  "total_eligible": 244.00,
  "deductible_applied": 0.00,
  "coinsurance_applied": 48.80,
  "total_reimbursable": 195.20,
  "updated_benefit_balance": 4804.80,
  "adjudicator": "AI",
  "notes": "All line items covered under Gold Wellness Plus. 20% co-insurance applied.",
  "line_decisions": [
    {
      "description": "Annual wellness examination",
      "category": "Exam",
      "billed_amount": 85.00,
      "eligible_amount": 85.00,
      "deductible_portion": 0.00,
      "coinsurance_portion": 17.00,
      "reimbursable_amount": 68.00,
      "status": "APPROVED",
      "denial_reason": null,
      "rule_applied": "R-09"
    }
  ]
}
```

### 11d. Response — Error Codes

| HTTP Status | Code | Meaning |
|---|---|---|
| 400 | `INVALID_INVOICE_JSON` | Invoice object failed schema validation |
| 400 | `MISSING_CLAIM_ID` | `claim_id` not provided |
| 401 | `UNAUTHORIZED` | Missing or invalid API key |
| 403 | `INSUFFICIENT_SCOPE` | API key lacks `claims:adjudicate` scope |
| 404 | `POLICY_NOT_FOUND` | No active policy found for `policy_id` |
| 404 | `MEMBER_NOT_FOUND` | `member_id` does not exist |
| 409 | `DUPLICATE_CLAIM` | `claim_id` already adjudicated; returns cached decision |
| 422 | `ADJUDICATION_FAILED` | Model failed to produce valid decision after retries |
| 429 | `RATE_LIMIT_EXCEEDED` | Tenant quota exceeded |
| 503 | `SERVICE_OVERLOADED` | Queue full; retry after indicated seconds |
| 500 | `INTERNAL_ERROR` | Unexpected error; include `claim_id` when contacting support |

**Error response shape:**
```json
{
  "claim_id": "c7a1f3d0-4e2b-4f9a-8c5e-1a2b3c4d5e6f",
  "status": "error",
  "error": {
    "code": "POLICY_NOT_FOUND",
    "message": "No active policy found for policy_id 'POL-GOLD-2024-00412'. Verify the member's enrollment status.",
    "docs_url": "https://docs.lifegroup.io/api/errors#POLICY_NOT_FOUND"
  }
}
```

### 11e. Rate Limiting

| Tier | Claims / minute | Claims / day | Burst |
|---|---|---|---|
| Starter | 5 | 200 | 10 |
| Growth | 30 | 2,000 | 60 |
| Enterprise | 150 | 20,000 | 300 |

Rate limit headers returned on every response:
```
X-RateLimit-Limit: 30
X-RateLimit-Remaining: 22
X-RateLimit-Reset: 1710768120
```

### 11f. Versioning & Deprecation

| Policy | Detail |
|---|---|
| URL versioning | `/api/v1/` — major version in path |
| Additive changes | New optional response fields added without version bump |
| Breaking changes | Require new major version (`/api/v2/`); v1 supported minimum 12 months post-v2 GA |
| Sunset notice | `Deprecation` response header + email to tenant admins |

### 11g. SaaS Integration Notes

- **Stateless** — no session affinity required between requests
- **Idempotency** — same `claim_id` within 24 hours returns cached decision; policy balance not double-debited
- **NEEDS_REVIEW webhook** (roadmap v1.1) — POST callback to SaaS platform when a claim item requires human adjuster
- **Batch endpoint** (roadmap v1.2) — `POST /api/v1/claims/adjudicate/batch` for up to 50 claims per request
- **Policy sync** — SaaS platforms must push policy updates via `PUT /api/v1/policies/{policy_id}` before submitting claims; stale policy data is the caller's responsibility

---

## 12. Out of Scope (v1)

- Complex multi-condition exclusions requiring clinical judgement (e.g. pre-existing condition determination)
- Fraud detection or anomaly scoring
- Multi-species policy logic beyond standard dog & cat (e.g. exotic pets, birds, reptiles)
- Appeals processing or decision reversal
- Integration with state insurance regulatory reporting
- Real-time veterinary coding (ICD / procedure code) validation

---

## 13. Open Questions

| # | Question | Owner |
|---|---|---|
| OQ-01 | What is the authoritative source of policy records — internal DB, third-party benefits admin, or both? | Platform / Eng |
| OQ-02 | Should `coinsurance_pct` be applied before or after the deductible? (Standard: deductible first) | Actuarial / Legal |
| OQ-03 | Is there a per-incident deductible in addition to the annual deductible? | Product |
| OQ-04 | What SLA applies to human-adjuster resolution of `NEEDS_REVIEW` items? | Operations |
| OQ-05 | Should tax amounts on the invoice be included in the reimbursable calculation? | Actuarial |
| OQ-06 | Are there category limits that reset mid-year (e.g. wellness allowance per 6-month period)? | Product |
