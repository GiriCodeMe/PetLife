FROM python:3.12-slim

# Security: run as non-root user
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# Install dependencies first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY app/ ./app/

# Ownership
RUN chown -R appuser:appgroup /app

USER appuser

# Environment defaults (override at runtime)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OLLAMA_BASE_URL=http://ollama:11434 \
    ORCHESTRATOR_MODEL=llama3.3:70b-instruct-q4_K_M \
    SPECIALIST_MODEL=qwen3:32b-q4_K_M \
    AGENT_TIMEOUT_SECONDS=120 \
    API_KEY=changeme \
    PORT=8006

EXPOSE 8006

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/health')"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
