**English** | [中文](./README.md)

# Acknowledgements and Source Attribution

This project references or directly uses code and designs from the following open-source projects, with gratitude:

### 1. RevitSdkSamples — Jeremy Tammik

The training data (SDK sample code) for this RAG project comes from Jeremy Tammik's [RevitSdkSamples](https://github.com/jeremytammik/RevitSdkSamples) project.

- **License**: MIT License
- **Usage**: Extracted C# sample code for RAG retrieval and Golden Code generation
- For original copyright and license details, please refer to the `NOTICE` and `LICENSE` files in the project root

### 2. mcp-servers-for-revit — sparx-fire & mcp-servers-for-revit

The Revit plugin (`mcp_bridge` module) in this project is based on the Revit Plugin and CommandSet components from the [mcp-servers-for-revit](https://github.com/mcp-servers-for-revit/mcp-servers-for-revit) project.

- **License**: MIT License — Copyright (c) 2026 sparx-fire, mcp-servers-for-revit
- **Usage**: Directly uses their compiled Revit plugin DLLs (RevitMCPPlugin, RevitMCPCommandSet) as the Revit-side TCP Socket service and command executor
- **Referenced content**:
  - `plugin/Core/SocketService.cs` — TCP JSON-RPC 2.0 communication protocol
  - `commandset/Commands/ExecuteDynamicCode/` — Roslyn dynamic code compilation and execution
  - `command.json` — 23 preset command definitions
- **Original project**: Fork of [revit-mcp](https://github.com/mcp-servers-for-revit/revit-mcp)

---

# Revit API RAG

A RAG (Retrieval-Augmented Generation) system based on Revit API documentation and SDK sample code, helping developers quickly generate Revit C# plugin code.

## 2025.03 Major Update

### Project Restructuring
- Legacy code (AutoDL local training) has been moved to the [`legacy/`](./legacy/) directory — **[View legacy documentation and logic diagrams](./legacy/README.md)**
- The new pipeline has been migrated to **Google Colab**, leveraging free GPU and Google Drive storage
- Project structure reorganized, separating `pipeline/` (training) and `server/` (deployment)

### Architecture Overview

```
revit-api-rag/
├── pipeline/              # Data processing & training (runs on Colab)
│   ├── run_all.ipynb      # Main training notebook
│   ├── api_parser/        # API documentation parsing
│   │   ├── parse_chm.py   # CHM HTML → structured data
│   │   └── quality_agent.py  # LLM quality pruning
│   ├── sdk_parser/        # SDK code parsing
│   │   ├── extract.py     # .cs source extraction
│   │   └── quality_agent.py  # Golden code generation
│   ├── embedder/          # Vectorization
│   │   └── embed.py       # SQLite → ChromaDB
│   ├── retriever.py       # Two-layer retriever
│   └── llm_client.py      # LLM client (OpenRouter)
├── mcp_bridge/            # Intent Bridge — Revit interaction bridge layer
│   ├── router.py          # FastAPI routes (SSE streaming, health check, etc.)
│   ├── code_generator.py  # RAG context assembly + LLM code generation
│   ├── interactive.py     # LLM intent classification + Revit data queries
│   ├── revit_client.py    # TCP JSON-RPC 2.0 client
│   ├── client_pool.py     # Connection pool (singleton + auto-reconnect)
│   ├── sandbox.py         # C# code security review
│   ├── tool_store.py      # Solidified Tool persistence
│   └── frontend/          # Gradio Web UI
│       └── app.py         # Multi-step interaction interface (Thinking + Pipeline + Code Execution)
├── revit_plugin/          # Revit 2026 plugin (C# / .NET 8)
│   ├── plugin/            # RevitMCPPlugin — TCP Socket service
│   └── commandset/        # RevitMCPCommandSet — 23 preset commands
├── config/                # Configuration
│   └── config.yaml
├── data/                  # Generated database files
│   ├── sqlite/            # SQLite (API + SDK structured data)
│   └── legacy_db/         # Legacy databases (kept for reference)
├── server/                # Web service (for deployment)
├── legacy/                # Legacy code (AutoDL era)
└── docs/                  # Documentation
```

## Data Processing Pipeline

> For detailed logic diagrams and prompt designs of the V1 legacy pipeline, see [legacy/README.md](./legacy/README.md)

### API Documentation Processing

![RevitAPI Parsing Pipeline](./docs/images/RevitAPI.png)

```
Revit 2026 API CHM
    ↓ 7z extraction
HTML files (~27000+ pages)
    ↓ parse_chm.py parsing
Structured data (name, summary, syntax, parameters, remarks...)
    ↓ quality_agent.py (Gemini Flash pruning)
revit_api.db (SQLite)
    ↓ embed.py
ChromaDB vector store (API)
```

- **Parsing**: Extracts class names, method signatures, parameters, remarks, and other structured information from HTML files decompressed from the CHM
- **Pruning**: Uses Gemini Flash to clean low-quality/redundant entries, retaining high-quality API references
- **Storage**: SQLite stores the full structured data; ChromaDB stores semantic vectors of `name + summary`

![Database Structure](./docs/images/RevitEembeddingDatabse.png)

### SDK Code Processing

![RevitSDK Parsing Pipeline](./docs/images/RevitSDK.png)

```
Revit SDK Samples (~200+ projects)
    ↓ extract.py (tree-sitter parsing)
.cs source → class/method extraction
    ↓ quality_agent.py (Gemini generates Golden Code)
SDK golden code (JSON)
    ↓ SQLite storage
revit_api.db (sdk_code table)
    ↓ embed.py
ChromaDB vector store (Code)
```

- **Extraction**: Uses tree-sitter to parse C# source code, extracting class definitions, method signatures, and key code blocks
- **Golden Code**: LLM reads the project README + source code to generate refined example code
- **Storage**: SQLite stores golden code + metadata; ChromaDB stores code semantic vectors

### Embedding Strategy
- **API vectorization**: `name + summary` → OpenRouter Embedding API → ChromaDB
- **Code vectorization**: `golden_code summary` → OpenRouter Embedding API → ChromaDB
- **Retrieval**: Query → ChromaDB semantic search (top_k) → SQLite full-text lookup
- **Reranking**: Optional rerank model for result refinement

## RAG Retrieval & Code Generation

![RAG Main Workflow](./docs/images/workflow.png)

```
User query: "Create a structural column"
    ↓ Query Rewriting (LLM extracts API keywords)
Rewritten: "structural column, NewFamilyInstance, FamilySymbol, Level"
    ↓ ChromaDB semantic search
API results: 15 items  |  SDK results: 5 items
    ↓ Context assembly
Prompt = API Reference + SDK Code + User Query
    ↓ LLM streaming generation (Gemini Flash / Claude)
Output: C# plugin code (concise mode / full mode)
```

![V2 Complete Workflow](./docs/images/RAG-Workflow-Update.jpg)

## Release Files

### RAG Database — [v2.0-data](https://github.com/imkcrevit/revit-api-rag/releases/tag/v2.0-data)

Four database files produced after training:

| File | Size | Description |
|------|------|-------------|
| `revit_api.db` | 33 MB | API structured data (name, summary, syntax, parameters, remarks...) |
| `revit_sdk.db` | 932 KB | SDK Golden Code + metadata |
| `chromadb_api.tar.gz` | 286 MB | API semantic vector store (27596 embeddings) |
| `chromadb_code.tar.gz` | 1.8 MB | SDK code semantic vector store (153 embeddings) |

After downloading, place them in the corresponding directories:
```
data/
├── sqlite/
│   ├── revit_api.db          # API structured data
│   └── revit_sdk.db          # SDK golden code
├── chromadb_api/              # Extract chromadb_api.tar.gz
└── chromadb_code/             # Extract chromadb_code.tar.gz
```

### Revit Plugin — [v0.2](https://github.com/imkcrevit/revit-api-rag/releases/tag/v0.2)

| File | Size | Description |
|------|------|-------------|
| `revit-mcp-plugin-v0.2-R2026.zip` | 20 MB | Revit 2026 plugin (DLL + .addin + CommandSet) |

Installation:
```
1. Download revit-mcp-plugin-v0.2-R2026.zip
2. Extract to %APPDATA%\Autodesk\Revit\Addins\2026\
3. Restart Revit 2026
```

Extracted directory structure:
```
%APPDATA%\Autodesk\Revit\Addins\2026\
├── mcp-servers-for-revit.addin          # Plugin registration manifest
└── revit_mcp_plugin/                    # Main plugin directory
    ├── RevitMCPPlugin.dll               # Main plugin (TCP Socket service)
    ├── RevitMCPSDK.dll                  # MCP SDK
    ├── Newtonsoft.Json.dll              # JSON serialization
    └── Commands/
        ├── commandRegistry.json         # Command registry
        └── RevitMCPCommandSet/          # Command set
            ├── command.json             # 23 preset command definitions
            └── 2026/                    # Revit 2026 build artifacts
                ├── RevitMCPCommandSet.dll
                └── ...(dependency DLLs)
```

## Usage Guide

For detailed steps and demonstrations, see **[Usage Guide — Intent Bridge Interactive Operations](./docs/usage-guide.en.md)**, including:

- **Single-step command execution** (Direct) — Query, modify, delete, and other operations that directly generate and execute code
- **Multi-step interaction: Family type selection** (Select Family) — Creating walls/columns/beams/slabs, first querying available family types from Revit for user selection
- **Multi-step interaction: Host selection + family type** (Select Both) — Creating windows/doors, first triggering Revit selection mode for users to pick host walls
- **Thinking process** — Real-time streaming display of the LLM reasoning chain
- **Pipeline progress log** — Real-time display of 9-stage progress
- **Solidified Tools** — Tool solidification and reuse

## Requirements

- **Training environment**: Google Colab (free tier is sufficient)
- **Revit version**: 2026
- **LLM**: OpenRouter (Gemini Flash / Claude / GPT)
- **Embedding**: OpenRouter Embedding API
- **Storage**: Google Drive (ChromaDB + SQLite ~100MB)

## Quick Start

### Training (Colab)
1. Open `pipeline/run_all.ipynb` in Google Colab
2. Run all cells in order
3. Generated database files are automatically saved to Google Drive

### Deployment (Local / GCP)
```bash
# Install dependencies
pip install -r requirements-server.txt

# Start the service
python -m server.main
```

---

## Changelog

### 2026.03 — V0.2 Intent Bridge + Revit Interaction

**Gradio Multi-step Interaction UI**
- Brand new 4-step workflow interface: Input → Select Options → Review Code → Execute
- Thinking mode: Real-time streaming display of LLM reasoning process `<thinking>`, collapsible
- Pipeline progress log: Query Rewrite → Embedding → Vector Search → Hydrating → Combining → Assembling → LLM Generating → Extracting → Security Review, displayed step by step with color markers
- Real-time JS timer, continuously incrementing during streaming generation
- Fixed-height scrollable panels to prevent page jumping

**Intent Bridge — Multi-step / Single-step Parsing**
- LLM intent classifier (`interactive.py`): Automatically identifies user instruction types
  - **Single-step (Direct)**: Query, modify, delete, and other operations directly generate code
  - **Multi-step (Select Family)**: Creating walls/columns/beams/slabs requires family type selection — first queries available family types from Revit for user selection, then generates code
  - **Multi-step (Select Both)**: Creating windows/doors requires host selection + family type selection — first triggers Revit selection mode for users to pick host elements
- Supports extracting coordinate parameters (2D/3D) from user instructions
- Keyword fallback mechanism: Automatically switches to rule-based matching when LLM is unavailable

**Revit Real-time Interaction**
- TCP JSON-RPC 2.0 protocol connecting to Revit 2026 plugin (port 18080)
- Rich connection status display: version, protocol, endpoint, latency, timestamp
- Query family types, levels, selected elements
- Trigger Revit PickObject interactive selection mode
- Dynamic code execution + result echo

**SSE Streaming Code Generation**
- `/generate-stream` endpoint: SSE protocol for token-by-token streaming output
- 9-stage pipeline progress events + token events + completion events
- Thinking / Code separation via regex extraction
- Security review: Automatic scanning for dangerous API calls before code execution

**Solidified Tools**
- Successfully executed code can be solidified into reusable tools with one click
- Tool parameters are dynamically queried from Revit (family type lists, level lists, etc.)
- API Explorer panel for viewing and managing solidified tools

**Revit Plugin Release**
- Compiled Revit 2026 Release DLLs, packaged and uploaded to [GitHub Release v0.2](https://github.com/imkcrevit/revit-api-rag/releases/tag/v0.2)
- Includes RevitMCPPlugin + RevitMCPCommandSet (23 preset commands) + .addin registration file

### 2025.03 — V2 Major Restructuring
- Legacy code moved to [`legacy/`](./legacy/) — [View legacy documentation](./legacy/README.md)
- Pipeline migrated to Google Colab
- Added SDK Pipeline V2 (tree-sitter + LLM golden code)
- Added Quality Agent (API + SDK data pruning)
- Added Query Rewriting
- Added streaming code generation (concise mode / full mode)

### 2025.10.23 — SDK Embedding ([Detailed documentation](./legacy/README.md#2025-10-23-update))
- Used tree-sitter to extract SDK code blocks
- LLM generates clean code JSON
- Data stored in SQLite

### Initial Version — API RAG ([Detailed documentation](./legacy/README.md#revit-api-rag-v1))
- Revit API CHM parsing → SQLite + ChromaDB
- SDK code parsing → ChromaDB
- Basic RAG retrieval + code generation
