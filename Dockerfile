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

# System deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

# Python deps (server only)
COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

# Copy source code
COPY config/ config/
COPY pipeline/ pipeline/
COPY server/ server/
COPY mcp_bridge/ mcp_bridge/
COPY intent_bridge/ intent_bridge/
COPY prompt_bridge/ prompt_bridge/
COPY text_studio/ text_studio/

# Copy built React frontend
COPY --from=frontend-build /build/dist/ frontend/dist/

# Copy data (ChromaDB + SQLite + Skills)
# For VM deployment: prefer volume mount (-v ./data:/app/data) instead
COPY data/sqlite/ data/sqlite/
COPY data/chromadb/ data/chromadb/
COPY data/skills/ data/skills/

# Environment
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data

EXPOSE 7860

CMD ["python", "-m", "server.main"]
