# UC-03 Automated Medical Coding — container image
# Requires Ollama to be reachable at OLLAMA_BASE_URL (default: http://localhost:11434)
# Set API_KEY env var before running.

FROM python:3.12-slim

# ---------- OS-level hygiene ----------
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# ---------- App user (non-root) ----------
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# ---------- Dependencies ----------
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ---------- Application code ----------
COPY app/ ./app/

# Ensure the appuser owns everything
RUN chown -R appuser:appuser /app

USER appuser

# ---------- Runtime config ----------
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OLLAMA_BASE_URL=http://localhost:11434 \
    OLLAMA_MODEL=qwen2.5:14b-instruct-q4_K_M \
    API_KEY=""

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# ---------- Entrypoint ----------
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
