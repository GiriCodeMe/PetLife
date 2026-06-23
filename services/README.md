# LifeGroup Pet Insurance AI Services

Six FastAPI microservices powered by local Ollama LLM inference.

## Services

| Port | Service | Model | Endpoint |
|------|---------|-------|----------|
| 8001 | UC-01 Invoice Parsing | Llama 3.1 8B | POST /api/v1/invoices/parse |
| 8002 | UC-02 Claims Adjudication | Phi-4 14B | POST /api/v1/claims/adjudicate |
| 8003 | UC-03 Medical Coding | Qwen2.5 14B | POST /api/v1/coding/notes |
| 8004 | UC-04 Breed Fraud | Llama 3.2 Vision 11B + CLIP | POST /api/v1/verification/breed |
| 8005 | UC-05 History Review | Llama 3.3 70B | POST /api/v1/history/reviews |
| 8006 | UC-06 Underwriting | Llama 3.3 70B + Qwen 3 32B x5 | POST /api/v1/underwriting/policies |

## Quick Start

```bash
cp .env.example .env
# edit .env and set API_KEY
docker compose up -d
```

## Pull Models

```bash
docker exec ollama ollama pull llama3.1:8b-instruct-q4_K_M
docker exec ollama ollama pull phi4:14b-q4_K_M
docker exec ollama ollama pull qwen2.5:14b-instruct-q4_K_M
docker exec ollama ollama pull llama3.2-vision:11b-q4_K_M
docker exec ollama ollama pull llama3.3:70b-instruct-q4_K_M
docker exec ollama ollama pull qwen3:32b-q4_K_M
```

## Security

All endpoints require Bearer token authentication via the API_KEY env var. PHI data is never logged or persisted beyond the transaction lifetime.
