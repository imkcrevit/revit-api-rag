"""
Text2Revit 服务 — 多轮对话引导 + 结构化指令生成

Session 状态机：
  IDLE → COLLECTING → COMPLETE

每轮：
  1. 识别意图 / 继续收集参数
  2. 检查缺失参数 → 生成引导
  3. 参数完整 → 输出结构化指令
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator

from server.app.session import Session
from server.app.deps import get_config, create_llm_for_session
from server.app.text2revit.intent import IntentRecognizer
from server.app.text2revit.actions import RevitAction, get_actions_summary
from server.app.api.streaming import format_sse_event, format_sse_done


# Session keys for Text2Revit state
_T2R_STATE = "_t2r_state"      # "IDLE" | "COLLECTING" | "COMPLETE"
_T2R_ACTION = "_t2r_action"    # current RevitAction.intent
_T2R_PARAMS = "_t2r_params"    # collected params dict


def _get_state(session: Session) -> dict[str, Any]:
    """Read Text2Revit state from session, using a dict on last_search_results."""
    if not hasattr(session, "_t2r"):
        session._t2r = {
            "state": "IDLE",
            "action_intent": None,
            "params": {},
        }
    return session._t2r


async def process_t2r_chat(
    message: str,
    session: Session,
) -> AsyncGenerator[str, None]:
    """
    Text2Revit chat pipeline. Yields SSE events.

    Events:
      - event: guide     — parameter guidance message
      - event: instruction — final structured instruction (JSON)
      - event: token     — streamed text
      - event: done      — complete
    """
    config = get_config()
    language = config.get("intent", {}).get("language", "zh")
    llm = create_llm_for_session(session)
    db_path = _resolve_api_db(config)

    recognizer = IntentRecognizer(llm, db_path)
    t2r = _get_state(session)

    session.add_message("user", message)

    # Handle "默认" / "defaults" — use defaults for all missing
    if message.strip().lower() in ("默认", "defaults", "default"):
        if t2r["state"] == "COLLECTING" and t2r["action_intent"]:
            from server.app.text2revit.actions import get_action
            action = get_action(t2r["action_intent"])
            if action:
                instruction = recognizer.build_instruction(action, t2r["params"])
                yield format_sse_event("instruction", json.dumps(instruction, ensure_ascii=False))
                _reset_state(t2r)
                response = _format_instruction_message(instruction, language)
                session.add_message("assistant", response)
                yield format_sse_event("token", response)
                yield format_sse_done()
                return

    # Handle "取消" / "cancel"
    if message.strip().lower() in ("取消", "cancel"):
        _reset_state(t2r)
        msg = "已取消。" if language == "zh" else "Cancelled."
        session.add_message("assistant", msg)
        yield format_sse_event("token", msg)
        yield format_sse_done()
        return

    if t2r["state"] == "COLLECTING":
        # We're in param collection mode — try to extract more params from message
        from server.app.text2revit.actions import get_action
        action = get_action(t2r["action_intent"])
        if action:
            result = recognizer.recognize(message)
            new_params = result.get("extracted_params", {})
            t2r["params"].update(new_params)

            missing = recognizer.check_missing_params(action, t2r["params"])
            if not missing:
                # All params collected!
                instruction = recognizer.build_instruction(action, t2r["params"])
                yield format_sse_event("instruction", json.dumps(instruction, ensure_ascii=False))
                _reset_state(t2r)
                response = _format_instruction_message(instruction, language)
                session.add_message("assistant", response)
                yield format_sse_event("token", response)
                yield format_sse_done()
                return
            else:
                guide = recognizer.generate_guidance(action, missing, language)
                session.add_message("assistant", guide)
                yield format_sse_event("guide", guide)
                yield format_sse_event("token", guide)
                yield format_sse_done()
                return

    # Fresh message — recognize intent
    result = recognizer.recognize(message)
    intent = result["intent"]
    action: RevitAction | None = result["action"]
    extracted = result["extracted_params"]

    if intent == "UNKNOWN" or action is None:
        # Unknown intent — respond with available actions
        if language == "zh":
            msg = f"抱歉，我暂时不支持这个操作。目前支持的操作有：\n\n{get_actions_summary()}\n\n请描述你想要执行的操作。"
        else:
            msg = f"Sorry, I don't support this operation yet. Currently supported:\n\n{get_actions_summary()}\n\nPlease describe what you'd like to do."
        session.add_message("assistant", msg)
        yield format_sse_event("token", msg)
        yield format_sse_done()
        return

    # Known intent — check params
    t2r["action_intent"] = intent
    t2r["params"] = extracted

    missing = recognizer.check_missing_params(action, extracted)
    if not missing:
        # All params already provided
        instruction = recognizer.build_instruction(action, extracted)
        yield format_sse_event("instruction", json.dumps(instruction, ensure_ascii=False))
        _reset_state(t2r)
        response = _format_instruction_message(instruction, language)
        session.add_message("assistant", response)
        yield format_sse_event("token", response)
        yield format_sse_done()
    else:
        # Need more params
        t2r["state"] = "COLLECTING"
        guide = recognizer.generate_guidance(action, missing, language)
        session.add_message("assistant", guide)
        yield format_sse_event("guide", guide)
        yield format_sse_event("token", guide)
        yield format_sse_done()


def _reset_state(t2r: dict):
    t2r["state"] = "IDLE"
    t2r["action_intent"] = None
    t2r["params"] = {}


def _format_instruction_message(instruction: dict, language: str) -> str:
    """Format the final instruction as a readable message."""
    action = instruction["action"]
    params = instruction["params"]
    params_str = json.dumps(params, ensure_ascii=False, indent=2)

    if language == "zh":
        return f"参数收集完成！生成的 Revit 操作指令：\n\n**操作**: `{action}`\n\n```json\n{params_str}\n```\n\n此指令可发送到 Revit 插件执行。"
    else:
        return f"Parameters complete! Generated Revit instruction:\n\n**Action**: `{action}`\n\n```json\n{params_str}\n```\n\nThis instruction can be sent to the Revit plugin for execution."


def _resolve_api_db(config: dict) -> str | None:
    """Resolve the API SQLite database path."""
    from pathlib import Path
    data_cfg = config.get("data", {})
    sqlite_dir = Path(data_cfg.get("sqlite_dir", "./data/sqlite"))
    db_path = sqlite_dir / "revit_api.db"
    return str(db_path) if db_path.exists() else None
