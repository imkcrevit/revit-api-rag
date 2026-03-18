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

# Copy data (ChromaDB + SQLite)
# For VM deployment: prefer volume mount (-v ./data:/app/data) instead
COPY data/sqlite/ data/sqlite/
COPY data/chromadb/ data/chromadb/

# Environment
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data

EXPOSE 7860

CMD ["python", "-m", "server.main"]
