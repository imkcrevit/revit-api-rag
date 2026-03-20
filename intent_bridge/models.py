"""
Pydantic v2 data models — Intent Bridge runtime state + API request/response (Bilingual)
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SlotStatus(str, Enum):
    empty = "empty"
    filled = "filled"
    defaulted = "defaulted"
    inferred = "inferred"


class SessionStatus(str, Enum):
    active = "active"
    complete = "complete"
    need_followup = "need_followup"
    constraint_error = "constraint_error"
    cancelled = "cancelled"


class SlotSource(str, Enum):
    user_input = "user_input"
    default = "default"
    inferred = "inferred"
    follow_up = "follow_up"
    not_provided = "not_provided"


# ---------------------------------------------------------------------------
# Core state models
# ---------------------------------------------------------------------------

class SlotState(BaseModel):
    name: str
    value: Any = None
    status: SlotStatus = SlotStatus.empty
    source: SlotSource = SlotSource.not_provided
    display: str = ""

    def fill(self, value: Any, source: SlotSource = SlotSource.user_input, display: str = ""):
        self.value = value
        self.status = SlotStatus.filled
        self.source = source
        self.display = display or str(value)

    def set_default(self, value: Any, display: str = ""):
        self.value = value
        self.status = SlotStatus.defaulted
        self.source = SlotSource.default
        self.display = display or f"{value} (default)"

    def set_inferred(self, value: Any, display: str = ""):
        self.value = value
        self.status = SlotStatus.inferred
        self.source = SlotSource.inferred
        self.display = display or f"{value} (inferred)"


class IntentState(BaseModel):
    name: str = ""
    display_name: str = ""
    confidence: float = 0.0
    slots: dict[str, SlotState] = Field(default_factory=dict)

    def get_filled_slots(self) -> dict[str, Any]:
        return {
            name: slot.value
            for name, slot in self.slots.items()
            if slot.status != SlotStatus.empty
        }

    def get_missing_slots(self) -> list[str]:
        return [
            name for name, slot in self.slots.items()
            if slot.status == SlotStatus.empty
        ]


# ---------------------------------------------------------------------------
# Question queue item — for step-by-step wizard
# ---------------------------------------------------------------------------

class QuestionItem(BaseModel):
    """One question in the wizard queue."""
    slot: str
    text: str
    options: list[str] = Field(default_factory=list)     # display labels
    values: list[Any] = Field(default_factory=list)       # actual values to fill
    allow_custom: bool = False                             # allow free-text input
    enrich: str = "none"                                   # enrichment tag: none|level|host_pick|family_type:<cat>


class ActionStep(BaseModel):
    """One step in a multi-action plan."""
    step: int = 1
    intent: str = ""
    display_name: str = ""
    api_method: str = ""
    description: str = ""
    slots: dict[str, Any] = Field(default_factory=dict)
    questions: list[QuestionItem] = Field(default_factory=list)
    completed: bool = False
    filled_slots: dict[str, SlotState] = Field(default_factory=dict)


class MissingSlotInfo(BaseModel):
    slot: str
    question: str
    slot_type: str = "string"
    options: list[str] | None = None


class ConstraintViolation(BaseModel):
    slot: str
    message: str
    current_value: Any = None


# ---------------------------------------------------------------------------
# Turn response (API layer)
# ---------------------------------------------------------------------------

class TurnResponse(BaseModel):
    session_id: str
    turn: int
    status: SessionStatus
    intent: dict[str, Any] = Field(default_factory=dict)
    slots: dict[str, dict[str, Any]] = Field(default_factory=dict)
    missing: list[MissingSlotInfo] = Field(default_factory=list)
    constraint_violations: list[ConstraintViolation] = Field(default_factory=list)
    followup_question: str = ""
    summary: str = ""
    structured_output: dict[str, Any] | None = None
    # Wizard question
    current_question: QuestionItem | None = None
    questions_remaining: int = 0

    def to_card_data(self) -> dict:
        return {
            "intent": self.intent,
            "slots": self.slots,
            "missing": [m.model_dump() for m in self.missing],
            "violations": [v.model_dump() for v in self.constraint_violations],
            "status": self.status.value,
            "summary": self.summary,
            "output": self.structured_output,
        }


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

class SessionState(BaseModel):
    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = Field(default_factory=time.time)
    last_active: float = Field(default_factory=time.time)
    turn_count: int = 0
    status: SessionStatus = SessionStatus.active
    intent: IntentState = Field(default_factory=IntentState)
    history: list[dict[str, str]] = Field(default_factory=list)
    # Question queue (filled by LLM once, consumed one at a time)
    pending_questions: list[QuestionItem] = Field(default_factory=list)
    # Multi-action plan
    action_plan: list[ActionStep] = Field(default_factory=list)
    current_action_index: int = 0

    def touch(self):
        self.last_active = time.time()

    def add_message(self, role: str, content: str):
        self.history.append({"role": role, "content": content})
        self.touch()

    def is_expired(self, timeout_seconds: int = 600) -> bool:
        return (time.time() - self.last_active) > timeout_seconds

    def pop_question(self) -> QuestionItem | None:
        if self.pending_questions:
            return self.pending_questions.pop(0)
        return None

    def peek_question(self) -> QuestionItem | None:
        if self.pending_questions:
            return self.pending_questions[0]
        return None


# ---------------------------------------------------------------------------
# API request models
# ---------------------------------------------------------------------------

class ParseRequest(BaseModel):
    user_input: str
    language: str = "auto"


class SessionCreateRequest(BaseModel):
    language: str = "auto"


class TurnRequest(BaseModel):
    user_input: str


class AnswerRequest(BaseModel):
    """Answer a wizard question."""
    value: Any                # selected value (or custom text)
    option_index: int = -1    # which option was selected (-1 = custom)


class SlotUpdateRequest(BaseModel):
    slots: dict[str, Any]
