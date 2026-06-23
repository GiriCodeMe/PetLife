# LifeGroup AI — Underwriting Workbench Demo Instructions

_Generated: 2026-06-23_

---

## Contents

1. [Prerequisites](#1-prerequisites)
2. [Launching All Six Services](#2-launching-all-six-services)
3. [Verifying Service Health](#3-verifying-service-health)
4. [Smoke Testing Each API Endpoint](#4-smoke-testing-each-api-endpoint)
   - [UC-04 Breed & Fraud Verification](#uc-04-breed--fraud-verification)
   - [UC-05 Longitudinal Medical History Review](#uc-05-longitudinal-medical-history-review)
   - [UC-01 Receipt & Invoice Parsing](#uc-01-receipt--invoice-parsing)
   - [UC-03 Automated Medical Coding](#uc-03-automated-medical-coding)
   - [UC-02 Basic Claims Adjudication](#uc-02-basic-claims-adjudication)
   - [UC-06 Multi-Agent Risk Underwriting](#uc-06-multi-agent-risk-underwriting)
5. [UI Demo Walkthrough](#5-ui-demo-walkthrough)
6. [Troubleshooting Quick Reference](#6-troubleshooting-quick-reference)

---

## 1. Prerequisites

### Hardware

| Requirement | Minimum | Recommended |
|---|---|---|
| RAM | 32 GB | 64 GB |
| GPU VRAM | 12 GB (for UC-04 Vision) | 24 GB |
| Disk (models) | 80 GB free | 120 GB free |
| CPU cores | 8 | 16+ |

> **NOTE:** UC-05 and UC-06 use Llama 3.3 70B and Qwen 3 32B respectively. Without a GPU these services will run on CPU and take 3–10× longer to respond. For a live demo, a machine with an NVIDIA RTX 4090 or A100 is recommended.

### Software

| Tool | Version | Install |
|---|---|---|
| Docker Desktop | 4.28+ | https://www.docker.com/products/docker-desktop |
| Ollama | 0.4+ | https://ollama.com |
| `curl` | any | Pre-installed on macOS/Linux; Git Bash on Windows |
| `jq` | any | `brew install jq` / `choco install jq` — optional but useful |

---

## 2. Launching All Six Services

All services are defined in a single Compose file. The recommended launch order is:
infrastructure → models → services.

### Step 1 — Configure environment

```bash
cd C:/AIBrain/specs/LifeGroup/Services

cp .env.example .env
```

Open `.env` and set at minimum:

```dotenv
API_KEY=demo-local-key-2026
OLLAMA_BASE_URL=http://ollama:11434
POSTGRES_DSN=postgresql://pguser:pgpass@postgres:5432/breeddb
REDIS_URL=redis://redis:6379/0
JOB_STORE_URL=redis://redis:6379/1
OLLAMA_TIMEOUT=180
```

### Step 2 — Pull Ollama models (one-time, ~25–80 GB)

Run these before starting the stack so Ollama is ready when the services boot:

```bash
# UC-01 — Invoice Parsing
ollama pull llama3.1:8b-instruct-q4_K_M

# UC-02 — Claims Adjudication
ollama pull phi4:14b-instruct-q4_K_M

# UC-03 — Automated Medical Coding
ollama pull qwen2.5:14b-instruct-q4_K_M

# UC-04 — Breed & Fraud Verification (Vision model)
ollama pull llama3.2-vision:11b-instruct-q4_K_M

# UC-05 — Longitudinal Medical History
ollama pull llama3.3:70b-instruct-q4_K_M

# UC-06 — Multi-Agent Underwriting (orchestrator = 70B above; agents below)
ollama pull qwen3:32b-q4_K_M
```

> **NOTE:** `llama3.3:70b` is shared between UC-05 and UC-06. Pull it once; both services use the same Ollama instance on port 11434.

### Step 3 — Start the full stack

```bash
docker compose up -d
```

This brings up 9 containers:

| Container | Role | Port |
|---|---|---|
| `ollama` | Local LLM inference server | 11434 |
| `postgres` | pgvector store (CLIP embeddings, UC-04) | 5432 |
| `redis` | Job queue + async result store (UC-05, UC-06) | 6379 |
| `uc01-invoice` | Receipt & Invoice Parsing | **8001** |
| `uc02-adjudication` | Claims Adjudication | **8002** |
| `uc03-coding` | Automated Medical Coding | **8003** |
| `uc04-breed` | Breed & Fraud Verification | **8004** |
| `uc05-history` | Longitudinal Medical History | **8005** |
| `uc06-underwriting` | Multi-Agent Risk Underwriting | **8006** |

### Step 4 — Monitor startup logs

```bash
docker compose logs -f --tail=50
```

Wait until you see all 6 services log `Application startup complete` before running any API calls. Startup typically takes 30–90 seconds after the containers start, as each service makes an initial connection to Ollama.

### Starting / stopping individual services

```bash
# Start one service only
docker compose up -d uc04-breed

# Stop one service
docker compose stop uc04-breed

# Restart a service after a code change
docker compose up -d --force-recreate uc03-coding

# Stop the full stack and remove containers (data volumes are preserved)
docker compose down

# Full teardown including volumes
docker compose down -v
```

---

## 3. Verifying Service Health

Each service exposes a `GET /health` endpoint. A healthy response returns HTTP 200 with `{"status":"ok"}`.

### Quick all-services health check (bash loop)

```bash
for port in 8001 8002 8003 8004 8005 8006; do
  status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:$port/health)
  echo "Port $port: $status"
done
```

Expected output:

```
Port 8001: 200
Port 8002: 200
Port 8003: 200
Port 8004: 200
Port 8005: 200
Port 8006: 200
```

### FastAPI interactive docs

Each service ships with Swagger UI. Open in a browser to explore schemas and run test calls manually:

| Service | Swagger URL |
|---|---|
| UC-01 Invoice Parsing | http://localhost:8001/docs |
| UC-02 Claims Adjudication | http://localhost:8002/docs |
| UC-03 Medical Coding | http://localhost:8003/docs |
| UC-04 Breed & Fraud | http://localhost:8004/docs |
| UC-05 Medical History | http://localhost:8005/docs |
| UC-06 Underwriting | http://localhost:8006/docs |

---

## 4. Smoke Testing Each API Endpoint

All curl examples use:
- `API_KEY=demo-local-key-2026` (matches `.env` value set in Step 1)
- The Biscuit/Mitchell demo case used in the UI workbench
- `jq` for pretty-printing JSON responses (remove `| jq` if not installed)

> **NOTE:** Replace file paths (e.g. `./samples/biscuit-photo.jpg`) with actual files on your machine. Sample files can be found in `Services/samples/` once the repo is populated.

---

### UC-04 Breed & Fraud Verification

**Endpoint:** `POST http://localhost:8004/api/v1/verification/breed`  
**Content-Type:** `multipart/form-data`  
**Processing time:** ~5–15 seconds

```bash
curl -s -X POST http://localhost:8004/api/v1/verification/breed \
  -H "Authorization: Bearer demo-local-key-2026" \
  -F "image=@./samples/biscuit-photo.jpg;type=image/jpeg" \
  -F "verification_id=vrf-biscuit-001" \
  -F "context=ENROLMENT" \
  -F "member_id=MBR-00123" \
  -F "declared_species=Canine" \
  -F "declared_breed=French Bulldog" \
  -F "declared_breed_tier=3" \
  -F "pet_name=Biscuit" \
  -F "submission_date=2026-06-23" \
  -F "pet_age_years=3" \
  | jq '{verdict: .data.overall_verdict, action: .data.recommended_action, breed: .data.breed_assessment.primary_breed, confidence: .data.breed_assessment.primary_confidence}'
```

**Expected smoke-test response:**

```json
{
  "verdict": "VERIFIED",
  "action": "PROCEED",
  "breed": "French Bulldog",
  "confidence": 0.942
}
```

**Failure indicators to watch for:**

| HTTP Code | Meaning |
|---|---|
| 422 | Missing required field — check form fields |
| 503 | Ollama or CLIP model not loaded — check `ollama list` |
| 400 | Image below 400×400px or over 10MB |

---

### UC-05 Longitudinal Medical History Review

**Endpoint:** `POST http://localhost:8005/api/v1/history/reviews` (submit, returns 202)  
**Poll:** `GET http://localhost:8005/api/v1/history/reviews/{review_id}`  
**Content-Type:** `multipart/form-data`  
**Processing time:** 30–120 seconds (async — poll until `status: COMPLETED`)

**Step 1 — Submit the job:**

```bash
curl -s -X POST http://localhost:8005/api/v1/history/reviews \
  -H "Authorization: Bearer demo-local-key-2026" \
  -F "record_bundle=@./samples/biscuit-records.pdf;type=application/pdf" \
  -F "review_id=rev-biscuit-001" \
  -F "context=UNDERWRITING" \
  -F "member_id=MBR-00123" \
  -F "policy_start_date=2025-06-01" \
  -F "patient_name=Biscuit" \
  -F "patient_species=Canine" \
  -F "patient_breed=French Bulldog" \
  -F "patient_dob=2021-04-12" \
  | jq '{status: .status, review_id: .review_id}'
```

Expected: `HTTP 202` with `{"status": "ACCEPTED", "review_id": "rev-biscuit-001"}`

**Step 2 — Poll until complete:**

```bash
# Poll every 10 seconds until status is COMPLETED or FAILED
watch -n 10 "curl -s http://localhost:8005/api/v1/history/reviews/rev-biscuit-001 \
  -H 'Authorization: Bearer demo-local-key-2026' \
  | jq '{status: .data.status, verdict: .data.overall_verdict, recommendation: .data.underwriting_recommendation}'"
```

**Expected final response:**

```json
{
  "status": "COMPLETED",
  "verdict": "PRE_EXISTING_FOUND",
  "recommendation": "APPROVE_WITH_EXCLUSIONS"
}
```

**Step 3 — Stream live progress (optional SSE):**

```bash
curl -N http://localhost:8005/api/v1/history/reviews/rev-biscuit-001/progress \
  -H "Authorization: Bearer demo-local-key-2026"
```

---

### UC-01 Receipt & Invoice Parsing

**Endpoint:** `POST http://localhost:8001/api/v1/invoices/parse`  
**Content-Type:** `multipart/form-data`  
**Processing time:** ~3–8 seconds

```bash
curl -s -X POST http://localhost:8001/api/v1/invoices/parse \
  -H "Authorization: Bearer demo-local-key-2026" \
  -F "file=@./samples/pvc-invoice-2025-03-18.pdf;type=application/pdf" \
  -F "tenant_id=lifegroup-demo" \
  -F "hints.clinic_name=Paddington Veterinary Centre" \
  | jq '{invoice_number: .data.invoice_number, clinic: .data.clinic_name, total: .data.total_due, currency: .data.currency, lines: (.data.line_items | length)}'
```

**Expected smoke-test response:**

```json
{
  "invoice_number": "PVC-2025-00847",
  "clinic": "Paddington Veterinary Centre",
  "total": 659.50,
  "currency": "GBP",
  "lines": 5
}
```

---

### UC-03 Automated Medical Coding

**Endpoint:** `POST http://localhost:8003/api/v1/coding/notes`  
**Content-Type:** `application/json`  
**Processing time:** ~8–20 seconds (two-pass LLM)

```bash
curl -s -X POST http://localhost:8003/api/v1/coding/notes \
  -H "Authorization: Bearer demo-local-key-2026" \
  -H "Content-Type: application/json" \
  -d '{
    "coding_request_id": "cod-biscuit-001",
    "claim_id": "CLM-2025-0847",
    "member_id": "MBR-00123",
    "note_type": "SOAP",
    "note_text": "S: Owner reports Biscuit has been scratching excessively for 3 weeks, particularly around face, paws and axillae. Previously diagnosed atopic dermatitis, currently on Apoquel. O: Erythema and excoriation noted on ventral abdomen. Intradermal test positive for house dust mite (3+) and grass pollen (2+). A: Atopic dermatitis, environmental allergens. Secondary allergic contact dermatitis. P: Increase Apoquel to twice daily. Administer Cytopoint 40mg SC. Repeat skin cytology. Follow up in 6 weeks.",
    "note_date": "2025-03-18",
    "patient_species": "Canine",
    "patient_breed": "French Bulldog",
    "patient_age_years": 3,
    "attending_vet": "Dr. Priya Nair",
    "clinic_name": "Paddington Veterinary Centre",
    "target_code_systems": ["SNOMED-CT", "ICD-10-CM"]
  }' \
  | jq '{confidence: .data.overall_confidence, requires_review: .data.requires_review, codes_found: (.data.coded_findings | length), primary_dx: (.data.coded_findings[] | select(.is_primary_diagnosis==true) | .snomed_display)}'
```

**Expected smoke-test response:**

```json
{
  "confidence": "HIGH",
  "requires_review": false,
  "codes_found": 5,
  "primary_dx": "Atopic dermatitis"
}
```

---

### UC-02 Basic Claims Adjudication

**Endpoint:** `POST http://localhost:8002/api/v1/claims/adjudicate`  
**Content-Type:** `application/json`  
**Processing time:** ~5–12 seconds

> **NOTE:** This endpoint expects the full invoice JSON object from UC-01 in the `invoice` field. For a smoke test, you can send a minimal invoice payload as shown below.

```bash
curl -s -X POST http://localhost:8002/api/v1/claims/adjudicate \
  -H "Authorization: Bearer demo-local-key-2026" \
  -H "Content-Type: application/json" \
  -d '{
    "claim_id": "CLM-2025-0847",
    "member_id": "MBR-00123",
    "policy_id": "POL-FRBL-2025-0042",
    "submission_date": "2025-03-20",
    "submitted_by": "clinic",
    "invoice": {
      "clinic_name": "Paddington Veterinary Centre",
      "invoice_number": "PVC-2025-00847",
      "invoice_date": "2025-03-18",
      "visit_date": "2025-03-18",
      "patient_name": "Biscuit",
      "patient_species": "Canine",
      "patient_breed": "French Bulldog",
      "owner_name": "Sarah Mitchell",
      "line_items": [
        {"description": "Consultation - Dermatology Follow-up", "category": "CONSULTATION", "quantity": 1, "unit_price": 85.00, "line_total": 85.00},
        {"description": "Intradermal Allergy Testing", "category": "DIAGNOSTICS", "quantity": 1, "unit_price": 320.00, "line_total": 320.00},
        {"description": "Apoquel 16mg Tablets x30", "category": "MEDICATION", "quantity": 1, "unit_price": 74.50, "line_total": 74.50},
        {"description": "Cytopoint 40mg Injection", "category": "MEDICATION", "quantity": 1, "unit_price": 112.00, "line_total": 112.00},
        {"description": "Skin Cytology and Culture", "category": "LABORATORY", "quantity": 1, "unit_price": 68.00, "line_total": 68.00}
      ],
      "subtotal": 659.50,
      "total_due": 659.50,
      "currency": "GBP"
    }
  }' \
  | jq '{status: .data.overall_status, billed: .data.total_billed, reimbursable: .data.total_reimbursable, adjudicator: .data.adjudicator}'
```

**Expected smoke-test response:**

```json
{
  "status": "PARTIALLY_APPROVED",
  "billed": 659.50,
  "reimbursable": 73.20,
  "adjudicator": "AI"
}
```

---

### UC-06 Multi-Agent Risk Underwriting

**Endpoint:** `POST http://localhost:8006/api/v1/underwriting/policies` (submit, returns 202)  
**Poll:** `GET http://localhost:8006/api/v1/underwriting/policies/{job_id}`  
**Content-Type:** `application/json`  
**Processing time:** 60–180 seconds (5 AI agents running in parallel)

**Step 1 — Submit the underwriting job:**

```bash
curl -s -X POST http://localhost:8006/api/v1/underwriting/policies \
  -H "Authorization: Bearer demo-local-key-2026" \
  -H "Content-Type: application/json" \
  -d '{
    "application_id": "APP-2025-BISCUIT-001",
    "case_bundle": {
      "invoice_data":       {"invoice_number": "PVC-2025-00847", "total_due": 659.50, "line_items": []},
      "adjudication_history": {"claim_id": "CLM-2025-0847", "overall_status": "PARTIALLY_APPROVED", "total_reimbursable": 73.20},
      "medical_codes":      {"coding_request_id": "cod-biscuit-001", "overall_confidence": "HIGH", "coded_findings": []},
      "breed_verification": {"verification_id": "vrf-biscuit-001", "overall_verdict": "VERIFIED", "breed_assessment": {"primary_breed": "French Bulldog", "predicted_risk_tier": 3}},
      "history_review":     {"review_id": "rev-biscuit-001", "overall_verdict": "PRE_EXISTING_FOUND", "underwriting_recommendation": "APPROVE_WITH_EXCLUSIONS", "pre_existing_conditions": [{"condition_name": "Atopic Dermatitis", "icd10_code": "L20.89"}, {"condition_name": "BOAS", "icd10_code": "J98.09"}]}
    },
    "application_form": {
      "policy_holder_name": "Sarah Mitchell",
      "pet_name": "Biscuit",
      "species": "canine",
      "declared_breed": "French Bulldog",
      "date_of_birth": "2021-04-12",
      "postcode": "SW1A 2AA",
      "policy_type": "ACCIDENT_ILLNESS"
    },
    "priority": "STANDARD"
  }' \
  | jq '{job_id: .job_id, status: .status}'
```

Expected: `HTTP 202` with `{"job_id": "...", "status": "QUEUED"}`

**Step 2 — Poll until complete (replace `{JOB_ID}` with the returned value):**

```bash
JOB_ID="<paste-job-id-here>"

curl -s http://localhost:8006/api/v1/underwriting/policies/$JOB_ID \
  -H "Authorization: Bearer demo-local-key-2026" \
  | jq '{
      status: .data.status,
      verdict: .data.underwriting_package.overall_verdict,
      premium: .data.underwriting_package.actuarial_assessment.adjusted_premium_annual_usd,
      deductible: .data.underwriting_package.underwriting_decision.deductible_usd,
      reimbursement_pct: .data.underwriting_package.underwriting_decision.reimbursement_pct,
      exclusions: [.data.underwriting_package.underwriting_decision.exclusions[].condition],
      compliance: .data.underwriting_package.compliance_validation.status
    }'
```

**Expected final response:**

```json
{
  "status": "COMPLETED",
  "verdict": "APPROVED_WITH_ADJUSTMENTS",
  "premium": 1847,
  "deductible": 250,
  "reimbursement_pct": 80,
  "exclusions": ["Atopic Dermatitis", "Brachycephalic Obstructive Airway Syndrome"],
  "compliance": "COMPLIANT"
}
```

**Step 3 — Stream agent progress via SSE:**

```bash
curl -N http://localhost:8006/api/v1/underwriting/policies/$JOB_ID/stream \
  -H "Authorization: Bearer demo-local-key-2026"
```

Each agent emits a line as it completes: `data: {"agent":"Vet Tech","status":"DONE","tokens_out":612}`

---

## 5. UI Demo Walkthrough

The Underwriting Workbench is a single-file HTML demo that simulates the full pipeline without needing the backend services running. It uses pre-loaded mock data to demonstrate the end-to-end underwriting journey.

### Opening the demo

```
C:\AIBrain\specs\LifeGroup\demo\underwriting-workbench.html
```

Open this file directly in any modern browser (Chrome, Edge, Firefox). No web server is required.

### Case Setup bar

The top bar is pre-filled with the Biscuit/Mitchell demo case:

| Field | Default value | How to change |
|---|---|---|
| Policy Holder | Sarah Mitchell | Type to replace |
| Pet Name | Biscuit | Type to replace |
| Species | Canine | Dropdown |
| Breed | French Bulldog | Type to replace |
| Date of Birth | 2021-04-12 | Date picker |
| Postcode | SW1A 2AA | Type to replace |
| Policy Type | ACCIDENT_ILLNESS | Dropdown |

> **NOTE:** Changing the case setup fields changes the display labels only — the mock result data is fixed. For a live demo with a real prospect, run the actual API endpoints and swap in the response JSON.

### Running the pipeline step by step

Work through each panel in the left sidebar order — this mirrors the data dependency chain:

| Step | Click in sidebar | Panel opens | Click button | Wait |
|---|---|---|---|---|
| 1 | UC-04 Breed & Fraud | Breed panel expands | **Run Service** | ~2 sec — photo analysis simulation |
| 2 | UC-05 Medical History | History panel expands | **Run Service** | ~4 sec — 42-page record bundle |
| 3 | UC-01 Invoice Parsing | Invoice panel expands | **Run Service** | ~2 sec — PDF extraction |
| 4 | UC-03 Medical Coding | Coding panel expands | **Run Service** | ~3 sec — two-pass NLP coding |
| 5 | UC-02 Adjudication | Claims panel expands | **Run Service** | ~2 sec — policy rule engine |
| 6 | UC-06 Underwriting | Final panel expands | **Run Service** | ~5 sec — 5 AI agents |

Each step transitions through three visual states:
- **Pending** — grey ring in sidebar, "Run Service" button enabled
- **Running** — blue pulsing ring, spinner shown in panel with contextual loading message
- **Complete** — green filled ring, "✓ Done" badge on button, full result table visible

### Running the full pipeline in one click

Click **▶ Run Full Pipeline** in the top-right. All 6 services execute in sequence with staggered delays. The progress bar at the bottom of the sidebar fills from 0 → 100% as each step completes.

Estimated total time for the auto-run demo: **~18 seconds**.

### Navigating results

Click any completed step in the sidebar to jump to and expand that panel. Clicking the panel header directly also collapses/expands it. All panels can be open simultaneously for side-by-side comparison.

### Key result panels to highlight in a demo

| Panel | Talking point |
|---|---|
| UC-04 Breed & Fraud | Point to Tier 3 risk rating and the "No duplicate image" fraud signal |
| UC-05 Medical History | Show the red PRE-EXISTING flags on Atopic Dermatitis and BOAS — this drives exclusions |
| UC-02 Adjudication | Show how 3 of 5 line items are DENIED due to the pre-existing exclusion — £659 billed but only £73.20 reimbursable |
| UC-06 Final Verdict | The full financial decision: £1,847 premium, 80% reimbursement, 2 exclusion riders, compliance PASS |

### Resetting the demo

Reload the page (`F5` / `Cmd-R`) to reset all steps to Pending and re-run the pipeline fresh.

---

## 6. Troubleshooting Quick Reference

| Symptom | Likely cause | Fix |
|---|---|---|
| `curl: (7) Failed to connect` | Container not running | `docker compose ps` — check the container status; `docker compose up -d uc0X-name` |
| HTTP 503 on any service | Ollama model not loaded | `ollama list` — if model is missing, pull it again (Step 2 above) |
| HTTP 422 Unprocessable Entity | Missing or wrong-type field | Check the Swagger UI at `/docs` for the exact schema |
| UC-05 or UC-06 stuck at PROCESSING | 70B model taking long on CPU | Normal — wait up to 10 minutes on CPU-only; add GPU for demo speed |
| UC-04 HTTP 400 Bad Request | Image resolution below 400×400 or over 10MB | Resize or compress the photo |
| `no such file or directory` in curl | Sample file path wrong | Use `ls ./samples/` to confirm file names exist |
| HTML demo file is blank / unstyled | CSS inject script not run | Re-run: `node .codemie/claude-plugin/skills/codemie-html-report/scripts/inject-css.js demo/underwriting-workbench.html` |
| Docker containers start but Ollama says model not found | Compose started before pull finished | `ollama pull <model>` then `docker compose restart uc0X-name` |

### Log inspection per service

```bash
# Tail logs for a specific service
docker compose logs -f uc06-underwriting

# View last 100 lines of a service
docker compose logs --tail=100 uc05-history

# Check all containers for errors
docker compose logs | grep -i "error\|exception\|traceback" | tail -40
```

### Confirm Ollama models are loaded

```bash
ollama list
```

All 6 models (or 7 unique models if counting the shared 70B) should appear with their quantisation tags.

---

_End of document — LifeGroup AI Underwriting Workbench v1.0_
