# Revit API RAG — GCP VM Deployment Guide

> **Target**: Deploy the full Revit API RAG system (FastAPI + Gradio UI) to a GCP
> Compute Engine **e2-medium** VM, accessible via public URL.
> Cloud mode provides Code Generation + API Explorer + Intent Bridge.
> MCP Bridge tab is local-only (future: point to remote Revit host).

---

## 1. Architecture Overview

```
┌───────────────────────────────────────────────────────────┐
│              GCP Compute Engine (e2-medium)                │
│              2 vCPU / 4 GB RAM / Debian 12                │
│  ┌─────────────────────────────────────────────────────┐  │
│  │             Docker Compose (port 7860)               │  │
│  │                                                     │  │
│  │  Gradio UI ─── FastAPI ─── RAGRetriever             │  │
│  │    Tab A: Code Generation (Chat)                    │  │
│  │    Tab B: Text2Revit (Legacy)                       │  │
│  │    Tab C: API Explorer (Search → Rerank → Gen)      │  │
│  │    Tab D: MCP Bridge (disabled — no Revit TCP)      │  │
│  │                                                     │  │
│  │  Data (persistent disk mount):                      │  │
│  │    SQLite: revit_api.db (33MB) + revit_sdk.db       │  │
│  │    ChromaDB: chromadb_api + chromadb_code (372MB)   │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                           │
│  Env: OPENROUTER_API_KEY, COHERE_API_KEY (via .env file)  │
│  Firewall: tcp:7860 (allow ingress)                       │
└───────────────────────────────────────────────────────────┘
         │
         │ HTTP (public IP:7860)
         ▼
    http://<EXTERNAL_IP>:7860
         │
         │ API calls (outbound)
         ├──► OpenRouter (LLM / Embedding)
         └──► Cohere (Rerank)
```

### Cloud vs Local Differences

| Feature | Local (Windows) | Cloud (GCP VM) |
|---|---|---|
| Tab A: Code Generation | via RAG + LLM | same |
| Tab B: Text2Revit | via RAG + LLM | same |
| Tab C: API Explorer | via RAG + Rerank | same |
| Tab D: MCP Bridge | connects Revit TCP :18080 | **disabled** (no Revit) |
| Data | local `data/` directory | persistent disk mount |
| API Keys | `.env` file | `.env` file on VM |
| Port | 7860 | 7860 (firewall opened) |

---

## 2. Prerequisites

```bash
# GCP CLI (local machine)
gcloud --version        # >= 450.0
docker --version        # >= 24.0

# Login & set project
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud config set compute/zone asia-east1-b    # or us-central1-a
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

# Image contains Python deps and app code. SQLite/ChromaDB data is mounted at runtime.
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

## 5. Create GCP VM

### 5.1 Create e2-medium Instance

```bash
ZONE=asia-east1-b
VM_NAME=revit-api-rag

gcloud compute instances create ${VM_NAME} \
  --zone=${ZONE} \
  --machine-type=e2-medium \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-size=30GB \
  --boot-disk-type=pd-standard \
  --tags=revit-rag-server

# Get external IP
gcloud compute instances describe ${VM_NAME} \
  --zone=${ZONE} \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
```

> **e2-medium**: 2 vCPU (shared) / 4 GB RAM. Sufficient for the RAG server
> with moderate concurrent users. Upgrade to e2-standard-2 (dedicated CPU)
> if you see performance issues.

### 5.2 Open Firewall

```bash
gcloud compute firewall-rules create allow-revit-rag \
  --direction=INGRESS \
  --priority=1000 \
  --network=default \
  --action=ALLOW \
  --rules=tcp:7860 \
  --source-ranges=0.0.0.0/0 \
  --target-tags=revit-rag-server
```

> **Security note**: `0.0.0.0/0` allows access from anywhere.
> Restrict `--source-ranges` to your IP range in production.

### 5.3 (Optional) Reserve Static IP

```bash
# Prevent IP change on VM restart
gcloud compute addresses create revit-rag-ip \
  --region=asia-east1

gcloud compute instances delete-access-config ${VM_NAME} \
  --zone=${ZONE} --access-config-name="External NAT"

gcloud compute instances add-access-config ${VM_NAME} \
  --zone=${ZONE} \
  --address=$(gcloud compute addresses describe revit-rag-ip --region=asia-east1 --format='get(address)')
```

---

## 6. Deploy to VM

### 6.1 SSH into VM

```bash
gcloud compute ssh ${VM_NAME} --zone=${ZONE}
```

### 6.2 Install Docker on VM

```bash
# Install Docker
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Add current user to docker group (re-login required)
sudo usermod -aG docker $USER
newgrp docker
```

### 6.3 Clone Repo & Setup

```bash
# Clone project
git clone https://github.com/YOUR_ORG/revit-api-rag.git
cd revit-api-rag

# Download data files
gh release download v2.0-data -D data/ || true
cd data/chromadb && tar xzf chromadb_api.tar.gz 2>/dev/null; cd ../..
cd data/chromadb && tar xzf chromadb_code.tar.gz 2>/dev/null; cd ../..

# Create .env file
cat > .env << 'EOF'
OPENROUTER_API_KEY=sk-or-v1-xxx
COHERE_API_KEY=xxx
EOF
chmod 600 .env
```

### 6.4 Docker Compose

Create `docker-compose.yml` (if not already present):

```yaml
services:
  revit-rag:
    build: .
    image: revit-api-rag:latest
    container_name: revit-rag
    restart: unless-stopped
    ports:
      - "7860:7860"
    env_file:
      - .env
    volumes:
      - ./data:/app/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:7860/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
```

### 6.5 Start Service

```bash
# Build and start
docker compose up -d --build

# Check logs
docker compose logs -f

# Verify
curl http://localhost:7860/health
```

### 6.6 Verify from External

```bash
# From local machine (replace with your VM's external IP)
EXTERNAL_IP=$(gcloud compute instances describe ${VM_NAME} \
  --zone=${ZONE} \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)')

curl http://${EXTERNAL_IP}:7860/health
echo "Open: http://${EXTERNAL_IP}:7860"
```

---

## 7. Configuration

### 7.1 VM Resources

| Setting | e2-medium | Notes |
|---|---|---|
| vCPU | 2 (shared) | Embedding + rerank are CPU-bound |
| RAM | 4 GB | ChromaDB + SQLite loaded in memory |
| Disk | 30 GB (pd-standard) | ~600MB image + data + OS |
| Network | Up to 2 Gbps | Sufficient for API calls |

> **Upgrade path**: If CPU becomes bottleneck →
> `gcloud compute instances set-machine-type ${VM_NAME} --zone=${ZONE} --machine-type=e2-standard-2`
> (requires VM stop/start)

### 7.2 config.yaml Overrides

The container uses the baked-in `config/config.yaml`. Key settings for cloud:

```yaml
# config/config.yaml — cloud-optimized values
server:
  host: "0.0.0.0"
  port: 7860                    # Docker exposes this port
  gradio_port: 7860
  cors_origins: ["*"]           # Restrict in production

mcp_bridge:
  revit_host: "localhost"       # N/A in cloud — MCP Bridge tab won't work
  revit_port: 18080             # Future: change to remote Revit host

proxy:
  enabled: false                # No proxy needed on GCP
```

### 7.3 Auto-Restart on VM Boot

The `restart: unless-stopped` policy in docker-compose handles container restarts.
To ensure Docker itself starts on boot:

```bash
sudo systemctl enable docker
```

---

## 8. CI/CD (Optional)

### GitHub Actions: Build → Push → Deploy via SSH

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy to GCP VM

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
  VM_NAME: revit-api-rag
  ZONE: asia-east1-b

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

      - name: Deploy to VM via SSH
        run: |
          gcloud compute ssh ${VM_NAME} --zone=${ZONE} --command="
            cd ~/revit-api-rag &&
            git pull origin main &&
            docker compose up -d --build
          "
```

---

## 9. Monitoring & Troubleshooting

### Logs

```bash
# SSH into VM
gcloud compute ssh ${VM_NAME} --zone=${ZONE}

# View container logs
docker compose logs -f
docker compose logs --tail=50

# System resources
htop
df -h
```

### Common Issues

| Symptom | Cause | Fix |
|---|---|---|
| OOM kill | 4GB RAM not enough for ChromaDB | Upgrade to e2-standard-2 (8GB) |
| Slow responses | Shared CPU throttled | Upgrade to e2-standard-2 (dedicated CPU) |
| "No API key configured" | .env missing or wrong | Check `.env` file, `docker compose restart` |
| MCP Bridge tab errors | Expected — no Revit in cloud | Ignore; Tab D is local-only |
| Cannot connect | Firewall rule missing | Check `gcloud compute firewall-rules list` |
| CORS errors | Origins restricted | Set `cors_origins` in config.yaml |
| Container won't start | Disk full | `docker system prune -a`, check `df -h` |

### Service Management

```bash
# Stop
docker compose down

# Restart
docker compose restart

# Rebuild after code change
docker compose up -d --build

# View resource usage
docker stats
```

---

## 10. Cost Estimate

| Resource | Spec | Monthly Cost |
|---|---|---|
| e2-medium VM | 2 vCPU / 4 GB, always on | ~$25/mo |
| Boot disk | 30 GB pd-standard | ~$1.2/mo |
| Static IP (if reserved) | 1 address | ~$3/mo (free when attached to running VM) |
| Egress | API responses | ~$0.12/GB |
| **Total (estimated)** | | **~$26-29/mo** |

> **External API costs** (not GCP):
> - OpenRouter: varies by model ($0.003-$0.06 per 1K tokens)
> - Cohere Rerank: free tier 1000 calls/month

> **Cost saving**: Stop VM when not in use:
> `gcloud compute instances stop ${VM_NAME} --zone=${ZONE}`
> (stopped VM: only disk cost ~$3/mo)

---

## 11. Security Checklist

- [ ] Restrict firewall `--source-ranges` to known IPs (not 0.0.0.0/0)
- [ ] Set `cors_origins` to specific domains in production
- [ ] Ensure `.env` file has `chmod 600` permissions
- [ ] Rotate API keys periodically
- [ ] Enable OS Login for SSH access control
- [ ] Set up budget alerts for the GCP project
- [ ] Consider adding nginx reverse proxy with HTTPS (Let's Encrypt)
- [ ] Consider fail2ban for SSH brute-force protection

---

## 12. Quick Reference Commands

```bash
# ── VM Management ──
gcloud compute ssh ${VM_NAME} --zone=${ZONE}
gcloud compute instances start ${VM_NAME} --zone=${ZONE}
gcloud compute instances stop ${VM_NAME} --zone=${ZONE}

# ── Service Management (on VM) ──
docker compose up -d --build      # Build & start
docker compose down               # Stop
docker compose restart             # Restart
docker compose logs -f             # Stream logs

# ── Update Code (on VM) ──
cd ~/revit-api-rag
git pull origin main
docker compose up -d --build

# ── Update API Keys (on VM) ──
nano .env                          # Edit keys
docker compose restart             # Apply

# ── Disk Cleanup (on VM) ──
docker system prune -a             # Remove unused images/containers

# ── Delete VM (cleanup) ──
gcloud compute instances delete ${VM_NAME} --zone=${ZONE}
gcloud compute firewall-rules delete allow-revit-rag
gcloud compute addresses delete revit-rag-ip --region=asia-east1
```

---

## 13. Future: MCP Bridge Remote Access

When the remote Revit host is ready, update `config/config.yaml`:

```yaml
mcp_bridge:
  revit_host: "<REMOTE_REVIT_IP_OR_DOMAIN>"   # Change from localhost
  revit_port: 18080
```

Then rebuild and restart:

```bash
docker compose up -d --build
```
