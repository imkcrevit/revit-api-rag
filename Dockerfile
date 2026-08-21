## Stage 1: Build React frontend
FROM node:20-alpine AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

## Stage 2: Python server
FROM python:3.11-slim

WORKDIR /app

# Python deps (server only)
# build-essential is only needed to compile wheels; purge it in the same layer
COPY requirements-server.txt .
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    pip install --no-cache-dir -r requirements-server.txt && \
    apt-get purge -y build-essential && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Copy source code
COPY config/ config/
COPY prompts/ prompts/
COPY pipeline/ pipeline/
COPY server/ server/
COPY mcp_bridge/ mcp_bridge/
COPY intent_bridge/ intent_bridge/
COPY prompt_bridge/ prompt_bridge/
COPY text_studio/ text_studio/
COPY scripts/docker-entrypoint.sh /usr/local/bin/revit-api-rag-entrypoint

# Copy built React frontend
COPY --from=frontend-build /build/dist/ frontend/dist/

# SQLite + ChromaDB are mounted by docker-compose at runtime; do NOT bake large
# model data into the image layer. Only skills are copied in.
RUN mkdir -p data/sqlite data/chromadb data/skills
COPY data/skills/ data/skills/

# Environment
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data

# The entrypoint starts as root only long enough to copy root-owned Docker
# secrets, then launches the application as uid/gid 1000. Volume-mounted data
# dirs must therefore remain writable by uid 1000.
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app && \
    chmod 0555 /usr/local/bin/revit-api-rag-entrypoint

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/health')" || exit 1

USER root
ENTRYPOINT ["/usr/local/bin/revit-api-rag-entrypoint"]
CMD ["python", "-m", "server.main"]
