import asyncio, json
from intent_bridge.slot_engine import ConversationOrchestrator
from intent_bridge.llm_adapter import LLMAdapter
from intent_bridge.models import SessionState
llm = LLMAdapter()
orch = ConversationOrchestrator(llm=llm)
s = SessionState()
r = asyncio.run(orch.process_turn("创建两个结构柱", s))
print("STATUS:", r.status.value)
print("SLOTS:", json.dumps(r.slots, ensure_ascii=False))
print("Q_REMAIN:", r.questions_remaining)
if r.current_question:
    print("CUR_Q:", r.current_question.slot, r.current_question.text[:80])
for q in s.pending_questions:
    print("Q:", q.slot, q.text[:80])
