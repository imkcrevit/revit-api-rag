# Intent Bridge — Architecture & Implementation Document v0.2

## Overview

Intent Bridge is the natural language intent parsing and structured output middleware for the Revit API Assistant. It replaces the legacy `text2revit` module with a production-grade system featuring RAG-driven parameter extraction, multi-action decomposition, bilingual support, and a wizard-style UI.

**Goal**: Architects describe operations in Chinese or English → system parses intent → confirms via interactive card UI → outputs structured JSON for downstream Revit execution.

---

## Module Structure

```
intent_bridge/
├── __init__.py
├── config.yaml                  # LLM model configuration (primary + fallback)
├── llm_adapter.py               # Async-first LLM client with retry & proxy
├── models.py                    # Pydantic v2 data models
├── slot_engine.py               # Core runtime (orchestrator, RAG, prompts)
├── router.py                    # FastAPI routes (/api/v1/intent/*)
├── schemas/
│   └── intent_slots.yaml        # 8 intent definitions with slot schemas
├── frontend/
│   ├── __init__.py
│   └── app.py                   # Gradio intent card UI (Tab C)
└── tests/
    ├── __init__.py
    └── test_e2e.py              # End-to-end test cases
```

**Modified existing files:**
- `server/main.py` — `include_router(intent_router)`
- `server/frontend/gradio_app.py` — Added Tab C "Intent Bridge"

---

## 1. RAG-Driven Parameter Extraction

### Problem
The legacy system used hardcoded action definitions for 6 operations. Parameters were fixed and couldn't adapt to real Revit API signatures.

### Solution
Intent Bridge queries a SQLite database containing actual Revit API documentation to retrieve method signatures, parameter types, and descriptions.

**Implementation** (`slot_engine.py` → `_query_api_by_method()`):

```python
def _query_api_by_method(method_name: str) -> str:
    """Query SQLite for real Revit API docs by method name."""
```

- Searches `chunks` table with `LIKE` matching on method name
- Returns formatted API documentation including parameter signatures
- Injected into the LLM prompt so the model knows exact parameter names and types
- Falls back to YAML schema definitions if no RAG results found

**Flow**: User input → Intent classification → API method identified → RAG retrieval of method docs → LLM generates questions with correct parameter types

---

## 2. Single-Action vs Multi-Action Decomposition

### Single Action
Standard flow for operations that map to one API call (e.g., "create a wall"):

```
User input → LLM analysis → Question queue → User answers → Structured output
```

### Multi-Action (Composite)
For operations requiring multiple sequential API calls (e.g., "create a room" requires enclosed walls first):

```
User input → LLM detects composite → Decomposes into action_plan
→ Step 1: Ask parameters → Fill slots → Mark complete
→ Step 2: Ask parameters → Fill slots → Mark complete
→ ... → All steps complete → Aggregate output
```

**Key components:**

| Component | Purpose |
|-----------|---------|
| `ActionStep` model | Stores per-step intent, api_method, questions, filled_slots |
| `_init_action_plan()` | Parses LLM's `action_plan` array, creates ActionStep objects |
| `_load_action_step()` | Loads current step's questions into the queue |
| `_advance_action_plan()` | Marks step complete, moves to next step |
| `_complete_action_plan()` | Aggregates all steps' outputs, generates summary |

**LLM output format for composite actions:**
```json
{
  "intent": "composite",
  "action_plan": [
    {
      "step": 1,
      "intent": "create_wall",
      "display_name": "创建墙体",
      "api_method": "Wall.Create",
      "description": "Create enclosing walls",
      "questions": [...]
    },
    {
      "step": 2,
      "intent": "create_room",
      "api_method": "Document.Create.NewRoom",
      "questions": [...]
    }
  ]
}
```

---

## 3. Ambiguity Detection & Resolution

The LLM prompt includes a mandatory ambiguity detection table for Chinese architectural terms:

| User term | Possible meanings |
|-----------|-------------------|
| 背面 | 北面 (north) / 背面 (back side) |
| 前面 | 南面 (south) / 前面 (front side) |
| 左边/右边 | Relative direction — needs clarification |
| 隔墙 | Partition wall / Separation wall |
| 加 | Create new / Add to existing |

**Behavior**: When ambiguous terms are detected, the system adds a clarification question before proceeding with parameter extraction. This prevents incorrect assumptions from propagating through the slot-filling process.

---

## 4. Bilingual Support (Chinese / English)

### Language Detection
The LLM prompt includes a language detection rule:

> Detect the user's language from their input. If Chinese → all questions, options, and summaries in Chinese. If English → all in English. Never mix languages (no raw English parameter names in Chinese output).

### UI Bilingual Labels
All interactive elements show both languages:
- Buttons: "发送 Send", "清除 Clear", "确认执行 Confirm", "确定 OK"
- Placeholders: Bilingual hints for input fields
- Status text: "已确认 Confirmed", "剩余 Remaining"

### Display Text Storage
When users answer questions, the system stores both:
- **Raw value**: The actual parameter value (e.g., `"NonStructural"`)
- **Display text**: The user-facing label (e.g., `"非承重墙 NonStructural"`)

This prevents raw English enum values from appearing in the Chinese summary.

---

## 5. Type Validation & Parameter Guidance

### Strict Type Rules
The prompt enforces that all parameter values must be Revit-executable types:

| Parameter type | Expected format | Example |
|---------------|-----------------|---------|
| Host element | `ElementId` (integer) | `12345` |
| Location | `XYZ` coordinates | `1000, 500, 0` |
| Dimensions | Numeric (mm) | `2400` |
| Type/Family | Revit type name | `"Generic - 200mm"` |
| Enum values | API enum string | `"NonStructural"` |

### ElementId Guidance
Since the web UI is not connected to a live Revit instance:
- System explains that users must look up ElementId in Revit
- Provides contextual placeholder: `"输入 ElementId (例: 12345) / Enter ElementId for host_element"`
- Rejects non-numeric inputs like "墙体背面" with correction guidance

### Every Parameter Must Be Asked
No silent defaults. The LLM must generate a question for every parameter, including those with common defaults. Users explicitly confirm or override each value.

---

## 6. Prompt Engineering

### Prompt Architecture (`_ANALYZE_PROMPT`)

The master prompt is written entirely in English for consistency, with language-adaptive output rules. Structure:

```
1. LANGUAGE RULE — Detect and match user's language
2. AVAILABLE INTENTS — Listed from schema registry
3. API DOCUMENTATION — Injected from RAG query
4. AMBIGUITY DETECTION TABLE — Mandatory check
5. PARAMETER RULES (Rules 1-6):
   - Rule 1: Ask about EVERY parameter
   - Rule 2: Values must be Revit-executable types
   - Rule 3: Host element needs ElementId explanation
   - Rule 4: Type/Family parameters are mandatory
   - Rule 5: Question format (3-6 options + "其他/Other")
   - Rule 6: Question ordering priority
6. MULTI-ACTION DECOMPOSITION — Composite action detection
7. OUTPUT JSON SCHEMA — Strict format specification
```

### Prompt Injection Points
Dynamic content injected at runtime:
- `{schema_block}` — Available intent names and descriptions from YAML
- `{api_docs}` — RAG-retrieved Revit API documentation
- `{user_input}` — The user's natural language request
- `{history}` — Conversation history for multi-turn context

---

## 7. Question Queue Architecture

### One LLM Call, Multiple Questions
The first user message triggers a single LLM call that returns ALL questions for the identified intent. Subsequent answer interactions are instant (no LLM calls).

```
Turn 1 (LLM): User input → Intent + all questions generated
Answer 1 (instant): User picks option → Next question from queue
Answer 2 (instant): User picks option → Next question from queue
...
Final answer (LLM): All slots filled → LLM generates summary
```

### Question Format
Each question includes:
- `slot`: Parameter name being asked about
- `text`: Human-readable question in detected language
- `options`: 3-6 predefined choices + "其他/Other"
- `values`: Actual API values corresponding to each option
- `allow_custom`: Whether free-text input is allowed

### Custom Input Flow
When user clicks "其他/Other":
1. Question text updates with custom input prompt
2. Input textbox becomes `interactive=True` with contextual placeholder
3. User types value and submits
4. System processes as a custom answer (`option_index: -1`)

> **Gradio Bug Workaround**: `visible=False → visible=True` doesn't render in Gradio. Solution: keep component always `visible=True`, toggle `interactive` instead.

---

## 8. LLM Adapter

### Configuration (`config.yaml`)
```yaml
llm:
  primary:
    model: "google/gemini-3-flash-preview"
    temperature: 0.1
  fallback:
    model: "openai/gpt-5.3-codex"
    temperature: 0.1
  max_retries: 2
  timeout_seconds: 60
```

### Features
| Feature | Implementation |
|---------|---------------|
| Primary/Fallback | Auto-switch on 429/5xx/403 errors |
| Retry logic | Max 2 retries with model switching |
| Proxy support | Reads `HTTPS_PROXY`/`HTTP_PROXY` from env |
| Async-first | `complete_async()` for all orchestrator calls |
| JSON extraction | Strip markdown fences → `json.loads` → regex fallback |
| Logging | Prompt length, response length, latency, model used |

### Proxy Support
Resolves region-blocking (403 "not available in your region"):
```python
self._proxy = (os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
               or os.getenv("https_proxy") or os.getenv("http_proxy") or None)
```
Passed to both `httpx.AsyncClient(proxy=...)` and `httpx.Client(proxy=...)`.

---

## 9. Conversation Orchestrator

### State Machine (`ConversationOrchestrator`)

```
                    ┌─────────────┐
                    │  User Input  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  LLM Parse   │ ← RAG docs injected
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │                         │
     ┌────────▼────────┐     ┌─────────▼─────────┐
     │  Single Action   │     │  Composite Action  │
     │  (intent != comp)│     │  (intent==composite)│
     └────────┬────────┘     └─────────┬─────────┘
              │                         │
     ┌────────▼────────┐     ┌─────────▼─────────┐
     │ Load Questions   │     │ _init_action_plan() │
     │ into Queue       │     │ Load Step 1         │
     └────────┬────────┘     └─────────┬─────────┘
              │                         │
     ┌────────▼────────────────────────▼────────┐
     │          Question Loop (instant)          │
     │  pop question → user answers → fill slot  │
     │  repeat until queue empty                 │
     └────────────────────┬─────────────────────┘
                          │
              ┌───────────┼───────────┐
              │                       │
     ┌────────▼────────┐   ┌─────────▼─────────┐
     │ _complete()      │   │ _advance_action()  │
     │ LLM summary      │   │ Next step or       │
     │ Structured output │   │ _complete_plan()   │
     └─────────────────┘   └───────────────────┘
```

### Key Methods

| Method | Async | Purpose |
|--------|-------|---------|
| `process_turn()` | Yes | First user message → LLM → route to single/composite |
| `answer_question()` | Yes | Fill slot from user answer → next question or complete |
| `_complete()` | Yes | All slots filled → LLM summary → structured output |
| `_init_action_plan()` | No | Parse composite LLM response into ActionStep list |
| `_load_action_step()` | No | Load step's questions into session queue |
| `_advance_action_plan()` | Yes | Move to next step or complete all |
| `_complete_action_plan()` | Yes | Aggregate all steps → LLM summary |
| `_llm_summary()` | Yes | Generate natural language summary from filled slots |
| `update_slots_directly()` | Yes | Direct slot update from card UI |

---

## 10. API Endpoints

**Prefix**: `/api/v1/intent/`

| Method | Path | LLM Call | Purpose |
|--------|------|----------|---------|
| POST | `/parse` | Yes | Stateless single-turn parse |
| POST | `/session` | No | Create new session |
| POST | `/session/{id}/turn` | Yes | User text → intent analysis |
| POST | `/session/{id}/answer` | Sometimes* | Answer wizard question |
| GET | `/session/{id}` | No | Query session state |
| POST | `/session/{id}/slots` | Sometimes* | Direct slot update |
| GET | `/schemas` | No | List all available intents |

*LLM call only on final answer (summary generation) or when advancing action plan steps.

---

## 11. Gradio Frontend

### Layout (Tab C: Intent Bridge)

```
┌─────────────────────────────────────────────────────┐
│  LEFT (scale=3)              │  RIGHT (scale=2)      │
│  ┌─────────────────────────┐ │  ┌──────────────────┐ │
│  │                         │ │  │ 🎯 Intent  [95%] │ │
│  │    Chat History         │ │  ├──────────────────┤ │
│  │    (Chatbot, h=420)     │ │  │ ✅ wall_type:... │ │
│  │                         │ │  │ ⚙️ height: 2400  │ │
│  │                         │ │  │ ❓ location: —   │ │
│  ├─────────────────────────┤ │  ├──────────────────┤ │
│  │ [Input box    ] [Send]  │ │  │ 💬 Question text │ │
│  │               [Clear]   │ │  │ [Option1][Opt2]  │ │
│  └─────────────────────────┘ │  │ [Option3][其他]  │ │
│                              │  │ [Custom input]OK │ │
│                              │  ├──────────────────┤ │
│                              │  │ [✅ Confirm]     │ │
│                              │  │ ▸ JSON Output    │ │
│                              │  └──────────────────┘ │
└─────────────────────────────────────────────────────┘
```

### Slot Status Icons
| Icon | Status | Meaning |
|------|--------|---------|
| ✅ | filled | User provided value |
| ⚙️ | defaulted | System default applied |
| 💡 | inferred | Inferred from context |
| ❓ | empty | Not yet provided |

### Gradio Workarounds
1. **CSS injection**: `gr.HTML('<style>...')` instead of `css=` parameter (Gradio 6 deprecation)
2. **Visibility bug**: Toggle `interactive` instead of `visible` for custom input
3. **Re-render bug**: Use `gr.update(value=..., visible=...)` instead of `gr.Markdown(value=..., visible=...)` for forced UI updates
4. **Button text matching**: Option handler matches displayed button text to options list (no pre-bound index)

---

## 12. Data Models

### Core Models (`models.py`)

```
SessionState
├── session_id, created_at, last_active, turn_count
├── status: SessionStatus (active|complete|need_followup|constraint_error|cancelled)
├── intent: IntentState
│   ├── name, display_name, confidence
│   └── slots: dict[str, SlotState]
│       ├── name, value, status, source, display
│       ├── fill(), set_default(), set_inferred()
├── history: list[dict]
├── pending_questions: list[QuestionItem]
│   ├── slot, text, options, values, allow_custom
├── action_plan: list[ActionStep]    ← Multi-action
│   ├── step, intent, display_name, api_method
│   ├── description, slots, questions
│   ├── completed, filled_slots
└── current_action_index: int

TurnResponse
├── session_id, turn, status
├── intent, slots, missing, constraint_violations
├── followup_question, summary, structured_output
├── current_question: QuestionItem
└── questions_remaining: int
```

---

## 13. Schema Registry

### Intent Definitions (`schemas/intent_slots.yaml`)

8 intents defined with full slot specifications:

| Intent | API Method | Description |
|--------|-----------|-------------|
| `create_wall` | `Wall.Create` | Create wall element |
| `create_floor` | `Floor.Create` | Create floor element |
| `create_door` | `FamilyInstance.Create` | Place door in wall |
| `create_window` | `FamilyInstance.Create` | Place window in wall |
| `create_room` | `Document.Create.NewRoom` | Create room (may need walls) |
| `modify_element` | `Element.Modify` | Modify existing element |
| `query_element` | `Element.Query` | Query element properties |
| `delete_element` | `Document.Delete` | Delete element |

Each slot definition includes:
- `type`: Data type (string, float, enum, ElementId, XYZ)
- `required`: Whether mandatory
- `default_strategy`: How to apply defaults
- `ask_if_missing`: Whether to prompt user
- `aliases`: Alternative names (Chinese/English)
- `constraints`: Validation rules
- `examples`: Example values for prompt context

---

## Key Design Decisions

1. **RAG over hardcoded schemas**: Real Revit API docs ensure parameter accuracy
2. **One LLM call per turn**: Questions pre-generated, answers are instant
3. **Async-first**: All orchestrator methods are async for non-blocking operation
4. **Independent session store**: Separate from RAG module's session management
5. **English prompts, bilingual output**: Prompt consistency + user language adaptation
6. **No silent defaults**: Every parameter explicitly confirmed by user
7. **Composite action detection**: LLM identifies multi-step operations automatically
8. **LLM-generated summaries**: Natural language instead of raw key=value output
