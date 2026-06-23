# Use Case 06 — Multi-Agent Risk Underwriting

_Generated: 2026-06-19_

---

## 1. Overview

| Field | Value |
|---|---|
| UC ID | UC-06 |
| Title | Multi-Agent Risk Underwriting |
| Domain | Pet (dog & cat) health insurance — SaaS platform |
| Status | Draft |
| Author | GiriRamadoss (BA) |
| Last Updated | 2026-06-19 |

---

## 2. Business Context

Underwriting a high-risk or complex pet insurance application requires synthesising medical history, breed-linked risk, fraud signals, prior claims patterns, and actuarial tables into a single, auditable policy decision. This analysis is too multi-dimensional for a single-pass LLM prompt — a specialist agent for each domain produces more accurate, explainable, and consistent results.

UC-06 is the **terminal stage** of the LifeGroup AI pipeline. It consumes the structured outputs of UC-01 through UC-05, orchestrates a panel of autonomous specialist agents running in parallel, and emits a final underwriting decision: premium tier, exclusions list, benefit caps, and a plain-English rationale for each factor.

**Pipeline position:**

```
UC-05 (History) → UC-04 (Breed/Fraud) → UC-01 (Invoice) → UC-03 (Coding) → UC-02 (Adjudication) → UC-06 (Underwriting) → Policy Issuance
```

All upstream AI outputs feed UC-06 as structured JSON. No raw PDFs are processed at this stage.

---

## 3. Use Case Summary

| Attribute | Detail |
|---|---|
| Trigger | New or renewal policy application with upstream AI outputs available |
| Actor | SaaS platform (automated) or human underwriter initiating override review |
| Precondition | UC-01 through UC-05 outputs present in case bundle; application form submitted |
| Primary Flow | Orchestrator agent dispatches specialist agents → agents run in parallel → results aggregated → underwriting decision drafted → compliance validated → decision delivered |
| Alternate Flow | Any agent returns LOW_CONFIDENCE or NEEDS_REVIEW → escalate to human underwriter |
| Postcondition | Signed underwriting decision JSON + human-readable PDF report stored; policy creation triggered or application declined |
| Priority | High |

---

## 4. Agent Architecture

### 4.1 Orchestrator Agent

**Model:** Llama 3.3 70B (Q4_K_M, 128K context)
**Role:** Coordinates the full multi-agent workflow. Dispatches Phase-1 agents with structured input payloads, waits for Phase-1 completion, triggers Phase-2 and Phase-3 in sequence, validates inter-agent consistency, and assembles the final underwriting package.

The orchestrator does **not** make domain judgements — it routes, assembles, and detects agent disagreements.

### 4.2 Vet Tech Agent

**Model:** Qwen 3 32B (Q4_K_M, 32K context)
**Role:** Clinical risk assessment. Reads the UC-05 pre-existing condition list, UC-03 SNOMED/ICD codes, and UC-01 invoice history to evaluate:
- Severity and trajectory of each confirmed condition
- Likelihood of near-term claims (12-month horizon)
- Species-specific and breed-specific clinical considerations
- Treatment cost benchmarks per condition

**Output schema:** `clinical_risk_assessment` object (see §10)

### 4.3 Fraud Risk Agent

**Model:** Qwen 3 32B (Q4_K_M)
**Role:** Synthesises UC-04 breed verification results, duplicate image signals, declared vs predicted breed mismatch, and policy history patterns to produce a fraud risk rating:

| Rating | Description |
|---|---|
| CLEAR | No fraud signals |
| LOW | 1 minor signal (e.g., breed mismatch below tier boundary) |
| MODERATE | 2+ signals or 1 high-severity signal |
| HIGH | Confirmed fraud indicator (stock photo, duplicate policy, identity mismatch) |

**Output schema:** `fraud_risk_assessment` object

### 4.4 Actuarial Agent

**Model:** Qwen 3 32B (Q4_K_M)
**Role:** Applies LifeGroup actuarial tables to compute:
- Base premium for species, breed tier, age, and postcode risk band
- Premium loading multipliers for each identified risk factor
- Expected loss ratio for the 12-month policy period
- Recommended deductible and reimbursement rate adjustments

**Input:** Vet Tech Agent output + Fraud Risk Agent output + application metadata
**Output schema:** `actuarial_assessment` object

### 4.5 Underwriter Agent

**Model:** Qwen 3 32B (Q4_K_M)
**Role:** Applies underwriting rules (§8) to the actuarial and clinical assessments to produce:
- Policy recommendation (APPROVE_STANDARD / APPROVE_WITH_ADJUSTMENTS / REFER / DECLINE)
- Exclusions list with rule citations
- Benefit cap modifications
- Plain-English rationale for each decision element (required for regulatory disclosure)

**Output schema:** `underwriting_decision` object

### 4.6 Compliance Agent

**Model:** Qwen 3 32B (Q4_K_M)
**Role:** Validates the Underwriter Agent output against regulatory requirements:
- Disclosure completeness (each exclusion has a human-readable reason)
- Adverse action notice requirements (for DECLINE or REFER outcomes)
- GDPR/CCPA data usage assertions (confirms PHI not retained beyond transaction scope)
- Anti-discrimination checks (confirms breed exclusions are actuarially justified, not arbitrary)

Returns COMPLIANT / NON_COMPLIANT with specific failure items if applicable.

### 4.7 Execution Phases

```
Phase 1 (Parallel):  Vet Tech Agent + Fraud Risk Agent
Phase 2 (Sequential): Actuarial Agent (consumes Phase 1 outputs)
Phase 3 (Sequential): Underwriter Agent (consumes Phase 2 output)
Phase 4 (Sequential): Compliance Agent (validates Phase 3 output)
Phase 5 (Orchestrator): Assemble final package; trigger policy issuance or escalation
```

Phase 1 agents share no state and can run concurrently, reducing total latency by ~40%.

---

## 5. AI Task

Given a complete case bundle (structured JSON from UC-01 through UC-05 + application form), the multi-agent system shall:

1. Assess clinical risk from confirmed diagnoses, chronic conditions, and treatment cost history
2. Assess fraud risk from breed verification and image analysis results
3. Compute a risk-adjusted premium and expected loss ratio
4. Apply underwriting rules to produce an exclusion list and recommendation
5. Validate the decision for regulatory compliance
6. Emit a structured underwriting decision with full audit trail (each claim in the decision citing the upstream source and the rule applied)

---

## 6. Recommended Models

| Agent | Model | Quantisation | Context | Temperature |
|---|---|---|---|---|
| Orchestrator | Llama 3.3 70B | Q4_K_M | 128K | 0.0 |
| Vet Tech | Qwen 3 32B | Q4_K_M | 32K | 0.0 |
| Fraud Risk | Qwen 3 32B | Q4_K_M | 32K | 0.0 |
| Actuarial | Qwen 3 32B | Q4_K_M | 32K | 0.0 |
| Underwriter | Qwen 3 32B | Q4_K_M | 32K | 0.0 |
| Compliance | Qwen 3 32B | Q4_K_M | 16K | 0.0 |

**Rationale — why two model families:**
- Llama 3.3 70B is selected for orchestration due to its superior multi-step reasoning and ability to detect inter-agent inconsistencies across a large context window (all 5 agent outputs may exceed 16K tokens combined).
- Qwen 3 32B is selected for specialist agents for its strong performance on structured JSON generation, rule-following, and financial reasoning at a lower memory footprint (fits on a single 24 GB VRAM card per agent with Q4_K_M).

**Temperature 0.0** is mandatory for all agents — underwriting decisions are financial/legal instruments that must be fully deterministic.

**JSON schema enforcement:** GBNF grammar-constrained decoding (llama.cpp) or Pydantic v2 validation for all agent outputs. Any agent output that fails schema validation triggers an automatic retry (max 3 attempts) before the orchestrator flags the case for human review.

---

## 7. Data Inputs (Upstream UC Outputs)

| Field | Source UC | Description |
|---|---|---|
| `invoice_data` | UC-01 | Parsed line items, costs, clinic details from invoices |
| `adjudication_history` | UC-02 | Prior claims decisions, denial codes, paid amounts |
| `medical_codes` | UC-03 | SNOMED-CT + ICD-10-CM codes with confidence tiers |
| `breed_verification` | UC-04 | Breed prediction, risk tier, fraud signals, CLIP similarity scores |
| `history_review` | UC-05 | Pre-existing condition list, chronic patterns, record gaps, PE/CD rule hits |
| `application_form` | Policy Admin | Species, declared breed, age, postcode, owner details, policy type requested |

All inputs are validated at ingestion. Missing UC outputs → case flagged `INCOMPLETE_INPUTS` and held pending.

---

## 8. Underwriting Rules

| Rule ID | Name | Condition | Action |
|---|---|---|---|
| UR-01 | High-Risk Breed | `breed_tier IN (4, 5)` | Premium loading ≥ 15%; reduce annual benefit limit by 10% |
| UR-02 | Pre-existing Confirmed | `pre_existing.classification = PRE_EXISTING_CONFIRMED (PE-01)` | Exclude condition from coverage; cite PE-01 in exclusion reason |
| UR-03 | Pre-existing Probable | `pre_existing.classification = PRE_EXISTING_PROBABLE (PE-02)` | Flag for underwriter review; do not auto-issue |
| UR-04 | Fraud Signal HIGH | `fraud_risk_rating = HIGH` | Decline application; escalate to fraud investigation team |
| UR-05 | Fraud Signal MODERATE | `fraud_risk_rating = MODERATE` | Refer to chief underwriter; hold policy issuance |
| UR-06 | Chronic Condition | `is_chronic = true (CD-01)` | Cap annual benefit for that condition at $3,000 |
| UR-07 | Record Gap | `record_gap_months > 18 (CD-07)` | Require submission of records covering gap or apply broad exclusion |
| UR-08 | Cancer History | `condition_name CONTAINS oncology_terms` | Refer to chief underwriter regardless of remission status |
| UR-09 | Multiple Pre-existing | `count(pre_existing_confirmed) >= 3` | Chief underwriter sign-off required |
| UR-10 | NEEDS_REVIEW from UC-05 | `history_review.overall_verdict = NEEDS_REVIEW` | Block auto-issuance; resolve UC-05 ambiguity first |
| UR-11 | Senior Pet | `age_years > 10 (feline) OR (age_years > 8 AND breed_weight_class = LARGE)` | Senior premium loading 20%; no routine/wellness coverage |
| UR-12 | Standard Baseline | No triggering conditions | APPROVE_STANDARD; apply standard deductible $250, 80% reimb., $15K annual limit |

Premium loading factors apply **multiplicatively** (not additively). Each exclusion reduces the annual benefit limit by $2,000 (maximum 3 exclusions reduce limit to $9,000).

---

## 9. Prompt Strategy

### 9.1 Orchestrator System Prompt (Llama 3.3 70B)

```
You are an insurance underwriting orchestration engine for LifeGroup Pet Insurance.
You receive structured JSON outputs from five upstream AI analysis modules and coordinate
a panel of specialist agents to produce an auditable underwriting decision.

You must:
- Dispatch specialist agents with exact input payloads (no summarisation)
- Detect and flag any inter-agent inconsistency (e.g., Vet Tech says condition is minor
  but Actuarial flags high expected loss)
- Never make domain judgements yourself — route, assemble, and flag only
- Output a valid underwriting_package JSON conforming to the schema provided

Temperature: 0.0. Schema enforcement: GBNF grammar.
```

### 9.2 Vet Tech Agent Prompt Template

```
You are a veterinary technician specialist for a pet insurance company.
Review the medical record summary below and assess clinical risk.
Species: {species}. Breed: {declared_breed}. Age: {age_years} years.

Medical inputs:
- Pre-existing conditions: {history_review.pre_existing_conditions}
- SNOMED/ICD codes: {medical_codes.codes}
- Invoice history summary: {invoice_data.summary}

For each pre-existing condition, assess:
1. Severity (MILD / MODERATE / SEVERE)
2. Treatment cost trajectory (STABLE / INCREASING / UNCERTAIN)
3. 12-month claim likelihood (LOW / MEDIUM / HIGH)
4. Any species/breed-specific clinical considerations

Return JSON conforming to the clinical_risk_assessment schema.
Temperature: 0.0.
```

### 9.3 Underwriter Agent Prompt Template

```
You are a senior underwriter for LifeGroup Pet Insurance.
Apply the underwriting rules listed below to the actuarial and clinical assessments
and produce a final underwriting decision.

Rules: [UR-01 through UR-12 — full text provided]
Actuarial assessment: {actuarial_assessment}
Clinical assessment: {clinical_risk_assessment}
Fraud assessment: {fraud_risk_assessment}

For each rule that triggers, cite: rule_id, condition_met, action_taken, source_data.
For each exclusion, write a plain-English disclosure sentence for the applicant.
Return JSON conforming to the underwriting_decision schema.
Temperature: 0.0.
```

---

## 10. Output Schema

```json
{
  "underwriting_package": {
    "case_id": "string",
    "application_id": "string",
    "policy_holder": "string",
    "pet_name": "string",
    "species": "canine | feline",
    "breed": "string",
    "age_years": "number",
    "processing_timestamp_utc": "ISO-8601",
    "agent_execution_log": [
      {
        "agent_name": "string",
        "model": "string",
        "input_token_count": "integer",
        "output_token_count": "integer",
        "schema_validation_status": "PASS | RETRY | FAIL",
        "retry_count": "integer"
      }
    ],
    "clinical_risk_assessment": {
      "overall_clinical_risk": "LOW | MEDIUM | HIGH | VERY_HIGH",
      "conditions": [
        {
          "condition_name": "string",
          "icd_code": "string",
          "severity": "MILD | MODERATE | SEVERE",
          "cost_trajectory": "STABLE | INCREASING | UNCERTAIN",
          "claim_likelihood_12m": "LOW | MEDIUM | HIGH",
          "notes": "string"
        }
      ]
    },
    "fraud_risk_assessment": {
      "fraud_risk_rating": "CLEAR | LOW | MODERATE | HIGH",
      "signals": ["string"],
      "clip_similarity_max": "number | null",
      "breed_mismatch_delta_tiers": "integer | null"
    },
    "actuarial_assessment": {
      "base_premium_annual_usd": "number",
      "loading_factors": [
        {
          "factor_name": "string",
          "multiplier": "number",
          "rule_id": "string"
        }
      ],
      "adjusted_premium_annual_usd": "number",
      "expected_loss_ratio_12m": "number",
      "recommended_deductible_usd": "number",
      "recommended_reimbursement_pct": "number"
    },
    "underwriting_decision": {
      "recommendation": "APPROVE_STANDARD | APPROVE_WITH_ADJUSTMENTS | REFER_TO_CHIEF_UNDERWRITER | DECLINE",
      "rules_triggered": [
        {
          "rule_id": "string",
          "condition_met": "string",
          "action_taken": "string",
          "source_data_reference": "string"
        }
      ],
      "exclusions": [
        {
          "condition": "string",
          "effective_from": "ISO-8601 date",
          "disclosure_text": "string"
        }
      ],
      "annual_benefit_limit_usd": "number",
      "deductible_usd": "number",
      "reimbursement_pct": "number",
      "rationale_summary": "string"
    },
    "compliance_validation": {
      "status": "COMPLIANT | NON_COMPLIANT",
      "failures": ["string"],
      "adverse_action_notice_required": "boolean"
    },
    "overall_verdict": "APPROVED | APPROVED_WITH_ADJUSTMENTS | REFERRED | DECLINED"
  }
}
```

---

## 11. Pipeline Position

```
UC-05 longitudinal history review    ──┐
UC-04 breed/fraud verification        ──┤
UC-03 medical coding                  ──┼──► UC-06 Multi-Agent Risk Underwriting ──► Policy Issuance
UC-02 claims adjudication history     ──┤
UC-01 invoice parsing history         ──┘
```

UC-06 consumes **all** upstream outputs. It must not be invoked until UC-04 and UC-05 complete — these two contain the most decision-critical signals (pre-existing conditions and fraud).

---

## 12. Constraints & Safety

- **Deterministic outputs:** Temperature 0.0 + schema enforcement across all agents. Same inputs must produce byte-identical JSON decisions (modulo timestamp).
- **PHI retention:** All PHI purged from agent context immediately after the transaction; only anonymised risk scores and rule hit lists retained for audit.
- **Human escalation:** Any agent returning NEEDS_REVIEW, LOW_CONFIDENCE, or schema validation failure after 3 retries must escalate to human underwriter — no auto-issuance permitted.
- **Audit trail:** Every field in `underwriting_decision` must cite a `source_data_reference` traceable to an upstream UC output or application form field.
- **No hallucination of conditions:** Vet Tech Agent may not infer conditions not present in upstream UC outputs. It must cite specific UC-03 or UC-05 data for each clinical finding.
- **Inter-agent consistency check:** Orchestrator validates that `actuarial_assessment.expected_loss_ratio_12m` is directionally consistent with `clinical_risk_assessment.overall_clinical_risk`. Inconsistency threshold: ELR > 0.85 with clinical risk = LOW → flag for review.
- **Local inference only:** No cloud API calls. All models run on-premises via Ollama/llama.cpp. No case data leaves the LifeGroup network boundary.

---

## 13. Acceptance Criteria

| ID | Criterion | Validation Method |
|---|---|---|
| AC-01 | Orchestrator dispatches all 5 specialist agents and assembles a valid `underwriting_package` JSON for a standard-risk case in < 8 minutes | Automated integration test |
| AC-02 | For a case with 2 confirmed pre-existing conditions, UR-02 triggers for each and corresponding exclusions appear in `underwriting_decision.exclusions` | Test bundle 02/03 (pre-existing) |
| AC-03 | For a case with `fraud_risk_rating = HIGH`, the recommendation is DECLINE and escalation flag is set | Test bundle with HIGH fraud input |
| AC-04 | For a case with `history_review.overall_verdict = NEEDS_REVIEW`, auto-issuance is blocked (UR-10) | Test bundle 04 (hip dysplasia) |
| AC-05 | Premium loading factors apply multiplicatively; adjusted premium = base × product(multipliers) ± 1% (floating-point tolerance) | Actuarial unit test |
| AC-06 | Every exclusion contains a non-empty `disclosure_text` in plain English ≤ 150 words | Compliance Agent validation |
| AC-07 | Compliance Agent returns COMPLIANT for all standard-path test bundles | Automated compliance test |
| AC-08 | Any agent schema validation failure after 3 retries routes the case to `REFER_TO_CHIEF_UNDERWRITER`, not DECLINE | Fault injection test |
| AC-09 | Audit trail: 100% of `underwriting_decision.rules_triggered` entries cite a valid `source_data_reference` from upstream UC output | JSON schema check |
| AC-10 | PHI not present in any agent output JSON beyond the `policy_holder` and `pet_name` fields | PHI scanning test |

---

## 14. Non-Functional Requirements

### 14.1 Performance

| Metric | Target |
|---|---|
| End-to-end latency (Phase 1 parallel + sequential phases) | ≤ 8 minutes (standard case) |
| Phase 1 parallel wall-clock (Vet Tech + Fraud Risk) | ≤ 3 minutes |
| Phase 2–4 sequential | ≤ 5 minutes combined |
| API async polling interval | 15 seconds recommended |
| Throughput | ≥ 50 underwriting decisions per hour per server node |

Phase 1 parallelism requires two Qwen 3 32B instances running concurrently. Minimum hardware: 2× GPU cards with 24 GB VRAM each, or 1× 80 GB VRAM card.

### 14.2 Accuracy & Consistency

| Metric | Target |
|---|---|
| Inter-run consistency (same inputs) | 100% identical decisions (deterministic) |
| Rule application accuracy | ≥ 99% (all applicable UR rules triggered, no spurious triggers) |
| Exclusion disclosure readability (Flesch-Kincaid grade ≤ 12) | ≥ 98% of disclosures |
| Audit trail completeness | 100% — every rule trigger cites source |
| Hallucination rate (conditions not in upstream input) | 0% — zero tolerance |

### 14.3 Reliability

- Automatic retry on agent schema validation failure: up to 3 attempts per agent
- Partial failure recovery: if any single non-critical agent fails after retries, the orchestrator may continue with NEEDS_REVIEW flag rather than dropping the entire case
- Idempotency: same `application_id` must not produce a second underwriting decision; return existing decision with `409 DUPLICATE_APPLICATION`

### 14.4 Security & Privacy

- Bearer token auth; scope `underwriting:write` required
- All data encrypted in transit (TLS 1.3) and at rest (AES-256)
- PHI purged from all agent contexts on transaction close
- GDPR Article 22 compliance: automated individual decision-making — applicant has right to request human review of any automated underwriting decision
- Role-based access: only licensed underwriters may invoke override/refer endpoints

### 14.5 Observability

- Structured logs per agent: `{case_id, agent_name, phase, input_hash, output_hash, latency_ms, retry_count}`
- Metrics: agent latency (p50/p95/p99), schema failure rate, escalation rate, decline rate
- Alert thresholds: escalation rate > 10% or schema failure rate > 2% → PagerDuty

### 14.6 Scalability

- Stateless orchestrator; horizontal scaling via queue-based dispatch
- Each agent instance processes one case at a time; scale by adding model replicas

---

## 15. API Specification

### 15.1 Submit Underwriting Request

**Endpoint:** `POST /api/v1/underwriting/policies`
**Auth:** Bearer token, scope `underwriting:write`
**Content-Type:** `application/json`

**Request body:**
```json
{
  "application_id": "string (required, unique per application)",
  "case_bundle": {
    "invoice_data":         { "...": "UC-01 output" },
    "adjudication_history": { "...": "UC-02 output" },
    "medical_codes":        { "...": "UC-03 output" },
    "breed_verification":   { "...": "UC-04 output" },
    "history_review":       { "...": "UC-05 output" }
  },
  "application_form": {
    "policy_holder_name": "string",
    "pet_name": "string",
    "species": "canine | feline",
    "declared_breed": "string",
    "date_of_birth": "YYYY-MM-DD",
    "postcode": "string",
    "policy_type": "ACCIDENT_ILLNESS | ACCIDENT_ONLY | WELLNESS_PLUS"
  },
  "priority": "STANDARD | EXPEDITED",
  "callback_url": "string (optional)"
}
```

**Response `202 Accepted`:**
```json
{
  "job_id": "uwj_a1b2c3d4e5f6",
  "status": "QUEUED",
  "poll_url": "/api/v1/underwriting/policies/uwj_a1b2c3d4e5f6",
  "estimated_completion_seconds": 480,
  "created_at": "ISO-8601"
}
```

---

### 15.2 Poll Job Status

**Endpoint:** `GET /api/v1/underwriting/policies/{job_id}`
**Auth:** Bearer token, scope `underwriting:read`

**Response `200 OK` (in progress):**
```json
{
  "job_id": "uwj_a1b2c3d4e5f6",
  "status": "PROCESSING",
  "phase": "PHASE_2_ACTUARIAL",
  "phases_complete": ["PHASE_1_VET_TECH", "PHASE_1_FRAUD_RISK"],
  "elapsed_seconds": 185
}
```

**Response `200 OK` (complete):**
```json
{
  "job_id": "uwj_a1b2c3d4e5f6",
  "status": "COMPLETE",
  "underwriting_package": { "...": "full schema from §10" }
}
```

---

### 15.3 SSE Progress Stream

**Endpoint:** `GET /api/v1/underwriting/policies/{job_id}/stream`

Events: `phase_started`, `agent_complete`, `inter_agent_check`, `decision_ready`, `compliance_complete`, `job_complete`

---

### 15.4 Human Override

**Endpoint:** `POST /api/v1/underwriting/policies/{job_id}/override`
**Auth:** Bearer token, scope `underwriting:override` (licensed underwriter role)

Allows a human underwriter to change recommendation, add/remove exclusions, and mark the decision as human-reviewed. Required when `overall_verdict = REFERRED`.

---

### 15.5 Error Codes

| HTTP | Code | Description |
|---|---|---|
| 400 | INVALID_REQUEST | Malformed JSON or missing required field |
| 400 | MISSING_UC_OUTPUT | One or more required upstream UC outputs absent |
| 400 | INCOMPLETE_HISTORY | UC-05 status is not COMPLETE |
| 401 | UNAUTHORIZED | Missing or invalid Bearer token |
| 403 | FORBIDDEN | Token lacks `underwriting:write` scope |
| 409 | DUPLICATE_APPLICATION | `application_id` already processed — returns existing decision |
| 422 | AGENT_SCHEMA_FAILURE | Agent failed schema validation after 3 retries — case routed to REFER |
| 429 | RATE_LIMIT_EXCEEDED | See X-RateLimit-* headers |
| 500 | ORCHESTRATION_ERROR | Internal agent coordination failure |
| 503 | MODEL_UNAVAILABLE | One or more required models not loaded |

### 15.6 Rate Limits

| Tier | Requests/hour | Concurrent jobs |
|---|---|---|
| Starter | 10 | 1 |
| Growth | 50 | 5 |
| Enterprise | 500 | 25 |

Headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`

---

## 16. Out of Scope

- Actual premium payment processing or policy document generation (handled by downstream Policy Admin System)
- Human underwriter workflow UI (this UC exposes an API; the UI is a separate deliverable)
- Reinsurance treaty evaluation
- Claims processing post-policy-issuance (handled by UC-02)
- Real-time quote generation (this UC is for full underwriting; quoting uses a simplified rule engine)
- Multi-pet policy bundling
- Group / employer pet benefit programmes

---

## 17. Open Questions

| ID | Question | Owner | Priority |
|---|---|---|---|
| OQ-01 | What actuarial tables does LifeGroup use for base premium computation? Are these available as structured data for the Actuarial Agent, or must they be embedded in the prompt? | Actuarial team | High |
| OQ-02 | GDPR Article 22: does the platform need to surface an explicit "request human review" button at point of sale, or is a post-decision appeal process sufficient? | Legal / Compliance | High |
| OQ-03 | For `REFER_TO_CHIEF_UNDERWRITER` cases, what is the SLA for human resolution? Does the API hold the job open or emit a REFERRED terminal status? | Operations | Medium |
| OQ-04 | Inter-agent consistency threshold: is ELR > 0.85 with clinical_risk = LOW the right trigger? Needs actuarial calibration. | Actuarial / AI team | Medium |
| OQ-05 | Cancer history rule (UR-08): does this apply only to active cancer or also to confirmed remission cases? Current draft defers all to chief underwriter. | Chief Underwriter | High |
| OQ-06 | Should the Compliance Agent validate against a specific state/country regulatory ruleset, or a generic insurance disclosure framework? | Compliance / Legal | Medium |
| OQ-07 | Schema versioning: if upstream UC output schemas change (e.g., UC-05 adds a new field), what is the compatibility contract? | Platform / Architecture | Low |
