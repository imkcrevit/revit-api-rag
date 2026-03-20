# Revit API RAG — System Architecture

## High-Level Architecture

```mermaid
graph TB
    subgraph User["👤 User"]
        Browser["Browser<br/>(Gradio UI :7860)"]
    end

    subgraph Server["FastAPI + Gradio Server (Port 7860)"]
        direction TB
        GradioUI["Gradio Frontend"]
        FastAPI["FastAPI Router"]

        subgraph Tabs["Gradio Tabs"]
            MainTab["Main Tab<br/>Query → Generate → Execute"]
            ExplorerTab["API Explorer Tab<br/>Search → Rerank → CodeGen"]
            ToolTab["Tool Library Tab<br/>Solidified Tools"]
            ConnTab["Connection Tab<br/>Revit Status"]
        end

        subgraph Routers["API Routers"]
            BridgeRouter["/api/v1/bridge/*<br/>MCP Bridge"]
            IntentRouter["/api/v1/intent/*<br/>Intent Bridge"]
            LegacyRouter["/api/*<br/>Legacy Chat"]
        end
    end

    subgraph Pipeline["Pipeline Layer"]
        direction TB
        Retriever["RAGRetriever<br/>Two-Tier Search"]
        Reranker["Reranker<br/>Cohere v3.5"]
        Embedder["Embedder<br/>text-embedding-3-large"]
        LLMClient["LLM Client<br/>Claude / Gemini / DeepSeek"]
        QueryRewriter["Query Rewriter<br/>Gemini Flash"]
    end

    subgraph MCPBridge["MCP Bridge Layer"]
        direction TB
        CodeGen["CodeGenerator<br/>RAG → C# Code"]
        Interactive["IntentClassifier<br/>+ RevitQueryExecutor"]
        Sandbox["Sandbox<br/>Security Review"]
        ToolStore["ToolStore<br/>YAML Tool Library"]
        RevitClient["RevitClient<br/>TCP JSON-RPC"]
    end

    subgraph Storage["Data Layer"]
        direction LR
        ChromaDB["ChromaDB<br/>revit_api (27k vectors)<br/>revit_sdk (200+ vectors)"]
        SQLite["SQLite<br/>revit_api.db (27k records)<br/>revit_sdk.db (code samples)"]
        ToolYAML["YAML Tools<br/>mcp_bridge/tools/*.yaml"]
    end

    subgraph External["External Services"]
        direction LR
        OpenRouter["OpenRouter API<br/>LLM + Embedding"]
        Cohere["Cohere API<br/>Rerank v3.5"]
        RevitPlugin["Revit 2026 Plugin<br/>TCP :18080<br/>Roslyn Compiler"]
    end

    Browser --> GradioUI
    GradioUI --> Tabs
    GradioUI --> Routers

    BridgeRouter --> CodeGen
    BridgeRouter --> Interactive
    BridgeRouter --> ToolStore
    BridgeRouter --> RevitClient
    IntentRouter --> Interactive

    CodeGen --> Retriever
    CodeGen --> LLMClient
    CodeGen --> Sandbox

    Retriever --> Embedder
    Retriever --> QueryRewriter
    Retriever --> Reranker
    Retriever --> ChromaDB
    Retriever --> SQLite

    Embedder --> OpenRouter
    LLMClient --> OpenRouter
    QueryRewriter --> OpenRouter
    Reranker --> Cohere

    Sandbox --> RevitClient
    RevitClient --> RevitPlugin
    Interactive --> RevitPlugin
    ToolStore --> ToolYAML
    ToolStore --> RevitPlugin

    style User fill:#e1f5fe
    style Server fill:#f3e5f5
    style Pipeline fill:#e8f5e9
    style MCPBridge fill:#fff3e0
    style Storage fill:#fce4ec
    style External fill:#f5f5f5
```

## Data Flow: Query → Code → Execution

```mermaid
sequenceDiagram
    participant U as User (Browser)
    participant G as Gradio UI
    participant R as Router
    participant IC as IntentClassifier
    participant RET as RAGRetriever
    participant LLM as LLM (Claude)
    participant SB as Sandbox
    participant RC as RevitClient
    participant RP as Revit Plugin

    U->>G: "创建结构柱" (Create Column)
    G->>R: POST /generate-stream

    Note over R: Check Tool Library
    R->>R: match_tool(query)
    alt Tool Found (execution_count > 0)
        R-->>G: Use existing tool
        G-->>U: Show tool + parameter form
    else No Tool Match
        Note over R: RAG Pipeline
        R->>IC: classify_intent(query)
        IC-->>R: {type: "select_family", element: "column"}

        R->>RET: search(query, rewrite=true)
        Note over RET: Tier 0: Query Rewrite
        RET->>LLM: Extract API keywords
        LLM-->>RET: "FamilyInstance NewFamilyInstance StructuralType..."

        Note over RET: Tier 1: Vector + Keyword Search
        RET->>RET: ChromaDB query + SQLite LIKE
        Note over RET: Tier 2: Hydrate from SQLite
        Note over RET: Tier 3: Cohere Rerank
        RET-->>R: SearchResults (15 API + 3 SDK)

        R->>LLM: Generate C# (context + 23 rules)
        LLM-->>R: C# code (streaming SSE)
        R-->>G: SSE: thinking → code → done

        Note over G: Interactive Selection
        G->>RP: get_family_types(["OST_StructuralColumns"])
        RP-->>G: [{name: "W10x49", id: 12345}, ...]
        G-->>U: Show family type dropdown
        U->>G: Select "W10x49"

        G->>R: POST /execute
        R->>SB: review(code)
        SB-->>R: safe=true
        R->>RC: send_code(code, params)
        RC->>RP: TCP JSON-RPC
        RP-->>RC: {success: true, result: "Created column"}
        RC-->>R: RevitResponse
        R-->>G: Execution result
        G-->>U: Show result + solidify option
    end
```

## RAG Retrieval Pipeline Detail

```mermaid
flowchart LR
    subgraph Input
        Q["User Query<br/>创建墙体"]
    end

    subgraph Tier0["Tier 0: Query Rewrite"]
        QR["Gemini Flash<br/>Extract API Keywords"]
        EQ["Enriched Query<br/>Wall Wall.Create WallType<br/>Level Line CurveLoop"]
    end

    subgraph Tier1["Tier 1: Dual Search"]
        direction TB
        VS["Vector Search<br/>ChromaDB (top 30)"]
        KS["Keyword Search<br/>SQLite LIKE<br/>name/full_id scoring"]
        MG["Merge + Deduplicate<br/>Boost dual-hit items"]
    end

    subgraph Tier2["Tier 2: Hydrate"]
        HY["SQLite Fetch<br/>Full records by ID<br/>syntax, params, remark"]
    end

    subgraph Tier3["Tier 3: Rerank"]
        RR["Cohere rerank-v3.5<br/>Cross-encoder scoring<br/>30 → 15 results"]
    end

    subgraph Output
        RS["SearchResults<br/>15 API docs<br/>3 SDK examples"]
    end

    Q --> QR --> EQ
    EQ --> VS
    Q --> KS
    VS --> MG
    KS --> MG
    MG --> HY --> RR --> RS

    style Tier0 fill:#e3f2fd
    style Tier1 fill:#f1f8e9
    style Tier2 fill:#fff8e1
    style Tier3 fill:#fce4ec
```

## Keyword Search Scoring Algorithm

```
Score Formula (per token):
  +5.0  token found in API name
  +3.0  bonus: token starts a name segment (e.g. "Wall" in "Wall.Create")
  +1.5  token found in full_id only
  +0.5  token found in summary

Penalties:
  ×0.3  name starts with "BuiltInFailures."
  ×0.6  name starts with "BuiltInParameter."
  ×0.3  name length > 80 chars
  ×0.5  name length > 60 chars
  ×0.7  name length > 40 chars
  ×0.5  name has 3+ dots (deeply nested)

Bonus:
  ×2.0  ALL search tokens found in name

Example: "create wall"
  Wall.Create Method         → (5+3 + 5+3) × 2.0 = 32.0  ✅ Top result
  WallFoundation.Create      → (5+3 + 5+3) × 2.0 = 32.0  ✅ Top result
  BuiltInFailures.Wall...    → (5+3 + 5+3) × 0.5 × 0.3 = 4.8  ⬇ Pushed down
```

## Component Directory Map

```
revit-api-rag/
├── server/                          # Web Server Layer
│   ├── main.py                      # Entry point: uvicorn + FastAPI + Gradio
│   ├── app/
│   │   ├── deps.py                  # Singletons: config, retriever, session
│   │   └── session.py               # Session store (2hr TTL)
│   ├── frontend/
│   │   └── gradio_app.py            # Gradio UI builder (4 tabs)
│   └── routers/
│       └── chat.py                  # Legacy /api/chat endpoint
│
├── pipeline/                        # Data & ML Pipeline
│   ├── retriever.py                 # RAGRetriever (vector + keyword + rerank)
│   ├── reranker.py                  # Cohere rerank wrapper
│   ├── llm_client.py                # OpenRouter LLM client (streaming)
│   ├── embedder/
│   │   ├── providers.py             # Embedding provider factory
│   │   └── embed.py                 # Batch embedding + ChromaDB storage
│   ├── api_parser/                  # Revit CHM → SQLite
│   │   ├── parse_chm.py            # HTML extraction from CHM
│   │   └── quality_agent.py        # LLM-based quality filtering
│   └── sdk_parser/                  # SDK source → Golden code
│       ├── extract.py              # tree-sitter C# parsing
│       └── quality_agent.py        # LLM golden code generation
│
├── mcp_bridge/                      # Code Generation & Execution
│   ├── router.py                    # /api/v1/bridge/* endpoints
│   ├── code_generator.py            # RAG context → C# code (23-rule prompt)
│   ├── revit_client.py              # TCP JSON-RPC to Revit plugin
│   ├── sandbox.py                   # Static security review
│   ├── interactive.py               # Intent classification + Revit queries
│   ├── tool_store.py                # Tool solidification (YAML)
│   ├── tools/                       # Saved tool YAML files
│   └── frontend/
│       ├── app.py                   # Main Gradio tab (query → execute)
│       └── api_explorer.py          # API Explorer tab
│
├── intent_bridge/                   # Intent Parsing (Module 2)
│   ├── router.py                    # /api/v1/intent/* endpoints
│   ├── llm_adapter.py              # LLM wrapper for intent parsing
│   ├── slot_engine.py              # Parameter slot management
│   └── orchestrator.py             # Multi-turn conversation state
│
├── revit_plugin/                    # C# Revit 2026 Plugin
│   ├── RevitMCPPlugin.cs           # TCP server (:18080)
│   ├── RevitMCPCommandSet.cs       # 23 predefined commands
│   └── RevitMCPPlugin.addin        # Revit addon manifest
│
├── config/
│   └── config.yaml                  # All configuration
│
└── data/
    ├── chromadb/                    # Vector embeddings
    │   ├── revit_api/              # 27k API doc vectors
    │   └── revit_sdk/              # 200+ SDK code vectors
    └── sqlite/
        ├── revit_api.db            # Full API documentation
        └── revit_sdk.db            # SDK golden code samples
```

## External Service Dependencies

| Service | Protocol | Purpose | Key Env Var |
|---------|----------|---------|-------------|
| OpenRouter | HTTPS | LLM (Claude/Gemini/DeepSeek) + Embedding | `OPENROUTER_API_KEY` |
| Cohere | HTTPS | Rerank v3.5 (optional) | `COHERE_API_KEY` |
| Revit Plugin | TCP :18080 | Code execution + element queries | N/A (localhost) |

## Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | Gradio 6.x (Python) |
| Backend | FastAPI + Uvicorn |
| LLM Gateway | OpenRouter (multi-provider) |
| Embedding | OpenAI text-embedding-3-large (3072d) |
| Vector DB | ChromaDB (persistent) |
| Content DB | SQLite |
| Reranking | Cohere rerank-v3.5 |
| Revit Plugin | C# / .NET 8 / Roslyn |
| Communication | TCP JSON-RPC 2.0 / SSE |
