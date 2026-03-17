# Revit API RAG — GCP Cloud Deployment Guide

> **Target**: Deploy the full Revit API RAG system (FastAPI + Gradio UI) to GCP Cloud Run,
> accessible via public URL. No Revit connection needed — cloud mode provides
> Code Generation + API Explorer + Intent Bridge (MCP Bridge tab is local-only).

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                      GCP Cloud Run                      │
│  ┌───────────────────────────────────────────────────┐  │
│  │              Container (port 7860)                 │  │
│  │                                                   │  │
│  │  Gradio UI ─── FastAPI ─── RAGRetriever           │  │
│  │    Tab A: Code Generation (Chat)                  │  │
│  │    Tab B: Text2Revit (Legacy)                     │  │
│  │    Tab C: API Explorer (Search → Rerank → Gen)    │  │
│  │    Tab D: MCP Bridge (disabled — no Revit TCP)    │  │
│  │                                                   │  │
│  │  Data (baked into image):                         │  │
│  │    SQLite: revit_api.db (33MB) + revit_sdk.db     │  │
│  │    ChromaDB: chromadb_api + chromadb_code (372MB) │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
│  Secrets: OPENROUTER_API_KEY, COHERE_API_KEY            │
│  Memory:  1 GiB+    CPU: 2+    Concurrency: 80         │
└─────────────────────────────────────────────────────────┘
         │
         │ HTTPS (public)
         ▼
    https://revit-api-rag-xxxxx.run.app
         │
         │ API calls (outbound)
         ├──► OpenRouter (LLM / Embedding)
         └──► Cohere (Rerank)
```

### Cloud vs Local Differences

| Feature | Local (Windows) | Cloud (GCP) |
|---|---|---|
| Tab A: Code Generation | via RAG + LLM | same |
| Tab B: Text2Revit | via RAG + LLM | same |
| Tab C: API Explorer | via RAG + Rerank | same |
| Tab D: MCP Bridge | connects Revit TCP :18080 | **disabled** (no Revit) |
| Data | local `data/` directory | baked into Docker image |
| API Keys | `.env` file | GCP Secret Manager |
| Port | 7860 | 7860 (Cloud Run maps to 443) |

---

## 2. Prerequisites

```bash
# GCP CLI
gcloud --version        # >= 450.0
docker --version        # >= 24.0

# Login & set project
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud config set run/region asia-east1    # or us-central1
```

### Required API Keys

| Key | Source | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | [openrouter.ai/keys](https://openrouter.ai/keys) | LLM (Claude/Gemini/DeepSeek) + Embedding |
| `COHERE_API_KEY` | [dashboard.cohere.com](https://dashboard.cohere.com/api-keys) | Rerank (optional, free tier available) |

---

## 3. Prepare Data Files

Data files are NOT in git (too large). Download from GitHub Release:

```bash
# From project root
cd revit-api-rag

# Option A: Download from GitHub Release
gh release download v2.0-data -D data/

# Option B: Manual download
# https://github.com/YOUR_ORG/revit-api-rag/releases/tag/v2.0-data

# Ensure directory structure:
# data/
# ├── sqlite/
# │   ├── revit_api.db          (33 MB)
# │   └── revit_sdk.db          (932 KB)
# └── chromadb/
#     ├── chroma.sqlite3
#     ├── chromadb_api/          (subdir — vector index)
#     └── chromadb_code/         (subdir — vector index)
```

> **Note**: If you have `chromadb_api.tar.gz` / `chromadb_code.tar.gz`,
> extract them into `data/chromadb/` first.

---

## 4. Docker Build & Local Test

### 4.1 Build Image

```bash
# From project root
docker build -t revit-api-rag:latest .

# Image size will be ~600MB (Python + deps + data)
docker images revit-api-rag
```

### 4.2 Local Test

```bash
# Run locally with API keys
docker run --rm -p 7860:7860 \
  -e OPENROUTER_API_KEY="sk-or-v1-xxx" \
  -e COHERE_API_KEY="xxx" \
  revit-api-rag:latest

# Verify:
# 1. http://localhost:7860          → Gradio UI
# 2. http://localhost:7860/health   → {"status": "ok"}
# 3. http://localhost:7860/docs     → FastAPI Swagger
```

### 4.3 Quick Smoke Test

```bash
# Health check
curl http://localhost:7860/health

# API search (no LLM needed)
curl -X POST http://localhost:7860/api/v1/bridge/api-search \
  -H "Content-Type: application/json" \
  -d '{"query": "Wall.Create", "top_k": 5, "fast": true}'

# Chat (needs OPENROUTER_API_KEY)
curl -X POST http://localhost:7860/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "How to create a wall in Revit API?", "session_id": "test"}'
```

---

## 5. Deploy to GCP Cloud Run

### 5.1 Store Secrets

```bash
# Create secrets in Secret Manager
echo -n "sk-or-v1-xxx" | gcloud secrets create OPENROUTER_API_KEY --data-file=-
echo -n "xxx"           | gcloud secrets create COHERE_API_KEY --data-file=-

# Grant Cloud Run access
PROJECT_NUM=$(gcloud projects describe $(gcloud config get-value project) --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding OPENROUTER_API_KEY \
  --member="serviceAccount:${PROJECT_NUM}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
gcloud secrets add-iam-policy-binding COHERE_API_KEY \
  --member="serviceAccount:${PROJECT_NUM}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### 5.2 Push Image to Artifact Registry

```bash
# Enable APIs (first time only)
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com

# Create repo (first time only)
gcloud artifacts repositories create revit-api-rag \
  --repository-format=docker \
  --location=asia-east1 \
  --description="Revit API RAG images"

# Configure Docker auth
gcloud auth configure-docker asia-east1-docker.pkg.dev

# Tag & push
REGION=asia-east1
PROJECT=$(gcloud config get-value project)
IMAGE=${REGION}-docker.pkg.dev/${PROJECT}/revit-api-rag/server:latest

docker tag revit-api-rag:latest ${IMAGE}
docker push ${IMAGE}
```

### 5.3 Deploy Service

```bash
gcloud run deploy revit-api-rag \
  --image=${IMAGE} \
  --region=${REGION} \
  --port=7860 \
  --memory=2Gi \
  --cpu=2 \
  --min-instances=0 \
  --max-instances=3 \
  --concurrency=80 \
  --timeout=300 \
  --set-secrets="OPENROUTER_API_KEY=OPENROUTER_API_KEY:latest,COHERE_API_KEY=COHERE_API_KEY:latest" \
  --allow-unauthenticated
```

> **`--allow-unauthenticated`**: Makes the service publicly accessible.
> Remove this flag if you want to require IAM authentication.

### 5.4 Verify Deployment

```bash
# Get service URL
URL=$(gcloud run services describe revit-api-rag --region=${REGION} --format='value(status.url)')
echo "Service URL: ${URL}"

# Health check
curl ${URL}/health

# Open in browser
echo "Open: ${URL}"
```

---

## 6. Configuration Tuning

### 6.1 Cloud Run Resources

| Setting | Recommended | Notes |
|---|---|---|
| Memory | 2 GiB | ChromaDB + SQLite loaded in memory |
| CPU | 2 | Embedding + rerank are CPU-bound |
| Min instances | 0 | Scale to zero when idle (saves cost) |
| Max instances | 3 | Prevent runaway costs |
| Concurrency | 80 | Single Gradio app handles many users |
| Timeout | 300s | LLM streaming can take 30-60s |
| Startup CPU boost | enabled | Faster cold starts |

```bash
# Enable startup CPU boost for faster cold starts
gcloud run services update revit-api-rag \
  --region=${REGION} \
  --cpu-boost
```

### 6.2 config.yaml Overrides

The container uses the baked-in `config/config.yaml`. To override at runtime:

```bash
# Override via environment variables (add to deploy command)
--set-env-vars="DATA_DIR=/app/data"
```

For config changes, rebuild the image. Key settings for cloud:

```yaml
# config/config.yaml — cloud-optimized values
server:
  host: "0.0.0.0"
  port: 7860                    # Cloud Run listens here
  gradio_port: 7860
  cors_origins: ["*"]           # Restrict in production

mcp_bridge:
  revit_host: "localhost"       # N/A in cloud — MCP Bridge tab won't work
  revit_port: 18080

proxy:
  enabled: false                # No proxy needed on GCP
```

### 6.3 Cold Start Optimization

Cloud Run cold starts load ~400MB of data. Typical cold start: **15-25s**.

Mitigation strategies:
1. **Min instances = 1**: Keep one instance warm ($0.00002/s idle)
2. **CPU boost**: Faster startup CPU allocation
3. **Lazy loading**: ChromaDB collections load on first query, not startup

```bash
# Keep 1 warm instance (≈$50/month if always on)
gcloud run services update revit-api-rag \
  --region=${REGION} \
  --min-instances=1
```

---

## 7. CI/CD (Optional)

### GitHub Actions Auto-Deploy

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy to Cloud Run

on:
  push:
    branches: [main]
    paths:
      - 'server/**'
      - 'pipeline/**'
      - 'config/**'
      - 'mcp_bridge/**'
      - 'intent_bridge/**'
      - 'Dockerfile'
      - 'requirements-server.txt'

env:
  PROJECT_ID: ${{ secrets.GCP_PROJECT_ID }}
  REGION: asia-east1
  SERVICE: revit-api-rag
  IMAGE: asia-east1-docker.pkg.dev/${{ secrets.GCP_PROJECT_ID }}/revit-api-rag/server

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write

    steps:
      - uses: actions/checkout@v4

      - id: auth
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.WIF_PROVIDER }}
          service_account: ${{ secrets.WIF_SA }}

      - uses: google-github-actions/setup-gcloud@v2

      - name: Configure Docker
        run: gcloud auth configure-docker ${REGION}-docker.pkg.dev

      - name: Download data from release
        run: |
          gh release download v2.0-data -D data/ || true
          # Extract if compressed
          cd data/chromadb && tar xzf chromadb_api.tar.gz 2>/dev/null || true
          cd data/chromadb && tar xzf chromadb_code.tar.gz 2>/dev/null || true
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Build & Push
        run: |
          docker build -t ${IMAGE}:${{ github.sha }} -t ${IMAGE}:latest .
          docker push ${IMAGE}:${{ github.sha }}
          docker push ${IMAGE}:latest

      - name: Deploy
        run: |
          gcloud run deploy ${SERVICE} \
            --image=${IMAGE}:${{ github.sha }} \
            --region=${REGION} \
            --port=7860 \
            --memory=2Gi --cpu=2 \
            --min-instances=0 --max-instances=3 \
            --concurrency=80 --timeout=300 \
            --set-secrets="OPENROUTER_API_KEY=OPENROUTER_API_KEY:latest,COHERE_API_KEY=COHERE_API_KEY:latest" \
            --allow-unauthenticated
```

---

## 8. Monitoring & Troubleshooting

### Logs

```bash
# Stream logs
gcloud run services logs read revit-api-rag --region=${REGION} --tail=50

# Filter errors
gcloud run services logs read revit-api-rag --region=${REGION} \
  --filter="severity>=ERROR" --limit=20
```

### Common Issues

| Symptom | Cause | Fix |
|---|---|---|
| 502 on startup | OOM — ChromaDB too large | Increase `--memory=4Gi` |
| Timeout on first request | Cold start + data loading | Enable `--cpu-boost`, set `--min-instances=1` |
| "No API key configured" | Secret not mounted | Check `gcloud secrets versions access latest --secret=OPENROUTER_API_KEY` |
| MCP Bridge tab errors | Expected — no Revit in cloud | Ignore; Tab D is local-only |
| Slow embedding search | CPU throttled on low concurrency | Increase `--cpu=4` |
| CORS errors from custom frontend | Origins restricted | Set `cors_origins: ["https://your-domain.com"]` |

### Cost Estimate

| Resource | Spec | Idle Cost | Active Cost |
|---|---|---|---|
| Cloud Run | 2 CPU / 2 GiB, min=0 | $0/mo | ~$0.05/hr active |
| Cloud Run | 2 CPU / 2 GiB, min=1 | ~$50/mo | ~$0.05/hr active |
| Artifact Registry | ~600MB image | ~$0.03/mo | — |
| Secret Manager | 2 secrets | free tier | — |
| Egress | API responses | ~$0.12/GB | — |

> **External API costs** (not GCP):
> - OpenRouter: varies by model ($0.003-$0.06 per 1K tokens)
> - Cohere Rerank: free tier 1000 calls/month

---

## 9. Security Checklist

- [ ] Remove `--allow-unauthenticated` if not for public demo
- [ ] Set `cors_origins` to specific domains in production
- [ ] Rotate API keys periodically via Secret Manager versions
- [ ] Enable Cloud Run VPC connector if accessing private resources
- [ ] Set up budget alerts for the GCP project
- [ ] Consider adding rate limiting (e.g., via Cloud Armor or API Gateway)

---

## 10. Quick Reference Commands

```bash
# ── Build & Deploy ──
docker build -t revit-api-rag .
docker tag revit-api-rag ${IMAGE}
docker push ${IMAGE}
gcloud run deploy revit-api-rag --image=${IMAGE} --region=${REGION}

# ── Update secrets ──
echo -n "new-key" | gcloud secrets versions add OPENROUTER_API_KEY --data-file=-

# ── Scale ──
gcloud run services update revit-api-rag --region=${REGION} --min-instances=1
gcloud run services update revit-api-rag --region=${REGION} --min-instances=0

# ── Rollback ──
gcloud run services update-traffic revit-api-rag --region=${REGION} --to-revisions=REVISION_NAME=100

# ── Delete (cleanup) ──
gcloud run services delete revit-api-rag --region=${REGION}
```
