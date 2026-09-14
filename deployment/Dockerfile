# syntax=docker/dockerfile:1

# --- builder ---------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build
COPY pyproject.toml requirements.txt ./
COPY agent_gateway ./agent_gateway

RUN python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir --upgrade pip \
    && /venv/bin/pip install --no-cache-dir -e .

# --- runtime -----------------------------------------------------------------
FROM python:3.11-slim AS runtime

RUN useradd --create-home --uid 1000 gateway
WORKDIR /app

COPY --from=builder /venv /venv
COPY --from=builder /build/agent_gateway /app/agent_gateway
COPY pyproject.toml /app/pyproject.toml

ENV PATH="/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

RUN mkdir -p /app/data && chown -R gateway:gateway /app
USER gateway

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')" || exit 1

CMD ["uvicorn", "agent_gateway.proxy.server:app", "--host", "0.0.0.0", "--port", "8080"]
