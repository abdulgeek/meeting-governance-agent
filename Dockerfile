# Governance engine: FastAPI WebSocket + STT + the decision core.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# deps first for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# runtime code only (audio/meeting/scripts/tests are dev artifacts, not shipped)
COPY governance ./governance
COPY realtime ./realtime
COPY policies ./policies

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Set DEEPGRAM_API_KEY (true streaming) and AWS creds via env in your deploy platform.
CMD ["uv", "run", "uvicorn", "realtime.server:app", "--host", "0.0.0.0", "--port", "8000"]
