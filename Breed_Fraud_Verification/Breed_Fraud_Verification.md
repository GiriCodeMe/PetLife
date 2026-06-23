# Use Case: Breed & Fraud Verification

_Generated: 2026-06-19_

---

## 1. Overview

Analyse pet photographs submitted at enrolment and with each claim to: (a) verify the declared breed matches the visual evidence, (b) classify the pet into the correct insurance risk tier, and (c) detect cross-claim image reuse and other photo-based fraud signals. Findings gate whether a policy is issued at the quoted premium and whether a claim proceeds to adjudication.

This UC operates at two pipeline touchpoints:

| Touchpoint | Trigger | Action |
|---|---|---|
| **Enrolment** | New policy application submitted with pet photo | Verify declared breed; assign risk tier; store image embedding |
| **Claim submission** | Claim filed with updated or re-submitted pet photo | Re-verify breed consistency; run cross-claim duplicate check |

---

## 2. Business Context

| Attribute | Detail |
|---|---|
| Domain | Pet (dog & cat) health insurance / LifeGroup underwriting & fraud prevention |
| Stakeholder Role | Business Analyst |
| Trigger | Photo uploaded during enrolment OR photo attached to claim submission |
| Outcome | Breed verification verdict + fraud risk score + recommended action |
| Downstream Consumers | Underwriting engine (premium adjustment), claims adjudication (UC-02), fraud investigation queue |
| Volume Expectation | Medium-high — every new policy + every claim with photo |
| Data Sensitivity | Biometric-adjacent (pet image); PII (owner identity indirectly linked); store minimally |

---

## 3. AI Task Definition

**Task type:** Multi-modal vision analysis + similarity search + structured decision output

### Sub-task A — Breed Identification

The vision model analyses the submitted pet photo and returns:
- Top-3 probable breeds with confidence scores
- Visual physical characteristics supporting the prediction (coat type, body structure, facial features, size estimate)
- Mapped insurance risk tier based on predicted breed

### Sub-task B — Breed Consistency Check

Compare the model's predicted breed against the policyholder's declared breed:
- Determine if predicted breed matches declared breed within acceptable tolerance
- Flag if predicted breed belongs to a higher risk tier than declared
- Flag if predicted breed is a restricted or excluded breed under the policy

### Sub-task C — Cross-Claim Image Fraud Detection

Compare the submitted image embedding against the LifeGroup image database:
- Near-duplicate detection: same pet enrolled under multiple policies or member accounts
- Stock photo detection: embedding similarity to known stock image fingerprints
- Post-mortem fraud: pet previously recorded as deceased on another claim

**Processing mode:** Local vision inference + vector similarity search. No pet photos transmitted to external services.

---

## 4. Recommended Models

| Model | Size | Quant | VRAM / RAM | Strength |
|---|---|---|---|---|
| Llama 3.2 Vision 11B Instruct | 11B | Q4_K_M | ~9 GB RAM | Strong breed identification; good structured output from images |
| Llama 3.2 Vision 11B Instruct | 11B | Q8_0 | ~13 GB RAM | Higher precision; preferred for ambiguous mixed-breed cases |
| Moondream2 | 1.8B | Full precision | ~4 GB RAM | Lightweight; fast on CPU; lower accuracy on rare breeds |
| LLaVA 1.6 Mistral 7B | 7B | Q4_K_M | ~6 GB RAM | Alternative if Llama 3.2 Vision unavailable |

**Recommendation:** `Llama 3.2 Vision 11B Q4_K_M` as primary; `Moondream2` as lightweight fallback for high-throughput enrolment screening.

**Embedding model for duplicate detection:** `CLIP ViT-L/14` (local) — generates image embeddings stored in a vector DB (pgvector or Chroma) for cosine similarity search.

**Temperature:** 0.0 for breed identification (deterministic); not applicable for embedding search.

---

## 5. Breed Risk Classification

### 5a. Risk Tiers

| Tier | Label | Description | Premium Modifier |
|---|---|---|---|
| 1 | Standard | Common mixed breeds, low genetic health risk | Baseline |
| 2 | Elevated | Breeds with moderate heritable conditions | +15–25% |
| 3 | High | Breeds with significant known health risks | +30–50% |
| 4 | Restricted | Breeds requiring underwriter approval | Manual review |
| 5 | Excluded | Breeds not covered under standard policies | Policy declined |

### 5b. Sample Breed-to-Tier Mapping (Dogs)

| Breed | Risk Tier | Primary Risk Factors |
|---|---|---|
| Labrador Retriever | 2 | Hip/elbow dysplasia, obesity |
| Golden Retriever | 2 | Cancer predisposition, hip dysplasia |
| Domestic Mixed Breed | 1 | Hybrid vigour; lower genetic disease burden |
| French Bulldog | 4 | Brachycephalic obstructive airway syndrome (BOAS), spinal issues |
| English Bulldog | 4 | BOAS, skin fold dermatitis, cardiac issues |
| Dachshund | 3 | Intervertebral disc disease (IVDD) |
| German Shepherd | 3 | Degenerative myelopathy, hip dysplasia |
| Cavalier King Charles Spaniel | 3 | Mitral valve disease, syringomyelia |
| Great Dane | 3 | Dilated cardiomyopathy, bloat (GDV), short lifespan |
| American Pit Bull Terrier | 5 | Excluded — actuarial and regulatory reasons |
| Wolf-Dog Hybrid | 5 | Excluded — regulatory |

### 5c. Sample Breed-to-Tier Mapping (Cats)

| Breed | Risk Tier | Primary Risk Factors |
|---|---|---|
| Domestic Shorthair / Longhair | 1 | Generally robust |
| Siamese | 2 | Amyloidosis, dental issues |
| Ragdoll | 2 | Hypertrophic cardiomyopathy (HCM) |
| Persian | 3 | Polycystic kidney disease (PKD), brachycephalic |
| Scottish Fold | 4 | Osteochondrodysplasia — severe joint deformity |
| Bengal | 2 | HCM, progressive retinal atrophy |
| Maine Coon | 3 | HCM, spinal muscular atrophy |

---

## 6. Fraud Detection Rules

| Rule ID | Fraud Type | Signal | Action |
|---|---|---|---|
| FD-01 | Breed downgrade | Predicted breed tier > declared breed tier | FLAG — potential premium evasion |
| FD-02 | Excluded breed declared as other | Predicted breed is Tier 5; declared as mixed or lower-tier | FLAG — policy should not have been issued |
| FD-03 | Cross-policy duplicate | Image cosine similarity ≥ 0.94 against another active policy | FRAUD_FLAG — same pet on multiple policies |
| FD-04 | Cross-policy deceased | Embedding matches pet recorded as deceased on a prior claim | FRAUD_FLAG — post-mortem fraud |
| FD-05 | Stock / internet photo | Embedding similarity ≥ 0.92 against known stock image fingerprint DB | FRAUD_FLAG — photo not of a real enrolled pet |
| FD-06 | Metadata inconsistency | EXIF data absent; image dimensions atypical of phone camera; no GPS | RISK_SIGNAL — warrants human review |
| FD-07 | Image reuse same account | Same image hash submitted on a new claim for a different pet | FLAG — potential claim stacking |
| FD-08 | Species mismatch | Declared species ≠ model-predicted species | FLAG — fundamental data integrity failure |
| FD-09 | Low-resolution evasion | Image below 200×200 px — insufficient for reliable breed ID | REJECT — request higher-resolution photo |
| FD-10 | Breed confidence too low | Top-1 breed confidence < 0.50 on non-mixed-breed declaration | NEEDS_REVIEW — ambiguous visual evidence |

---

## 7. Input Schema

### 7a. Verification Request

| Field | Type | Required | Description |
|---|---|---|---|
| `verification_id` | string (UUID) | Yes | Unique request identifier |
| `context` | string (enum) | Yes | `ENROLMENT` / `CLAIM` / `RENEWAL` |
| `policy_id` | string | No | Existing policy ID (required for `CLAIM` and `RENEWAL`) |
| `member_id` | string | Yes | Policyholder identifier |
| `declared_species` | string | Yes | `Canine` / `Feline` |
| `declared_breed` | string | Yes | Breed as declared by policyholder |
| `declared_breed_tier` | integer (1–5) | Yes | Risk tier assigned at enrolment |
| `pet_name` | string | Yes | Pet name for audit trail |
| `pet_age_years` | number | No | Age — assists size/proportion validation |
| `image` | binary (JPEG/PNG/WEBP) | Yes | Pet photograph. Min 400×400 px. Max 10 MB. |
| `image_source` | string (enum) | No | `MEMBER_UPLOAD` / `CLINIC_UPLOAD` / `THIRD_PARTY` |
| `submission_date` | date (ISO 8601) | Yes | Date of submission |

### 7b. Image Quality Requirements

| Requirement | Minimum Standard |
|---|---|
| Resolution | 400×400 px minimum; 800×800 px recommended |
| Subject visibility | Pet must occupy ≥ 30% of the frame |
| Lighting | No extreme under/overexposure |
| Format | JPEG, PNG, or WEBP |
| File size | 50 KB minimum (rejects heavily compressed images); 10 MB maximum |
| Multiple pets | Single pet per image required |

---

## 8. Output Schema

### 8a. Verification Response

| Field | Type | Description |
|---|---|---|
| `verification_id` | string | Echo of input ID |
| `verified_date` | date (ISO 8601) | Date verification was performed |
| `overall_verdict` | string | `VERIFIED` / `BREED_MISMATCH` / `NEEDS_REVIEW` / `FRAUD_FLAG` / `REJECTED` |
| `recommended_action` | string | `PROCEED` / `REPRICE` / `MANUAL_REVIEW` / `SUSPEND` / `DECLINE` |
| `breed_assessment` | object | See 8b |
| `fraud_signals[]` | array | See 8c |
| `image_quality` | object | See 8d |
| `adjudicator` | string | `AI` / `HUMAN` |
| `notes` | string \| null | Free-text summary of key findings |

### 8b. Breed Assessment Object

| Field | Type | Description |
|---|---|---|
| `predicted_species` | string | Model-predicted species |
| `top_breeds[]` | array | Top-3 breed predictions with `breed_name`, `confidence_score`, `risk_tier` |
| `primary_breed` | string | Highest-confidence prediction |
| `primary_confidence` | number | 0.00–1.00 |
| `predicted_risk_tier` | integer (1–5) | Risk tier of primary predicted breed |
| `declared_vs_predicted` | string | `MATCH` / `PARTIAL_MATCH` / `MISMATCH` / `AMBIGUOUS` |
| `tier_delta` | integer | `predicted_risk_tier - declared_breed_tier` (positive = under-declared risk) |
| `visual_characteristics` | array | Key observed features (e.g. `brachycephalic face`, `double coat`, `drop ears`) |

### 8c. Fraud Signal Object

| Field | Type | Description |
|---|---|---|
| `rule_id` | string | Fraud rule triggered (e.g. `FD-03`) |
| `signal_type` | string | `BREED_DOWNGRADE` / `DUPLICATE_IMAGE` / `DECEASED_PET` / `STOCK_PHOTO` / `METADATA_ANOMALY` / `SPECIES_MISMATCH` / `LOW_CONFIDENCE` |
| `severity` | string | `HIGH` / `MEDIUM` / `LOW` |
| `detail` | string | Human-readable explanation |
| `matched_policy_id` | string \| null | For FD-03/FD-04: the matching policy ID |
| `similarity_score` | number \| null | For image-based rules: cosine similarity score |

### 8d. Image Quality Object

| Field | Type | Description |
|---|---|---|
| `resolution` | string | e.g. `1024x768` |
| `quality_score` | number | 0.00–1.00 composite quality score |
| `has_exif` | boolean | Whether EXIF metadata is present |
| `subject_coverage_pct` | number | Estimated % of frame occupied by the pet |
| `quality_verdict` | string | `ACCEPTABLE` / `BORDERLINE` / `INSUFFICIENT` |

---

## 9. Verdict & Action Matrix

| Overall Verdict | Condition | Recommended Action |
|---|---|---|
| `VERIFIED` | Species match + breed match (MATCH or PARTIAL_MATCH) + no fraud signals | `PROCEED` |
| `BREED_MISMATCH` | Predicted tier = declared tier but different breed (cosmetic mismatch) | `PROCEED` (log for records) |
| `BREED_MISMATCH` | Predicted tier > declared tier (under-declared risk) | `REPRICE` — adjust premium to correct tier |
| `NEEDS_REVIEW` | Confidence < 0.50 OR mixed-breed uncertainty OR single MEDIUM fraud signal | `MANUAL_REVIEW` |
| `FRAUD_FLAG` | Any HIGH-severity fraud signal (FD-03, FD-04, FD-05) | `SUSPEND` — freeze policy/claim; route to fraud team |
| `FRAUD_FLAG` | FD-02 (excluded breed) | `DECLINE` — policy void |
| `REJECTED` | Image quality insufficient (FD-09) | Request resubmission with better photo |

---

## 10. Prompt Strategy

**Approach:** Structured vision prompt with explicit breed taxonomy and output schema

**Breed Identification Prompt:**
```
You are a veterinary breed identification specialist. Analyse the attached pet photograph.

Declared species: {declared_species}
Declared breed: {declared_breed}

Tasks:
1. Identify the species visible in the image.
2. Identify the top 3 most probable breeds. For each breed provide:
   - breed_name (use AKC/GCCF standard names)
   - confidence_score (0.00–1.00)
   - visual_evidence: list of 3–5 physical characteristics supporting this prediction
3. Determine if the primary predicted breed matches the declared breed.
4. Note any physical characteristics consistent with excluded or restricted breeds
   (brachycephalic features, pit bull-type morphology, wolf-like features).

Return ONLY valid JSON matching this schema: {output_schema}.
If the image quality is insufficient for reliable breed identification,
set primary_confidence to 0.0 and quality_verdict to INSUFFICIENT.
Do not guess breed from background, collar tags, or owner-provided context clues.
Analyse visual characteristics only.
```

**Temperature:** 0.0 | **Image resolution:** Resize to 1024px max side before inference (performance vs. accuracy balance)

---

## 11. Pipeline Architecture

```
Image Upload (JPEG/PNG/WEBP)
        │
        ▼
Image Pre-processing ─────── Validate format, resolution, file size
        │                      Resize to 1024px max side
        │                      EXIF metadata extraction
        ▼
Image Quality Check ─────── Blur detection (Laplacian variance)
        │                    Subject coverage estimation
        │                    FD-09: reject if < 400x400 px
        ▼
CLIP Embedding Generation ── Local CLIP ViT-L/14 inference
        │                     512-dim vector embedding
        ▼
Duplicate / Fraud Search ─── pgvector cosine similarity search
        │                     Against: active_policies, deceased_pets, stock_photo_db
        │                     FD-03, FD-04, FD-05 rules evaluated here
        ▼
Vision LLM Inference ──────── Llama 3.2 Vision 11B (local Ollama)
        │                       Breed identification + visual characteristics
        │                       Temperature: 0.0, grammar-constrained JSON
        ▼
Breed Consistency Check ───── Compare predicted breed → declared breed
        │                      Map to risk tier via breed_risk_config.yaml
        │                      FD-01, FD-02, FD-08, FD-10 evaluated here
        ▼
Verdict Assembly ──────────── Aggregate breed + fraud signals → overall_verdict
        │                      Apply verdict/action matrix (Section 9)
        ▼
Embedding Storage ─────────── Store CLIP embedding in pgvector
        │                       keyed on (member_id, pet_name, submission_date)
        │                       Only on VERIFIED or NEEDS_REVIEW outcomes
        ▼
Structured Response ────────── JSON → underwriting engine / fraud queue / claims UC-02
```

---

## 12. Non-Functional Requirements

### 12a. Performance

| NFR | Target | Notes |
|---|---|---|
| Vision inference latency (p95) | ≤ 8 s per image | Llama 3.2 11B on GPU; CPU target ≤ 20 s |
| CLIP embedding generation | ≤ 1 s per image | CLIP ViT-L/14 is lightweight |
| Vector similarity search | ≤ 200 ms | pgvector with IVFFlat index on 1M embeddings |
| End-to-end API response (p95) | ≤ 12 s | All steps combined, GPU-backed inference |
| Throughput | ≥ 20 concurrent enrolment verifications | Bursty during campaign or open-enrolment periods |

### 12b. Scalability

| NFR | Target | Notes |
|---|---|---|
| Horizontal scaling | Stateless API — scale inference nodes behind load balancer | Vector DB is shared; inference nodes are stateless |
| Vector DB capacity | Supports 10M+ embeddings with sub-200 ms search | pgvector with HNSW index at scale |
| Image storage | Not retained by default — embedding only stored post-verification | Reduces storage cost and PII exposure |
| Model cold start | ≤ 6 s for Llama 3.2 Vision 11B | Pre-loaded at container startup |

### 12c. Accuracy & Quality

| NFR | Target | Notes |
|---|---|---|
| Breed identification accuracy (purebred) | ≥ 88% top-1 accuracy | Validated against 500-image labelled dataset per species |
| Breed identification accuracy (mixed breed) | ≥ 75% top-3 accuracy | Mixed breeds evaluated on top-3 recall |
| Fraud duplicate detection precision | ≥ 99% at 0.94 similarity threshold | Low false-positive tolerance for fraud flags |
| Fraud duplicate detection recall | ≥ 95% | Misses are higher cost than false positives in fraud context |
| Species identification accuracy | ≥ 99.5% | Fundamental — failure here invalidates all downstream checks |
| Excluded breed detection recall | ≥ 97% | Misclassifying a Tier-5 breed as lower tier is a critical failure |

### 12d. Availability & Reliability

| NFR | Target | Notes |
|---|---|---|
| Uptime SLA | 99.5% monthly | |
| Image not retained on failure | Submitted image purged from memory within 30 s if processing fails | No partial image data persisted |
| Graceful degradation | If vision model unavailable, return `NEEDS_REVIEW` with `adjudicator: HUMAN` | Never block enrolment indefinitely |
| Idempotency | Same `verification_id` returns cached result; embedding not stored twice | |

### 12e. Security

| NFR | Requirement |
|---|---|
| Authentication | Bearer token; scope: `verification:write` |
| Transport | HTTPS only — TLS 1.2 minimum |
| Image data | Never logged, never persisted beyond transaction; only CLIP embedding stored |
| Tenant isolation | Embeddings and verification results scoped to tenant; cross-tenant similarity search blocked |
| Audit log | `verification_id`, `tenant_id`, `overall_verdict`, `recommended_action`, `timestamp` only — no image data |
| Embedding storage consent | Policyholder consent for embedding storage captured at enrolment (GDPR/CCPA compliance) |
| Fraud investigation data | Fraud-flagged records retained per legal hold requirements; separate access controls |

### 12f. Observability

| NFR | Requirement |
|---|---|
| Health endpoint | `GET /health` — model load + vector DB connectivity + stock photo DB version |
| Metrics | Prometheus: verdict distribution, fraud flag rate by rule, breed mismatch rate, image rejection rate |
| Tracing | `verification_id` propagated through all pipeline steps |
| Alerting | Alert if fraud flag rate > 5% (unusual spike) or breed mismatch rate > 15% over 1-hour window |

### 12g. Maintainability

| NFR | Requirement |
|---|---|
| Breed risk config | `breed_risk_config.yaml` — editable without deployment; hot-reloaded every 5 minutes |
| Fraud threshold tuning | Similarity thresholds in config — adjustable without code change |
| Model swap | Vision model path in config only |
| Stock photo DB | Updated weekly via scheduled job; no service restart required |

---

## 13. API Specification

### 13a. Endpoint

```
POST /api/v1/verification/breed
```

**Content-Type:** `multipart/form-data`
**Authentication:** `Authorization: Bearer <api_key>` (scope: `verification:write`)

### 13b. Request

| Parameter | Type | Required | Description |
|---|---|---|---|
| `image` | binary | Yes | Pet photo. JPEG/PNG/WEBP. Min 400×400 px. Max 10 MB. |
| `verification_id` | string (UUID) | Yes | Caller-supplied unique ID |
| `context` | string | Yes | `ENROLMENT` / `CLAIM` / `RENEWAL` |
| `member_id` | string | Yes | Policyholder ID |
| `policy_id` | string | No | Required for `CLAIM` and `RENEWAL` contexts |
| `declared_species` | string | Yes | `Canine` / `Feline` |
| `declared_breed` | string | Yes | Breed as declared |
| `declared_breed_tier` | integer | Yes | 1–5 |
| `pet_name` | string | Yes | Pet name |
| `submission_date` | date | Yes | ISO 8601 |

**Example cURL:**
```bash
curl -X POST https://api.lifegroup.io/api/v1/verification/breed \
  -H "Authorization: Bearer $API_KEY" \
  -F "image=@biscuit-enrolment-photo.jpg" \
  -F "verification_id=b1c2d3e4-f5a6-7890-bcde-f12345678901" \
  -F "context=ENROLMENT" \
  -F "member_id=MEM-00412" \
  -F "declared_species=Canine" \
  -F "declared_breed=Golden Retriever" \
  -F "declared_breed_tier=2" \
  -F "pet_name=Biscuit" \
  -F "submission_date=2024-01-15"
```

### 13c. Response — Success (HTTP 200)

```json
{
  "verification_id": "b1c2d3e4-f5a6-7890-bcde-f12345678901",
  "verified_date": "2024-01-15",
  "overall_verdict": "VERIFIED",
  "recommended_action": "PROCEED",
  "adjudicator": "AI",
  "notes": "Declared breed confirmed. No fraud signals detected. Enrolment may proceed.",
  "breed_assessment": {
    "predicted_species": "Canine",
    "primary_breed": "Golden Retriever",
    "primary_confidence": 0.94,
    "predicted_risk_tier": 2,
    "declared_vs_predicted": "MATCH",
    "tier_delta": 0,
    "visual_characteristics": ["golden/cream double coat", "broad skull", "drop ears", "muscular build"],
    "top_breeds": [
      {"breed_name": "Golden Retriever", "confidence_score": 0.94, "risk_tier": 2},
      {"breed_name": "Labrador Retriever", "confidence_score": 0.04, "risk_tier": 2},
      {"breed_name": "Flat-Coated Retriever", "confidence_score": 0.02, "risk_tier": 2}
    ]
  },
  "fraud_signals": [],
  "image_quality": {
    "resolution": "2048x1536",
    "quality_score": 0.91,
    "has_exif": true,
    "subject_coverage_pct": 0.62,
    "quality_verdict": "ACCEPTABLE"
  }
}
```

### 13d. Response — Error Codes

| HTTP Status | Code | Meaning |
|---|---|---|
| 400 | `IMAGE_RESOLUTION_TOO_LOW` | Image below 400×400 px |
| 400 | `IMAGE_FILE_TOO_SMALL` | File under 50 KB — likely over-compressed |
| 400 | `IMAGE_FILE_TOO_LARGE` | File exceeds 10 MB |
| 400 | `UNSUPPORTED_IMAGE_FORMAT` | Format is not JPEG, PNG, or WEBP |
| 400 | `INVALID_SPECIES` | `declared_species` not `Canine` or `Feline` |
| 400 | `MISSING_POLICY_ID` | `policy_id` required for `CLAIM` / `RENEWAL` context |
| 401 | `UNAUTHORIZED` | Missing or invalid API key |
| 403 | `INSUFFICIENT_SCOPE` | API key lacks `verification:write` scope |
| 409 | `DUPLICATE_REQUEST` | `verification_id` already processed; returns cached result |
| 422 | `VERIFICATION_FAILED` | Vision model failed after retries |
| 429 | `RATE_LIMIT_EXCEEDED` | Tenant quota exceeded |
| 503 | `SERVICE_OVERLOADED` | Queue full |
| 500 | `INTERNAL_ERROR` | Unexpected error |

### 13e. Rate Limiting

| Tier | Requests / minute | Requests / day | Burst |
|---|---|---|---|
| Starter | 10 | 300 | 20 |
| Growth | 60 | 5,000 | 100 |
| Enterprise | 300 | 50,000 | 500 |

### 13f. Versioning & Deprecation

Same policy as UC-01/02/03 — additive changes without version bump; breaking changes require `/api/v2/`; 12-month support window post-deprecation.

### 13g. SaaS Integration Notes

- **Stateless** — no session affinity required
- **Idempotency** — same `verification_id` returns cached result within 24 hours
- **Consent flag** — pass `embedding_consent=true` to authorise CLIP embedding storage; without it, embedding is used for this request only and discarded
- **Webhook (roadmap v1.1)** — async mode for batch enrolment campaigns; callback POST on completion
- **Batch enrolment (roadmap v1.2)** — `POST /api/v1/verification/breed/batch` — up to 50 images per request

---

## 14. Pipeline Position (UC Integration Map)

```
[Photo Upload — Enrolment or Claim]
        │
        ▼
[UC-04] Breed & Fraud Verification  ◄─── pet photo (JPEG/PNG)
        │
        ├── VERIFIED / BREED_MISMATCH ──► Underwriting engine (tier + premium)
        ├── NEEDS_REVIEW ────────────────► Manual underwriter queue
        ├── FRAUD_FLAG ──────────────────► Fraud investigation team; claim/policy suspended
        └── REJECTED ────────────────────► Re-submission request to member
                │
                ▼ (on PROCEED)
[UC-01] Receipt & Invoice Parsing
        │
        ▼
[UC-03] Automated Medical Coding
        │
        ▼
[UC-02] Basic Claims Adjudication
```

---

## 15. Out of Scope (v1)

- Video-based breed verification
- Real-time verification during vet consultation (camera stream)
- Exotic species beyond dog and cat
- Age estimation from photos
- Body condition score (BCS) assessment from photos
- Colour / marking pattern fraud (dye, bleach, grooming misrepresentation)
- Document forgery detection (vet certificates, pedigree papers)
- Integration with kennel club or AKC breed registry APIs

---

## 16. Open Questions

| # | Question | Owner |
|---|---|---|
| OQ-01 | What is the legal basis for storing CLIP embeddings under GDPR/CCPA — legitimate interest or explicit consent? | Legal / Compliance |
| OQ-02 | Should breed mismatch at claim time (not enrolment) trigger a policy review or just a premium adjustment going forward? | Actuarial / Product |
| OQ-03 | What is the fraud team SLA for investigating FRAUD_FLAG verdicts — same day or 72-hour? | Operations |
| OQ-04 | Is the stock photo fingerprint DB maintained internally or sourced from a third-party provider? | Engineering |
| OQ-05 | Should a NEEDS_REVIEW at enrolment block policy issuance or allow provisional issuance pending review? | Product / Underwriting |
| OQ-06 | How are multi-pet households handled — one photo per pet, or a group photo parsed by the model? | Product |
