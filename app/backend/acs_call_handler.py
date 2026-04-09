"""
Azure Communication Services (ACS) Call Automation Handler
用于测试和处理来自 ACS 的电话来电

这个模块实现了：
1. 接收 ACS Call Automation 的 webhook 事件
2. 自动接听来电
3. 建立 ACS + /realtime/acs WebSocket 音频桥接（mixed-mono）
4. 记录通话状态

环境变量配置：
- VOICE_ENTRY_MODE: 语音入口模式，'web'（浏览器，默认）或 'acs'（电话）。两者互斥，不能同时启用。
- ACS_CONNECTION_STRING: Azure Communication Services 连接字符串
- ACS_CALLBACK_URL: 你的公网可访问的回调 URL (例如: https://yourapp.com/api/acs/calls/events)
- ACS_PHONE_NUMBER: 你的 ACS 电话号码 (例如: +1234567890)
- ACS_REALTIME_WS_URL: 可选，显式指定媒体桥接 WebSocket 地址（默认根据 ACS_CALLBACK_URL 推导为 wss://<host>/realtime/acs）
- ACS_USE_LEGACY_RECOGNIZE: 可选；当 VOICE_ENTRY_MODE=acs 时默认 false（使用 Realtime 桥接）；
  当 VOICE_ENTRY_MODE=web 时默认 true（向后兼容）。显式设置为 true 可强制启用 legacy 识别+TTS 流程作为 fallback。
"""

import asyncio
import json
import inspect
import logging
import os
import re
import tempfile
import time
import uuid
from typing import Any, Optional
from urllib.parse import quote, urlparse, urlunparse

from aiohttp import web
from dotenv import load_dotenv
from email_service import send_conversation_email
from quote_tools import (
    _quote_extraction_tool_schema,
    _send_quote_email_tool_schema,
    _update_quote_info_tool_schema,
)
from quote_workflow import (
    build_quote_confirmation_recap as shared_build_quote_confirmation_recap,
    build_quote_state,
    build_quote_targeted_recap as shared_build_quote_targeted_recap,
    create_quote_from_extracted,
    extract_quote_from_conversation,
    fetch_available_products,
    generate_quote_collection_response as shared_generate_quote_collection_response,
    normalize_and_match_quote_extracted_data,
)
from receptionist_config import (
    DEPARTMENTS,
    INFO_TOPICS,
    RECEPTIONIST_INTENTS,
    WELCOME_MESSAGE,
    build_company_info_answer,
    build_name_acknowledgement,
    build_receptionist_prompt,
    build_routing_prompt,
    build_transfer_message,
    format_department_label,
    get_contact_for_department,
    get_sales_rep_contact,
    to_e164_au,
)

# 先获取 logger，供后续导入失败时使用
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voicerag")

_TEST_TRANSFER_LOG_PREFIX = "TEST_TRANSFER"
_TEST_FLOW_LOG_PREFIX = "TEST_FLOW"
_ACS_TTS_VOICE = os.environ.get("ACS_TTS_VOICE", "en-AU-NatashaNeural")
_ACS_TTS_LOCALE = os.environ.get("ACS_TTS_LOCALE", "en-AU")
# Set ENABLE_QUOTE=false to disable quote generation and route all quote requests
# to the receptionist / human-assistance path instead.
_ENABLE_QUOTE: bool = os.environ.get("ENABLE_QUOTE", "true").strip().lower() != "false"

# ── Turn latency instrumentation ────────────────────────────────────────────
_LATENCY_LOG_PREFIX = "LATENCY"


class _TurnTimer:
    """Lightweight per-turn timestamp collector.

    All public methods return `self` so they can be chained, but they are
    purely additive — no existing control flow is altered.

    Stages (in pipeline order):
        asr_done          – user text received from ASR
        classification_start / classification_end  – top-level intent gate
        response_start / response_end              – generate_answer_text_with_gpt
        tts_start                                  – play_answer_message dispatched
    """

    __slots__ = (
        "call_id", "turn_id",
        "asr_done",
        "classification_start", "classification_end",
        "response_start", "response_end",
        "tts_start",
        "path",
    )

    def __init__(self, call_id: str) -> None:
        self.call_id = call_id
        self.turn_id = str(uuid.uuid4())[:8]
        self.asr_done: Optional[float] = None
        self.classification_start: Optional[float] = None
        self.classification_end: Optional[float] = None
        self.response_start: Optional[float] = None
        self.response_end: Optional[float] = None
        self.tts_start: Optional[float] = None
        self.path: Optional[str] = None

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    def mark_asr_done(self) -> "_TurnTimer":
        self.asr_done = self._now()
        return self

    def mark_classification_start(self) -> "_TurnTimer":
        self.classification_start = self._now()
        return self

    def mark_classification_end(self) -> "_TurnTimer":
        self.classification_end = self._now()
        return self

    def mark_response_start(self) -> "_TurnTimer":
        self.response_start = self._now()
        return self

    def mark_response_end(self) -> "_TurnTimer":
        self.response_end = self._now()
        return self

    def mark_tts_start(self) -> "_TurnTimer":
        self.tts_start = self._now()
        return self

    def set_path(self, path: str) -> "_TurnTimer":
        self.path = path
        return self

    @staticmethod
    def _ms(a: Optional[float], b: Optional[float]) -> Optional[float]:
        if a is None or b is None:
            return None
        return round((b - a) * 1000, 1)

    def emit(self) -> None:
        """Emit a single structured latency log entry for this turn."""
        asr_to_classification_ms = self._ms(self.asr_done, self.classification_start)
        classification_latency_ms = self._ms(self.classification_start, self.classification_end)
        classification_to_response_ms = self._ms(
            self.classification_end if self.classification_end is not None else self.asr_done,
            self.response_start,
        )
        response_latency_ms = self._ms(self.response_start, self.response_end)
        response_to_tts_ms = self._ms(self.response_end, self.tts_start)
        total_turn_latency_ms = self._ms(self.asr_done, self.tts_start)

        entry = {
            "call_id": self.call_id,
            "turn_id": self.turn_id,
            "path": self.path,
            "asr_to_classification_ms": asr_to_classification_ms,
            "classification_latency_ms": classification_latency_ms,
            "classification_to_response_ms": classification_to_response_ms,
            "response_latency_ms": response_latency_ms,
            "response_to_tts_ms": response_to_tts_ms,
            "total_turn_latency_ms": total_turn_latency_ms,
        }
        logger.info("%s %s", _LATENCY_LOG_PREFIX, json.dumps(entry))
# ── End turn latency instrumentation ────────────────────────────────────────

# 延迟导入 ACS SDK，避免导入失败导致模块无法加载
try:
    from azure.communication.callautomation import CallAutomationClient
    # 语音智能 / 识别相关类型（不同 SDK 版本可能略有差异，统一做兼容处理）
    try:
        from azure.communication.callautomation import (  # type: ignore
            PhoneNumberIdentifier,
            RecognizeInputType,
        )
    except ImportError:
        PhoneNumberIdentifier = None  # type: ignore[assignment]
        RecognizeInputType = None  # type: ignore[assignment]
        logger.info("PhoneNumberIdentifier / RecognizeInputType not available; speech Q&A may be limited.")
    try:
        # 新版 SDK：使用 AnswerCallOptions + CallIntelligenceOptions，可以在接听时配置认知服务
        from azure.communication.callautomation import (  # type: ignore
            AnswerCallOptions,
            CallIntelligenceOptions,
        )
    except ImportError:
        AnswerCallOptions = None  # type: ignore[assignment]
        CallIntelligenceOptions = None  # type: ignore[assignment]
        logger.info("AnswerCallOptions / CallIntelligenceOptions not available in this SDK version; will try simpler answer_call signature.")
    _acs_sdk_available = True
except ImportError as e:
    logger.warning("Azure Communication Services SDK not available: %s", str(e))
    logger.warning("Please install: pip install azure-communication-callautomation")
    _acs_sdk_available = False
    CallAutomationClient = None  # type: ignore[assignment]
    AnswerCallOptions = None  # type: ignore[assignment]
    CallIntelligenceOptions = None  # type: ignore[assignment]

# 存储活跃通话
_active_acs_calls: dict[str, dict[str, Any]] = {}

# ACS 客户端（全局单例）
_acs_client: Optional[CallAutomationClient] = None

def _use_legacy_acs_recognize_flow() -> bool:
    """是否启用旧版 ACS 识别+TTS 逻辑。

    当 VOICE_ENTRY_MODE=acs 时，默认走 GPT Realtime 音频桥接（legacy 关闭）。
    如需强制启用 legacy 流程作为 fallback，请显式设置 ACS_USE_LEGACY_RECOGNIZE=true。

    当 VOICE_ENTRY_MODE=web（或未设置）时，为向后兼容保留 legacy 默认开启行为。
    """
    voice_mode = os.environ.get("VOICE_ENTRY_MODE", "web").strip().lower()
    # acs 模式：默认使用 Realtime 桥接，legacy 关闭
    # web 模式或未设置：保持旧默认值（legacy 开启）以向后兼容
    default_legacy = "false" if voice_mode == "acs" else "true"
    return os.environ.get("ACS_USE_LEGACY_RECOGNIZE", default_legacy).strip().lower() in {"1", "true", "yes", "on"}


def _use_acs_realtime_bridge() -> bool:
    """ACS 电话线路是否启用 Realtime 媒体桥接。"""
    return not _use_legacy_acs_recognize_flow()


def _extract_caller_id(event_data: dict[str, Any]) -> str:
    """从 ACS 事件中提取 callerId（优先 phone number）用于 Realtime 会话键。"""
    data = event_data.get("data", {}) or {}
    from_info = data.get("from", {}) or {}

    caller_phone = (from_info.get("phoneNumber", {}) or {}).get("value")
    caller_raw_id = from_info.get("rawId")
    caller_communication_id = (from_info.get("communicationUser", {}) or {}).get("id")

    return (
        caller_phone
        or caller_raw_id
        or caller_communication_id
        or data.get("callerId")
        or "unknown-caller"
    )


def _build_realtime_ws_url(session_key: str) -> str:
    """构造 ACS 媒体流目标 WebSocket（默认 /realtime/acs，callerId 作为 session）。"""
    explicit_ws_url = os.environ.get("ACS_REALTIME_WS_URL", "").strip()
    if explicit_ws_url:
        separator = "&" if "?" in explicit_ws_url else "?"
        return f"{explicit_ws_url}{separator}session={quote(session_key)}"

    callback_url = os.environ.get("ACS_CALLBACK_URL", "").strip()
    if not callback_url:
        raise ValueError("ACS_CALLBACK_URL is required to derive /realtime websocket url")

    parsed = urlparse(callback_url)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    realtime_path = "/realtime/acs"
    return urlunparse((ws_scheme, parsed.netloc, realtime_path, "", f"session={quote(session_key)}", ""))


def _get_pending_receptionist_route(call_connection_id: Optional[str]) -> dict[str, Any]:
    if not call_connection_id or call_connection_id not in _active_acs_calls:
        return {}
    return dict(_active_acs_calls[call_connection_id].get("pending_receptionist_route") or {})


def _set_pending_receptionist_route(call_connection_id: Optional[str], route_state: dict[str, Any]) -> None:
    if not call_connection_id or call_connection_id not in _active_acs_calls:
        return
    _active_acs_calls[call_connection_id]["pending_receptionist_route"] = route_state


def _clear_pending_receptionist_route(call_connection_id: Optional[str]) -> None:
    if not call_connection_id or call_connection_id not in _active_acs_calls:
        return
    _active_acs_calls[call_connection_id].pop("pending_receptionist_route", None)


def _resolve_receptionist_route(
    call_connection_id: Optional[str],
    department: Optional[str],
    company_name: Optional[str],
    architectural_firm: bool = False,
) -> Optional[str]:
    pending_route = _get_pending_receptionist_route(call_connection_id)
    department = department or pending_route.get("department")
    company_name = company_name or pending_route.get("company_name")
    architectural_firm = bool(architectural_firm or pending_route.get("architectural_firm", False))

    logger.info(
        "%s Route resolution start: call=%s, pending_route=%s, department=%s, company_name=%s, architectural_firm=%s",
        _TEST_TRANSFER_LOG_PREFIX,
        call_connection_id,
        pending_route,
        department,
        company_name,
        architectural_firm,
    )

    if not pending_route and not department and not company_name:
        logger.info("%s Route resolution skipped: no pending route and no routing entities extracted.", _TEST_TRANSFER_LOG_PREFIX)
        return None

    if not department:
        _set_pending_receptionist_route(
            call_connection_id,
            {
                "department": None,
                "architectural_firm": architectural_firm,
                "company_name": company_name,
            },
        )
        logger.info(
            "%s Route resolution waiting for department: call=%s, saved_state=%s",
            _TEST_TRANSFER_LOG_PREFIX,
            call_connection_id,
            _get_pending_receptionist_route(call_connection_id),
        )
        return build_routing_prompt()

    if not company_name:
        _set_pending_receptionist_route(
            call_connection_id,
            {
                "department": department,
                "architectural_firm": architectural_firm,
                "company_name": None,
            },
        )
        logger.info(
            "%s Route resolution waiting for company name: call=%s, department=%s, saved_state=%s",
            _TEST_TRANSFER_LOG_PREFIX,
            call_connection_id,
            department,
            _get_pending_receptionist_route(call_connection_id),
        )
        return build_routing_prompt(department, missing_company_name=True)

    if call_connection_id and call_connection_id in _active_acs_calls:
        _active_acs_calls[call_connection_id]["caller_company"] = company_name

    _clear_pending_receptionist_route(call_connection_id)

    contact = get_sales_rep_contact() if architectural_firm else get_contact_for_department(department)
    logger.info(
        "%s Route target selected: call=%s, department=%s, company_name=%s, architectural_firm=%s, contact=%s",
        _TEST_TRANSFER_LOG_PREFIX,
        call_connection_id,
        department,
        company_name,
        architectural_firm,
        contact,
    )
    transfer_succeeded = False
    if contact:
        transfer_succeeded = _try_transfer_to_reception_contact(call_connection_id, contact["phone"], contact["name"])

    if transfer_succeeded:
        logger.info(
            "%s ACS transfer reported started: call=%s, department=%s, contact_name=%s, contact_phone=%s",
            _TEST_TRANSFER_LOG_PREFIX,
            call_connection_id,
            department,
            contact["name"] if contact else None,
            contact["phone"] if contact else None,
        )
        return build_transfer_message(department, architectural_firm=architectural_firm)

    logger.warning(
        "%s Transfer not started, falling back to verbal routing: call=%s, department=%s, company_name=%s, architectural_firm=%s, contact=%s",
        _TEST_TRANSFER_LOG_PREFIX,
        call_connection_id,
        department,
        company_name,
        architectural_firm,
        contact,
    )
    if architectural_firm:
        return (
            f"Thanks. I understand you're calling from {company_name}. "
            f"I'll connect you with {contact['name']} on {contact['phone']}."
            if contact
            else "I'll have our sales team help with that enquiry."
        )

    return (
        f"Thanks. For {format_department_label(department)}, the best contact is {contact['name']} on {contact['phone']}."
        if contact
        else "I can connect you with our main office on 03 8652 8000."
    )


def _try_transfer_to_reception_contact(
    call_connection_id: Optional[str],
    phone_number: str,
    contact_name: str,
) -> bool:
    logger.info(
        "%s ACS transfer attempt: call=%s, contact_name=%s, raw_phone=%s",
        _TEST_TRANSFER_LOG_PREFIX,
        call_connection_id,
        contact_name,
        phone_number,
    )
    if not call_connection_id:
        logger.warning("%s ACS transfer blocked: missing call_connection_id.", _TEST_TRANSFER_LOG_PREFIX)
        return False

    acs_client = get_acs_client()
    if not acs_client:
        logger.warning("%s ACS transfer blocked: ACS client unavailable.", _TEST_TRANSFER_LOG_PREFIX)
        return False

    if not PhoneNumberIdentifier:
        logger.warning("%s ACS transfer blocked: PhoneNumberIdentifier unavailable in current SDK.", _TEST_TRANSFER_LOG_PREFIX)
        return False

    e164_number = to_e164_au(phone_number)
    if not e164_number:
        logger.warning("%s ACS transfer blocked: failed to convert phone number to E.164. raw_phone=%s", _TEST_TRANSFER_LOG_PREFIX, phone_number)
        return False

    logger.info(
        "%s ACS transfer prerequisites passed: call=%s, contact_name=%s, e164_phone=%s",
        _TEST_TRANSFER_LOG_PREFIX,
        call_connection_id,
        contact_name,
        e164_number,
    )

    try:
        call_connection_client = acs_client.get_call_connection(call_connection_id)
        transfer_method = getattr(call_connection_client, "transfer_call_to_participant", None)
        if not callable(transfer_method):
            logger.warning(
                "%s ACS transfer blocked: CallConnectionClient has no transfer_call_to_participant. client_type=%s",
                _TEST_TRANSFER_LOG_PREFIX,
                type(call_connection_client).__name__,
            )
            return False

        operation_context = f"reception-transfer-{contact_name.lower().replace(' ', '-')}"
        logger.info(
            "%s Invoking ACS transfer: call=%s, contact_name=%s, e164_phone=%s, operation_context=%s",
            _TEST_TRANSFER_LOG_PREFIX,
            call_connection_id,
            contact_name,
            e164_number,
            operation_context,
        )
        transfer_method(
            PhoneNumberIdentifier(e164_number),
            operation_context=operation_context,
        )
        logger.info(
            "%s ACS transfer invocation returned without exception: call=%s, contact_name=%s, e164_phone=%s",
            _TEST_TRANSFER_LOG_PREFIX,
            call_connection_id,
            contact_name,
            e164_number,
        )
        return True
    except Exception as e:
        logger.warning(
            "%s ACS transfer raised exception: call=%s, contact_name=%s, raw_phone=%s, error=%s",
            _TEST_TRANSFER_LOG_PREFIX,
            call_connection_id,
            contact_name,
            phone_number,
            str(e),
        )
        return False


async def _classify_receptionist_intent_with_llm(
    client,
    deployment: str,
    user_text: str,
    conversation_history: list,
    pending_route: dict[str, Any],
) -> dict[str, Any]:
    recent_history = [
        {
            "role": ("assistant" if msg.get("role") == "assistant" else "user"),
            "content": msg.get("content", ""),
        }
        for msg in (conversation_history or [])[-6:]
        if isinstance(msg, dict) and msg.get("content")
    ]
    payload = {
        "pending_route": pending_route,
        "recent_history": recent_history,
        "latest_user_text": user_text,
        "supported_info_topics": sorted(INFO_TOPICS),
        "supported_departments": sorted(DEPARTMENTS),
    }
    logger.info(
        "%s Intent classification request: latest_user_text=%s, pending_route=%s, recent_history_count=%d",
        _TEST_TRANSFER_LOG_PREFIX,
        user_text,
        pending_route,
        len(recent_history),
    )
    prompt = (
        "Classify the caller's latest turn for a phone receptionist workflow. "
        "Return JSON only. "
        "Allowed intent values: caller_intro, company_info, routing_request, general_qa. "
        "If the user is asking for company details, set intent=company_info and choose one info_topic from: "
        "company_summary, office_address, office_hours, warehouse_address, warehouse_hours, website, email, office_phone. "
        "If the user wants to be connected, transferred, routed, or asks for a department or person, set intent=routing_request. "
        "For routing_request also extract department when clear using one of: flooring, panels, glass, fibreglass. "
        "Also extract company_name if the caller states who they are from. "
        "Set is_architectural_firm=true only when the caller clearly says they are from an architect, architectural, design, or similar firm. "
        "If the caller is simply introducing themself, set intent=caller_intro and extract caller_name. "
        "If unclear, set intent=general_qa. "
        "Schema: {"
        "\"intent\":\"...\","
        "\"info_topic\":null|\"...\","
        "\"department\":null|\"...\","
        "\"caller_name\":null|\"...\","
        "\"company_name\":null|\"...\","
        "\"is_architectural_firm\":true|false"
        "}."
    )
    try:
        response = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=160,
        )
        content = (response.choices[0].message.content or "{}").strip()
        logger.info("%s Intent classification raw response: %s", _TEST_TRANSFER_LOG_PREFIX, content[:500])
        result = json.loads(content)
    except Exception as e:
        logger.warning("Receptionist intent classification failed: %s", str(e))
        return {"intent": "general_qa"}

    intent = result.get("intent")
    info_topic = result.get("info_topic")
    department = result.get("department")
    normalized = {
        "intent": intent if intent in RECEPTIONIST_INTENTS else "general_qa",
        "info_topic": info_topic if info_topic in INFO_TOPICS else None,
        "department": department if department in DEPARTMENTS else None,
        "caller_name": (result.get("caller_name") or None),
        "company_name": (result.get("company_name") or None),
        "is_architectural_firm": bool(result.get("is_architectural_firm", False)),
    }
    logger.info("%s Receptionist intent classification: %s", _TEST_TRANSFER_LOG_PREFIX, normalized)
    return normalized


def _default_quote_delivery() -> dict[str, Any]:
    return {
        "quote_id": None,
        "quote_number": None,
        "quote_url": None,
        "email_sent": False,
        "email_error": None,
    }


def _enrich_quote_state_with_delivery(quote_state: Optional[dict[str, Any]]) -> dict[str, Any]:
    state = dict(quote_state or {})
    state["delivery"] = {
        **_default_quote_delivery(),
        **dict(state.get("delivery") or {}),
    }
    state.setdefault("extracted", {})
    state.setdefault("missing_fields", ["customer_name", "contact_info", "quote_items"])
    state.setdefault("products_available", [])
    state.setdefault("is_complete", False)
    return state


def _store_acs_quote_state(
    call_connection_id: Optional[str],
    quote_state: dict[str, Any],
    conversation_history: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    stored_state = _enrich_quote_state_with_delivery(quote_state)
    if call_connection_id and call_connection_id in _active_acs_calls:
        _active_acs_calls[call_connection_id]["quote_state"] = stored_state
        if conversation_history is not None:
            _active_acs_calls[call_connection_id]["conversation_history"] = conversation_history
    return stored_state


def _append_assistant_message(call_connection_id: Optional[str], answer_text: str) -> None:
    if not call_connection_id or call_connection_id not in _active_acs_calls or not answer_text:
        return

    conversation_history = _active_acs_calls[call_connection_id].get("conversation_history", [])
    conversation_history.append({"role": "assistant", "content": answer_text})
    if len(conversation_history) > 10:
        conversation_history = conversation_history[-10:]
    _active_acs_calls[call_connection_id]["conversation_history"] = conversation_history
    _active_acs_calls[call_connection_id]["last_answer"] = answer_text

    transcript = _active_acs_calls[call_connection_id].setdefault("call_transcript", [])
    transcript.append({"role": "assistant", "content": answer_text})


def _get_pending_quote_update(call_connection_id: Optional[str]) -> dict[str, Any]:
    if not call_connection_id or call_connection_id not in _active_acs_calls:
        return {}
    return dict(_active_acs_calls[call_connection_id].get("pending_quote_update") or {})


def _set_pending_quote_update(call_connection_id: Optional[str], pending_update: dict[str, Any]) -> None:
    if not call_connection_id or call_connection_id not in _active_acs_calls:
        return
    _active_acs_calls[call_connection_id]["pending_quote_update"] = pending_update


def _clear_pending_quote_update(call_connection_id: Optional[str]) -> None:
    if not call_connection_id or call_connection_id not in _active_acs_calls:
        return
    _active_acs_calls[call_connection_id].pop("pending_quote_update", None)


def _get_pending_quote_recap(call_connection_id: Optional[str]) -> dict[str, Any]:
    if not call_connection_id or call_connection_id not in _active_acs_calls:
        return {}
    return dict(_active_acs_calls[call_connection_id].get("pending_quote_recap") or {})


def _set_pending_quote_recap(call_connection_id: Optional[str], pending_recap: dict[str, Any]) -> None:
    if not call_connection_id or call_connection_id not in _active_acs_calls:
        return
    _active_acs_calls[call_connection_id]["pending_quote_recap"] = pending_recap


def _clear_pending_quote_recap(call_connection_id: Optional[str]) -> None:
    if not call_connection_id or call_connection_id not in _active_acs_calls:
        return
    _active_acs_calls[call_connection_id].pop("pending_quote_recap", None)


def _is_awaiting_quote_confirmation(call_connection_id: Optional[str]) -> bool:
    if not call_connection_id or call_connection_id not in _active_acs_calls:
        return False
    return bool(_active_acs_calls[call_connection_id].get("awaiting_quote_confirmation"))


def _set_awaiting_quote_confirmation(call_connection_id: Optional[str], value: bool = True) -> None:
    if not call_connection_id or call_connection_id not in _active_acs_calls:
        return
    _active_acs_calls[call_connection_id]["awaiting_quote_confirmation"] = value


def _clear_awaiting_quote_confirmation(call_connection_id: Optional[str]) -> None:
    if not call_connection_id or call_connection_id not in _active_acs_calls:
        return
    _active_acs_calls[call_connection_id].pop("awaiting_quote_confirmation", None)


def _get_empty_recognition_count(call_connection_id: Optional[str]) -> int:
    if not call_connection_id or call_connection_id not in _active_acs_calls:
        return 0
    return int(_active_acs_calls[call_connection_id].get("empty_recognition_count", 0) or 0)


def _set_empty_recognition_count(call_connection_id: Optional[str], count: int) -> None:
    if not call_connection_id or call_connection_id not in _active_acs_calls:
        return
    _active_acs_calls[call_connection_id]["empty_recognition_count"] = max(0, int(count))


def _reset_empty_recognition_count(call_connection_id: Optional[str]) -> None:
    _set_empty_recognition_count(call_connection_id, 0)


def _build_acs_progress_summary(call_connection_id: str, *, full_transcript: bool = False) -> str:
    call_info = _active_acs_calls.get(call_connection_id, {})
    quote_state = _enrich_quote_state_with_delivery(call_info.get("quote_state"))
    extracted = quote_state.get("extracted", {})

    # Use full unbounded transcript when available (end-of-call email); fall back to
    # the windowed conversation_history for mid-call progress snapshots.
    if full_transcript and call_info.get("call_transcript"):
        messages = call_info["call_transcript"]
        conversation_label = "Full call transcript:"
    else:
        messages = call_info.get("conversation_history", [])[-10:]
        conversation_label = "Recent conversation (last 10 turns):"

    lines = [
        f"ACS call progress summary for call {call_connection_id}",
        "",
        "Collected quote details:",
        f"- customer_name: {extracted.get('customer_name') or 'not provided'}",
        f"- contact_info: {extracted.get('contact_info') or 'not provided'}",
    ]

    quote_items = extracted.get("quote_items") or []
    if quote_items:
        lines.append("- quote_items:")
        for item in quote_items:
            if isinstance(item, dict):
                lines.append(
                    f"  - {item.get('product_package') or 'unknown product'} x {item.get('quantity') or 'unknown quantity'}"
                )
    else:
        lines.append("- quote_items: not provided")

    lines.extend(
        [
            f"- expected_start_date: {extracted.get('expected_start_date') or 'not provided'}",
            f"- notes: {extracted.get('notes') or 'not provided'}",
            f"- missing_fields: {', '.join(quote_state.get('missing_fields', [])) or 'none'}",
            "",
            conversation_label,
        ]
    )

    for message in messages:
        if isinstance(message, dict):
            role = str(message.get("role", "unknown")).upper()
            content = str(message.get("content", "")).strip()
            if content:
                lines.append(f"{role}: {content}")

    return "\n".join(lines).strip() + "\n"


async def _send_transcript_email_from_snapshot(
    call_connection_id: str,
    call_snapshot: dict,
) -> None:
    """Send the end-of-call transcript email using a pre-captured state snapshot.

    Called via asyncio.create_task after _active_acs_calls has already been
    popped, so it must not read from _active_acs_calls.
    """
    quote_state = _enrich_quote_state_with_delivery(call_snapshot.get("quote_state"))
    contact_info = str(quote_state.get("extracted", {}).get("contact_info") or "").strip()
    if "@" not in contact_info:
        logger.info(
            "Skipping end-of-call transcript email: no valid email for call %s", call_connection_id
        )
        return

    # Build summary directly from snapshot without touching _active_acs_calls.
    extracted = quote_state.get("extracted", {})
    messages = call_snapshot.get("call_transcript") or call_snapshot.get("conversation_history", [])

    lines = [
        f"ACS call transcript for call {call_connection_id}",
        "",
        "Collected quote details:",
        f"- customer_name: {extracted.get('customer_name') or 'not provided'}",
        f"- contact_info: {extracted.get('contact_info') or 'not provided'}",
    ]
    quote_items = extracted.get("quote_items") or []
    if quote_items:
        lines.append("- quote_items:")
        for item in quote_items:
            if isinstance(item, dict):
                lines.append(
                    f"  - {item.get('product_package') or 'unknown product'}"
                    f" x {item.get('quantity') or 'unknown quantity'}"
                )
    else:
        lines.append("- quote_items: not provided")
    lines.extend([
        f"- expected_start_date: {extracted.get('expected_start_date') or 'not provided'}",
        f"- notes: {extracted.get('notes') or 'not provided'}",
        f"- missing_fields: {', '.join(quote_state.get('missing_fields', [])) or 'none'}",
        "",
        "Full call transcript:",
    ])
    for msg in messages:
        if isinstance(msg, dict):
            role = str(msg.get("role", "unknown")).upper()
            content = str(msg.get("content", "")).strip()
            if content:
                lines.append(f"{role}: {content}")

    summary_text = "\n".join(lines).strip() + "\n"

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as f:
            f.write(summary_text)
            temp_path = f.name
        sent = await send_conversation_email(contact_info, temp_path, call_connection_id)
        logger.info("End-of-call transcript email send result for %s: %s", call_connection_id, sent)
    except Exception as e:
        logger.error("Failed to send transcript email for %s: %s", call_connection_id, str(e))
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                logger.warning("Failed to remove transcript temp file: %s", temp_path)


async def _send_acs_progress_email_if_available(
    call_connection_id: str,
    *,
    full_transcript: bool = False,
) -> bool:
    call_info = _active_acs_calls.get(call_connection_id, {})
    quote_state = _enrich_quote_state_with_delivery(call_info.get("quote_state"))
    contact_info = str(quote_state.get("extracted", {}).get("contact_info") or "").strip()
    if "@" not in contact_info:
        logger.info(
            "Skipping ACS %s email: no valid email for call %s",
            "transcript" if full_transcript else "progress",
            call_connection_id,
        )
        return False

    temp_path = None
    try:
        summary_text = _build_acs_progress_summary(call_connection_id, full_transcript=full_transcript)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as temp_file:
            temp_file.write(summary_text)
            temp_path = temp_file.name

        sent = await send_conversation_email(contact_info, temp_path, call_connection_id)
        logger.info(
            "ACS %s email send result for %s: %s",
            "transcript" if full_transcript else "progress",
            call_connection_id,
            sent,
        )
        return sent
    except Exception as e:
        logger.error("Failed to send ACS progress email for %s: %s", call_connection_id, str(e))
        return False
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                logger.warning("Failed to remove temporary ACS progress summary file: %s", temp_path)


async def _hang_up_acs_call(call_connection_id: str) -> None:
    acs_client = get_acs_client()
    if not acs_client:
        logger.warning("ACS client not available, cannot hang up call %s", call_connection_id)
        return

    try:
        call_connection_client = acs_client.get_call_connection(call_connection_id)
        call_connection_client.hang_up(is_for_everyone=True)
        logger.info("ACS call hung up programmatically: %s", call_connection_id)
    except Exception as e:
        logger.error("Failed to hang up ACS call %s: %s", call_connection_id, str(e))


async def _end_call_after_repeated_empty_input(call_connection_id: str) -> None:
    logger.info("Ending ACS call after repeated empty recognition events: %s", call_connection_id)
    await _send_acs_progress_email_if_available(call_connection_id)
    if call_connection_id in _active_acs_calls:
        _active_acs_calls[call_connection_id]["hangup_after_playback"] = True
    await play_answer_message(
        call_connection_id,
        "I haven't heard a response for a while, so I'll end the call now. I'll email your current progress if I have your email address. Goodbye.",
    )


def _is_explicit_quote_confirmation(user_text: str) -> bool:
    normalized = re.sub(r"[^a-z0-9\s]", " ", (user_text or "").lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return False

    return normalized in {"confirm", "yes"}


def _wrap_chat_tool_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize internal tool schema to chat.completions tool format."""
    if not isinstance(schema, dict):
        return schema
    if schema.get("function") and schema.get("type") == "function":
        return schema

    function_schema = dict(schema)
    function_schema.pop("type", None)
    return {
        "type": "function",
        "function": function_schema,
    }


def _sanitize_tts_text(text: str, max_len: int = 350) -> str:
    sanitized = re.sub(r"\s+", " ", str(text or "")).strip()
    sanitized = sanitized.replace("&", "and")  # & is invalid in SSML XML; replace for natural speech
    sanitized = sanitized.replace("*", "").replace("#", "")
    sanitized = re.sub(r"[<>`]", "", sanitized)
    if len(sanitized) > max_len:
        sanitized = sanitized[: max_len - 3].rstrip() + "..."
    return sanitized


def _build_acs_text_source(text: str, context_tag: str) -> Any:
    sanitized_text = _sanitize_tts_text(text)
    if not sanitized_text:
        sanitized_text = "I'm sorry, I don't have a response right now."
        logger.warning("TTS text was empty after sanitization (context=%s); using fallback.", context_tag)
    logger.info(
        "TEST_TTS Building TextSource: context=%s, locale=%s, voice=%s, text_len=%d, text=%s",
        context_tag,
        _ACS_TTS_LOCALE,
        _ACS_TTS_VOICE,
        len(sanitized_text),
        sanitized_text,
    )

    try:
        from azure.communication.callautomation import TextSource
    except ImportError:
        from azure.communication.callautomation.models import TextSource  # type: ignore

    return TextSource(
        text=sanitized_text,
        voice_name=_ACS_TTS_VOICE,
        source_locale=_ACS_TTS_LOCALE,
    )


def _create_media_streaming_options(stream_url: str) -> Any:
    """兼容不同 ACS SDK 版本构造 MediaStreamingOptions。"""
    try:
        from azure.communication.callautomation import (  # type: ignore
            AudioFormat,
            MediaStreamingAudioChannelType,
            MediaStreamingContentType,
            MediaStreamingOptions,
            StreamingTransportType,
        )

        return MediaStreamingOptions(
            transport_url=stream_url,
            transport_type=StreamingTransportType.WEBSOCKET,
            content_type=MediaStreamingContentType.AUDIO,
            audio_channel_type=MediaStreamingAudioChannelType.MIXED,
            audio_format=AudioFormat.PCM24_K_MONO,
            enable_bidirectional=True,
            start_media_streaming=True,
        )
    except Exception:
        logger.warning("MediaStreamingOptions types not available; fallback to dict payload.")
        return {
            "transport_url": stream_url,
            "transport_type": "websocket",
            "content_type": "audio",
            "audio_channel_type": "mixed",
            "audio_format": "pcm24KMono",
            "enable_bidirectional": True,
            "start_media_streaming": True,
        }


async def start_realtime_bridge(call_connection_id: str, session_key: str) -> None:
    """启动 ACS -> /realtime/acs WebSocket 媒体桥接。"""
    acs_client = get_acs_client()
    if not acs_client:
        logger.error("ACS client not available, cannot start realtime bridge")
        return

    call_connection = acs_client.get_call_connection(call_connection_id)
    stream_url = _build_realtime_ws_url(session_key)
    logger.info("🌉 Starting ACS + GPT-4o Realtime bridge")
    logger.info("   call_connection_id=%s", call_connection_id)
    logger.info("   session_key=%s", session_key)
    logger.info("   stream_url=%s", stream_url)

    try:
        start_media_streaming = None
        if hasattr(call_connection, "start_media_streaming"):
            start_media_streaming = call_connection.start_media_streaming  # type: ignore[assignment]
        elif hasattr(call_connection, "call_media") and hasattr(call_connection.call_media, "start_media_streaming"):
            start_media_streaming = call_connection.call_media.start_media_streaming  # type: ignore[assignment]
        else:
            raise AttributeError("Current ACS SDK does not expose start_media_streaming")

        # SDK 1.5.0 的 start_media_streaming() 是 keyword-only，且不接受位置参数。
        # 媒体流配置（transport_url 等）应在 answer_call(..., media_streaming=...) 阶段提供。
        method_signature = inspect.signature(start_media_streaming)
        logger.info("start_media_streaming signature: %s", method_signature)
        start_media_streaming()  # type: ignore[misc]

        _active_acs_calls.setdefault(call_connection_id, {})["realtime_bridge"] = {
            "status": "started",
            "session_key": session_key,
            "stream_url": stream_url,
            "started_at": time.time(),
        }
        logger.info("✅ ACS realtime bridge started")
    except Exception as e:
        error_text = str(e)
        # 已启动媒体流时，不应视为失败（幂等触发常见于重复事件/重试）
        if "Media streaming has already started" in error_text or "(8583)" in error_text:
            _active_acs_calls.setdefault(call_connection_id, {})["realtime_bridge"] = {
                "status": "already_started",
                "session_key": session_key,
                "stream_url": stream_url,
                "started_at": time.time(),
            }
            logger.info("ℹ️ ACS realtime bridge already started for call=%s", call_connection_id)
            return
        logger.error("❌ Failed to start ACS realtime bridge: %s", error_text)


def get_acs_client() -> Optional[CallAutomationClient]:
    """获取或创建 ACS Call Automation 客户端"""
    global _acs_client
    
    if not _acs_sdk_available or CallAutomationClient is None:
        logger.warning("ACS SDK not available, cannot create client")
        return None
    
    if _acs_client is not None:
        return _acs_client
    
    connection_string = os.environ.get("ACS_CONNECTION_STRING")
    # 额外日志：打印原始连接串 repr，帮助排查格式问题（空格 / 引号 / 不可见字符等）
    logger.error("ACS_CONNECTION_STRING raw repr=%r", connection_string)
    
    if not connection_string:
        logger.warning("ACS_CONNECTION_STRING not configured. ACS call handling will be disabled.")
        return None
    
    try:
        _acs_client = CallAutomationClient.from_connection_string(connection_string)
        logger.info("ACS Call Automation client initialized successfully")
        return _acs_client
    except Exception as e:
        logger.error("Failed to initialize ACS client: %s", str(e))
        return None


async def handle_incoming_call_event(event_data: dict[str, Any]) -> dict[str, Any]:
    """
    处理来电事件 - 自动接听电话
    
    Args:
        event_data: ACS 发送的 IncomingCall 事件数据
        
    Returns:
        处理结果
    """
    acs_client = get_acs_client()
    if not acs_client:
        logger.error("ACS client not available, cannot handle incoming call")
        return {"error": "ACS client not configured"}
    
    try:
        # 正确解析事件数据（incomingCallContext 是字符串 token，不是对象）
        data = event_data.get("data", {})
        incoming_call_context = data.get("incomingCallContext", "")
        if not incoming_call_context:
            incoming_call_context = event_data.get("incomingCallContext", "")
        
        # 从事件数据中提取来电信息
        from_info = data.get("from", {})
        to_info = data.get("to", {})
        
        # 提取真正的电话号码（用于语音识别的 target_participant）
        caller_phone = from_info.get("phoneNumber", {}).get("value")
        recipient_phone = to_info.get("phoneNumber", {}).get("value")
        
        # 也保存 rawId（用于日志/调试）
        caller_raw_id = from_info.get("rawId", "")
        recipient_raw_id = to_info.get("rawId", "")
        
        logger.info("📞 Incoming Call:")
        logger.info("   Caller Phone: %s", caller_phone or "unknown")
        logger.info("   Caller RawId: %s", caller_raw_id or "unknown")
        logger.info("   Recipient Phone: %s", recipient_phone or "unknown")
        logger.info("   Incoming Call Context: %s...", incoming_call_context[:50] if incoming_call_context else "None")
        
        if not incoming_call_context:
            logger.error("❌ No incomingCallContext found in event data")
            return {"error": "No incomingCallContext in event"}
        
        # 获取回调 URL（不要自动补 /events，使用原始 URL）
        callback_url = os.environ.get("ACS_CALLBACK_URL")
        if not callback_url:
            logger.error("❌ ACS_CALLBACK_URL not configured")
            return {"error": "Callback URL not configured"}
        
        logger.info("   Callback URL: %s", callback_url)

        media_streaming_options = None
        if _use_acs_realtime_bridge():
            stream_url = _build_realtime_ws_url(_extract_caller_id(event_data))
            media_streaming_options = _create_media_streaming_options(stream_url)
            logger.info("   Realtime Stream URL: %s", stream_url)
        else:
            logger.info("   ACS legacy recognize flow enabled; skipping realtime media streaming setup")
        
        # 准备 Cognitive Services 配置（用于在通话建立阶段启用 TTS 能力）
        cog_endpoint = os.environ.get("ACS_COGNITIVE_SERVICE_ENDPOINT", "").strip()
        answer_result = None
        
        logger.info("   ACS_COGNITIVE_SERVICE_ENDPOINT: %r", cog_endpoint or "NOT SET")
        
        try:
            # 优先使用新版 SDK 的 AnswerCallOptions + CallIntelligenceOptions
            if cog_endpoint and 'AnswerCallOptions' in globals() and AnswerCallOptions is not None and CallIntelligenceOptions is not None:  # type: ignore[name-defined]
                logger.info("📞 Answering call with CallIntelligenceOptions (cognitive_services_endpoint)...")
                call_intel_options = CallIntelligenceOptions(  # type: ignore[call-arg]
                    cognitive_services_endpoint=cog_endpoint
                )
                answer_options = AnswerCallOptions(  # type: ignore[call-arg]
                    incoming_call_context=incoming_call_context,
                    callback_url=callback_url,
                    call_intelligence_options=call_intel_options,
                )
                answer_result = acs_client.answer_call(answer_options)
            elif cog_endpoint:
                # 某些 SDK 版本在 answer_call 上直接暴露 cognitive_services_endpoint 参数
                logger.info("📞 Answering call with cognitive_services_endpoint kwarg...")
                try:
                    answer_kwargs: dict[str, Any] = {
                        "incoming_call_context": incoming_call_context,
                        "callback_url": callback_url,
                        "cognitive_services_endpoint": cog_endpoint,
                    }
                    answer_call_signature = inspect.signature(acs_client.answer_call)
                    if media_streaming_options is not None and "media_streaming" in answer_call_signature.parameters and not isinstance(media_streaming_options, dict):
                        answer_kwargs["media_streaming"] = media_streaming_options
                        logger.info("   media_streaming options attached to answer_call")
                    elif isinstance(media_streaming_options, dict):
                        logger.warning("   media_streaming options unavailable as typed object; skipping for this SDK")

                    answer_result = acs_client.answer_call(**answer_kwargs)
                except TypeError:
                    logger.warning("answer_call() does not accept cognitive_services_endpoint; falling back to basic answer_call.")
                    fallback_kwargs: dict[str, Any] = {
                        "incoming_call_context": incoming_call_context,
                        "callback_url": callback_url,
                    }
                    answer_call_signature = inspect.signature(acs_client.answer_call)
                    if media_streaming_options is not None and "media_streaming" in answer_call_signature.parameters and not isinstance(media_streaming_options, dict):
                        fallback_kwargs["media_streaming"] = media_streaming_options
                        logger.info("   media_streaming options attached to fallback answer_call")
                    answer_result = acs_client.answer_call(**fallback_kwargs)
            else:
                # 未配置认知服务终结点，使用最基础的 answer_call（仍可接通，但可能无法使用某些智能特性）
                logger.warning("ACS_COGNITIVE_SERVICE_ENDPOINT not set; answering call without cognitive configuration.")
                answer_kwargs: dict[str, Any] = {
                    "incoming_call_context": incoming_call_context,
                    "callback_url": callback_url,
                }
                answer_call_signature = inspect.signature(acs_client.answer_call)
                if media_streaming_options is not None and "media_streaming" in answer_call_signature.parameters and not isinstance(media_streaming_options, dict):
                    answer_kwargs["media_streaming"] = media_streaming_options
                    logger.info("   media_streaming options attached to answer_call")
                answer_result = acs_client.answer_call(**answer_kwargs)
        except Exception as e:
            logger.error("❌ Error calling answer_call with cognitive configuration: %s", str(e))
            import traceback
            logger.error("Traceback: %s", traceback.format_exc())
            # 最后兜底：尝试最简单的签名
            try:
                logger.info("📞 Retrying basic answer_call without cognitive configuration...")
                answer_result = acs_client.answer_call(
                    incoming_call_context=incoming_call_context,
                    callback_url=callback_url,
                )
            except Exception as e2:
                logger.error("❌ Fallback basic answer_call also failed: %s", str(e2))
                import traceback as tb
                logger.error("Traceback: %s", tb.format_exc())
                return {"error": f"answer_call failed: {e2}"}
        
        if answer_result and hasattr(answer_result, 'call_connection_id'):
            call_connection_id = answer_result.call_connection_id
            
            # 记录活跃通话（保存真正的电话号码，用于后续语音识别的 target_participant）
            _active_acs_calls[call_connection_id] = {
                "call_connection_id": call_connection_id,
                "caller_phone": caller_phone,  # 真正的电话号码，如 "+8615397262726"，用于 PhoneNumberIdentifier
                "caller_raw_id": caller_raw_id,  # rawId 如 "4:+613..."，仅用于日志/调试
                "caller_info": from_info,  # 保存完整的 from_info，用于兜底
                "recipient_phone": recipient_phone,
                "recipient_raw_id": recipient_raw_id,
                "caller_session_key": _extract_caller_id(event_data),
                "status": "answered",
                "started_at": time.time(),
                "call_transcript": [],  # unbounded full-call transcript (never pruned)
            }
            
            logger.info("✅ Call answered successfully!")
            logger.info("   Connection ID: %s", call_connection_id)
            
            return {
                "success": True,
                "call_connection_id": call_connection_id,
                "caller_phone": caller_phone,
                "message": "Call answered successfully"
            }
        else:
            logger.error("❌ Failed to answer call - no connection ID returned")
            logger.error("   Answer result: %s", answer_result)
            return {"error": "Failed to answer call"}
            
    except Exception as e:
        logger.error("❌ Error handling incoming call: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())
        return {"error": str(e)}


async def handle_call_connected_event(event_data: dict[str, Any]) -> None:
    """处理通话已连接事件"""
    try:
        # callConnectionId 在 data 字段中
        event_data_obj = event_data.get("data", {})
        call_connection_id = event_data_obj.get("callConnectionId")
        
        logger.info("✅ Call Connected - Connection ID: %s", call_connection_id)
        
        if call_connection_id and call_connection_id in _active_acs_calls:
            _active_acs_calls[call_connection_id]["status"] = "connected"
            logger.info("   Updated call status to 'connected'")

            if _use_acs_realtime_bridge():
                session_key = _active_acs_calls[call_connection_id].get("caller_session_key") or "unknown-caller"
                await start_realtime_bridge(call_connection_id, str(session_key))
            else:
                logger.info("Legacy ACS recognize flow enabled, playing welcome message instead of starting realtime bridge")
                await play_welcome_message(call_connection_id)
        else:
            logger.warning("   Call connection ID not found in active calls")
        
    except Exception as e:
        logger.error("Error handling call connected event: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())


async def handle_call_disconnected_event(event_data: dict[str, Any]) -> None:
    """处理通话断开事件"""
    try:
        # callConnectionId 在 data 字段中
        event_data_obj = event_data.get("data", {})
        call_connection_id = event_data_obj.get("callConnectionId")
        result_info = event_data_obj.get("resultInformation", {})
        disconnect_reason = result_info.get("message", "Unknown reason")
        
        logger.info("❌ Call Disconnected - Connection ID: %s", call_connection_id)
        logger.info("   Reason: %s", disconnect_reason)
        
        if call_connection_id and call_connection_id in _active_acs_calls:
            # Snapshot call state before the pop so the async email task has stable data.
            call_snapshot = dict(_active_acs_calls[call_connection_id])
            _active_acs_calls.pop(call_connection_id)
            logger.info("   Removed call from active calls: %s", call_connection_id)

            # Fire-and-forget: send transcript email without blocking call teardown.
            logger.info("   Scheduling end-of-call transcript email for %s", call_connection_id)
            asyncio.create_task(
                _send_transcript_email_from_snapshot(call_connection_id, call_snapshot),
                name=f"transcript-email-{call_connection_id}",
            )
        else:
            logger.warning("   Call connection ID not found in active calls")
        
    except Exception as e:
        logger.error("Error handling call disconnected event: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())


async def handle_play_completed_event(event_data: dict[str, Any]) -> None:
    """处理音频播放完成事件"""
    try:
        event_data_obj = event_data.get("data", {})
        call_connection_id = event_data_obj.get("callConnectionId")
        operation_context = event_data_obj.get("operationContext")
        
        logger.info("%s Play completed: call=%s context=%s", _TEST_FLOW_LOG_PREFIX, call_connection_id, operation_context)
        
        if call_connection_id and call_connection_id in _active_acs_calls:
            if operation_context == "welcome-tts":
                # 欢迎语播放完成，启动第一次语音识别
                _active_acs_calls[call_connection_id]["welcome_played"] = True
                logger.info("%s Welcome playback finished; starting recognition. call=%s", _TEST_FLOW_LOG_PREFIX, call_connection_id)
                await start_speech_recognition(call_connection_id)
            elif operation_context == "answer-tts":
                # 回答播放完成，重新启动识别，实现多轮对话
                if _active_acs_calls[call_connection_id].get("hangup_after_playback"):
                    logger.info("%s Answer playback finished; hanging up call=%s", _TEST_FLOW_LOG_PREFIX, call_connection_id)
                    await _hang_up_acs_call(call_connection_id)
                else:
                    logger.info("%s Answer playback finished; restarting recognition. call=%s", _TEST_FLOW_LOG_PREFIX, call_connection_id)
                    await start_speech_recognition(call_connection_id)

    except Exception as e:
        logger.error("Error handling play completed event: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())


async def handle_play_failed_event(event_data: dict[str, Any]) -> None:
    """处理音频播放失败事件（详细打印 Cognitive Services 错误信息）"""
    try:
        data = event_data.get("data", {}) or {}
        call_connection_id = data.get("callConnectionId") or event_data.get("callConnectionId")
        operation_context = data.get("operationContext")

        result_info = data.get("resultInformation", {}) or {}
        logger.warning("TEST_TTS Play failed - call=%s, operation_context=%s", call_connection_id, operation_context)
        logger.warning(
            "TEST_TTS Play failed summary: code=%s, subCode=%s, message=%s",
            result_info.get("code"),
            result_info.get("subCode"),
            result_info.get("message"),
        )

        # 有时更深一层 details 里还有具体的 speechErrorCode / subcode
        if isinstance(result_info, dict) and "details" in result_info:
            logger.warning("resultInformation.details=%s", json.dumps(result_info["details"], ensure_ascii=False))

    except Exception as e:
        logger.error("Error handling play failed event: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())


async def handle_recognize_completed(event_data: dict[str, Any]) -> None:
    """
    处理语音识别完成事件：
    1. 从事件里拿到用户说的话（转成的文本）
    2. 检测是否是报价请求，如果是则收集报价信息
    3. 调 GPT 生成回答
    4. 用 ACS TTS 播放回答
    """
    try:
        _turn_timer: Optional[_TurnTimer] = None  # initialised after ASR text is confirmed
        data = event_data.get("data", {}) or {}
        call_connection_id = data.get("callConnectionId")

        logger.info("%s RecognizeCompleted received: call=%s", _TEST_FLOW_LOG_PREFIX, call_connection_id)

        # 不同版本 / 模式下，识别结果可能挂在不同字段上，这里尽量兼容性查找
        recognize_result = (
            data.get("recognizeResult")
            or data.get("speechResult")
            or data.get("recognize_result")
            or {}
        )

        def _find_transcript(obj: Any, depth: int = 0) -> str:
            if depth > 4 or obj is None:
                return ""
            if isinstance(obj, dict):
                # 常见字段名
                for key in (
                    "transcript",
                    "text",
                    "recognizedSpeech",
                    "speechText",
                    "displayText",
                    "speech",
                    "lexical",
                    "itn",
                    "maskedItn",
                ):
                    if key in obj and isinstance(obj[key], str) and obj[key].strip():
                        return obj[key]
                for v in obj.values():
                    t = _find_transcript(v, depth + 1)
                    if t:
                        return t
            elif isinstance(obj, list):
                for item in obj:
                    t = _find_transcript(item, depth + 1)
                    if t:
                        return t
            return ""

        user_text = _find_transcript(recognize_result)
        if not user_text:
            # 再从整个 event_data 里兜底找一次
            user_text = _find_transcript(event_data)
        user_text = (user_text or "").strip()

        if not user_text:
            logger.warning("%s No transcript extracted: call=%s", _TEST_FLOW_LOG_PREFIX, call_connection_id)
            if call_connection_id:
                empty_count = _get_empty_recognition_count(call_connection_id) + 1
                _set_empty_recognition_count(call_connection_id, empty_count)
                logger.info("%s Empty transcript count=%d for call=%s", _TEST_FLOW_LOG_PREFIX, empty_count, call_connection_id)
                if empty_count >= 10:
                    await _end_call_after_repeated_empty_input(call_connection_id)
                else:
                    await play_answer_message(call_connection_id, "I didn't catch that. Could you please say that again?")
            return

        logger.info("%s Transcript extracted: call=%s text=%s", _TEST_FLOW_LOG_PREFIX, call_connection_id, user_text[:160])
        _reset_empty_recognition_count(call_connection_id)
        _turn_timer = _TurnTimer(call_connection_id or "no-call-id").mark_asr_done()

        # 初始化通话的报价状态（如果还没有）
        if call_connection_id and call_connection_id not in _active_acs_calls:
            _active_acs_calls[call_connection_id] = {
                "call_connection_id": call_connection_id,
                "status": "active",
                "call_transcript": [],
            }
            logger.info("%s Initialized missing call state: call=%s", _TEST_FLOW_LOG_PREFIX, call_connection_id)
        
        # 处理报价逻辑
        if call_connection_id:
            call_info = _active_acs_calls.get(call_connection_id, {})
            quote_state = call_info.get("quote_state", {})
            conversation_history = call_info.get("conversation_history", [])
            
            logger.info(
                "%s Pre-answer state: call=%s history_len=%d has_quote=%s quote_complete=%s missing=%s",
                _TEST_FLOW_LOG_PREFIX,
                call_connection_id,
                len(conversation_history),
                bool(quote_state),
                bool(quote_state.get("is_complete")),
                quote_state.get("missing_fields", []),
            )
            
            # 先更新报价状态（提取信息）；回答文本生成完成后再一次性播报
            logger.info("%s Generating answer: call=%s", _TEST_FLOW_LOG_PREFIX, call_connection_id)
            _turn_timer.mark_response_start()
            answer_text, quote_updated, already_played = await generate_answer_text_with_gpt(
                user_text, call_connection_id, _timer=_turn_timer
            )
            _turn_timer.mark_response_end()
            
            # 重新获取更新后的报价状态
            updated_call_info = _active_acs_calls.get(call_connection_id, {})
            quote_state = updated_call_info.get("quote_state", {})
            updated_conversation = updated_call_info.get("conversation_history", [])
            
            logger.info(
                "%s Answer generated: call=%s answer_len=%d already_played=%s quote_updated=%s quote_complete=%s history_len=%d",
                _TEST_FLOW_LOG_PREFIX,
                call_connection_id,
                len(answer_text or ""),
                already_played,
                quote_updated,
                quote_state.get("is_complete", False),
                len(updated_conversation),
            )
        else:
            logger.info("➡️  BRANCH: Entering SIMPLE MODE branch (no call_connection_id)")
            # 没有 call_connection_id，使用简单模式
            answer_text, _, already_played = await generate_answer_text_with_gpt(user_text, None)
            already_played = False

        # 播放回答：ACS 现在统一等完整文本生成后再一次性 TTS 播报
        if call_connection_id and not already_played:
            logger.info("%s Handing answer to TTS: call=%s answer_len=%d", _TEST_FLOW_LOG_PREFIX, call_connection_id, len(answer_text or ""))
            _turn_timer.mark_tts_start().emit()
            await play_answer_message(call_connection_id, answer_text)
        elif call_connection_id and already_played:
            # Answer was streamed / played inside generate_answer_text_with_gpt; no TTS here.
            _turn_timer.set_path((_turn_timer.path or "") + "+already_played").emit()
        elif not call_connection_id:
            logger.warning("No call_connection_id in RecognizeCompleted event; cannot play answer.")
            _turn_timer.set_path("no_call_id").emit()

    except Exception as e:
        logger.error("Error handling RecognizeCompleted event: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())
        if _turn_timer is not None:
            _turn_timer.set_path("exception").emit()
        # 告诉来电者当前问答流程出了问题，方便你感知
        try:
            data = event_data.get("data", {}) or {}
            call_connection_id = data.get("callConnectionId") or event_data.get("callConnectionId")
        except Exception:
            call_connection_id = None
        await speak_error_message(call_connection_id, debug_tag="recognize-completed-exception")


async def handle_recognize_completed_event(event_data: dict[str, Any]) -> None:
    """兼容旧调用路径，转发到新的处理函数。"""
    await handle_recognize_completed(event_data)


async def handle_recognize_failed_event(event_data: dict[str, Any]) -> None:
    """处理语音识别失败事件，主要用于日志排查"""
    try:
        data = event_data.get("data", {}) or {}
        call_connection_id = data.get("callConnectionId")
        result_info = data.get("resultInformation", {}) or {}

        logger.warning("⚠️  RecognizeFailed - call=%s", call_connection_id)
        logger.warning("resultInformation=%s", json.dumps(result_info, ensure_ascii=False))

        # 在电话里提示一次“系统出错”，方便你知道是识别阶段的问题
        await speak_error_message(call_connection_id, debug_tag="recognize-failed")

    except Exception as e:
        logger.error("Error handling RecognizeFailed event: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())


async def generate_answer_text_with_gpt(
    user_text: str,
    call_connection_id: Optional[str] = None,
    _timer: Optional["_TurnTimer"] = None,
) -> tuple[str, bool, bool]:
    """
    使用 Azure OpenAI 根据用户语音转成的文本生成回答（电话版 Q&A 核心逻辑）。
    
    支持报价功能：
    - 检测报价意图
    - 收集报价信息
    - 生成自然对话回答
    
    ACS 电话分支统一先生成完整回答，再一次性播报。
    
    Returns:
        tuple[str, bool, bool]: (回答文本, 报价状态是否更新, 是否已提前播报)
    """
    # 如果 GPT 不可用，就回个固定文案，避免电话静音
    fallback = "I am sorry, I could not process your question. Please try again later."

    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.identity import DefaultAzureCredential
        from openai import AzureOpenAI
    except Exception as e:
        logger.warning("Azure OpenAI SDK not available, using fallback answer. Error: %s", str(e))
        if _timer is not None:
            _timer.set_path("sdk_unavailable")
        return fallback, False, False

    openai_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    openai_deployment = (
        os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        or os.environ.get("AZURE_OPENAI_EXTRACTION_DEPLOYMENT")
        or "gpt-4o-mini"
    )
    llm_key = os.environ.get("AZURE_OPENAI_API_KEY")

    # 立即输出使用的模型信息
    if not openai_endpoint or not openai_deployment:
        logger.warning("Azure OpenAI endpoint/deployment not configured. Using fallback answer.")
        if _timer is not None:
            _timer.set_path("no_endpoint")
        return fallback, False, False

    if llm_key:
        credential = AzureKeyCredential(llm_key)
    else:
        credential = DefaultAzureCredential()

    try:
        if isinstance(credential, AzureKeyCredential):
            client = AzureOpenAI(
                api_key=credential.key,
                api_version="2024-02-15-preview",
                azure_endpoint=openai_endpoint,
            )
        else:
            token = credential.get_token("https://cognitiveservices.azure.com/.default").token
            client = AzureOpenAI(
                api_key=token,
                api_version="2024-02-15-preview",
                azure_endpoint=openai_endpoint,
            )

        # 获取当前通话的对话历史（用于报价信息提取）
        conversation_history = []
        quote_state = {}
        if call_connection_id and call_connection_id in _active_acs_calls:
            call_info = _active_acs_calls[call_connection_id]
            quote_state = call_info.get("quote_state", {})
            conversation_history = call_info.get("conversation_history", [])
        
        # 添加当前用户消息到历史（如果还没有添加）
        # call_transcript is an audit log — always append, no deduplication.
        if call_connection_id and call_connection_id in _active_acs_calls:
            transcript = _active_acs_calls[call_connection_id].setdefault("call_transcript", [])
            transcript.append({"role": "user", "content": user_text})
        if not conversation_history or conversation_history[-1].get("content") != user_text:
            conversation_history.append({"role": "user", "content": user_text})
        # 只保留最近 10 条消息
        if len(conversation_history) > 10:
            conversation_history = conversation_history[-10:]

        # 更新通话状态中的对话历史
        if call_connection_id and call_connection_id in _active_acs_calls:
            _active_acs_calls[call_connection_id]["conversation_history"] = conversation_history

        quote_state = _enrich_quote_state_with_delivery(quote_state) if quote_state else {}
        logger.info(
            "%s Enter generate_answer_text_with_gpt: call=%s history_len=%d has_quote=%s quote_complete=%s",
            _TEST_FLOW_LOG_PREFIX,
            call_connection_id,
            len(conversation_history),
            bool(quote_state),
            bool(quote_state.get("is_complete")),
        )
        if (
            call_connection_id
            and quote_state.get("is_complete")
            and not quote_state.get("missing_fields")
            and _is_explicit_quote_confirmation(user_text)
        ):
            logger.info("➡️  BRANCH: Explicit quote confirmation detected; sending quote email directly")
            tool_result = await _execute_acs_quote_tool_call(
                "send_quote_email",
                {},
                call_connection_id,
                conversation_history,
                quote_state,
            )
            quote_state = _enrich_quote_state_with_delivery(tool_result.get("quote_state"))
            if tool_result.get("success"):
                quote_result = tool_result.get("quote_result") or tool_result.get("quote_state", {}).get("delivery", {})
                if tool_result.get("already_sent"):
                    answer_text = "Your quote email was already sent. If you want me to resend it, just say resend the quote email."
                else:
                    answer_text = (
                        f"Great, I've processed your confirmation. "
                        f"Your quote number is {quote_result.get('quote_number', 'N/A')}. "
                        "I've sent the quote email. Is there anything else I can help you with?"
                    )
                    if call_connection_id in _active_acs_calls:
                        _active_acs_calls[call_connection_id].pop("quote_state", None)
                _clear_pending_quote_update(call_connection_id)
                _clear_pending_quote_recap(call_connection_id)
                _clear_awaiting_quote_confirmation(call_connection_id)
            else:
                answer_text = "I wasn't able to send the quote just now. Please try again in a moment."
            _append_assistant_message(call_connection_id, answer_text)
            logger.info("Answer text from explicit confirmation flow: %s", answer_text)
            if _timer is not None:
                _timer.set_path("quote_confirmation")
            return answer_text, False, False

        pending_recap = _get_pending_quote_recap(call_connection_id)
        if call_connection_id and pending_recap and quote_state:
            logger.info("➡️  BRANCH: Resolving pending quote recap: %s", pending_recap)
            recap_request = await _extract_quote_recap_request(
                client,
                openai_deployment,
                user_text,
                conversation_history,
                pending_fields=pending_recap.get("requested_fields", []),
            )
            requested_fields = recap_request.get("requested_fields", [])
            if recap_request.get("wants_all"):
                requested_fields = []
            if recap_request.get("needs_clarification") and not requested_fields:
                _set_pending_quote_recap(call_connection_id, pending_recap)
                answer_text = _build_quote_recap_follow_up(pending_recap.get("requested_fields", []))
                _append_assistant_message(call_connection_id, answer_text)
                logger.info("Answer text from pending-recap follow-up: %s", answer_text)
                if _timer is not None:
                    _timer.set_path("pending_recap_followup")
                return answer_text, False, False

            _clear_pending_quote_recap(call_connection_id)
            recap = _build_quote_targeted_recap(quote_state, requested_fields)
            if quote_state.get("is_complete"):
                _set_awaiting_quote_confirmation(call_connection_id)
                answer_text = (
                    f"{recap} Please say 'confirm' or 'yes' to create the quote, "
                    "or tell me what you'd like to change."
                )
            else:
                follow_up = _generate_quote_collection_response(quote_state.get("missing_fields", []), quote_state)
                answer_text = f"{recap} {follow_up}"
            _append_assistant_message(call_connection_id, answer_text)
            logger.info("Answer text from pending-recap resolution: %s", answer_text)
            if _timer is not None:
                _timer.set_path("pending_recap_resolved")
            return answer_text, False, False

        pending_update = _get_pending_quote_update(call_connection_id)
        if call_connection_id and pending_update:
            logger.info("➡️  BRANCH: Resolving pending quote update: %s", pending_update)
            update_request = await _extract_quote_update_request(
                client,
                openai_deployment,
                user_text,
                conversation_history,
                quote_state,
                pending_fields=pending_update.get("requested_fields", []),
            )
            requested_fields = update_request.get("requested_fields", []) or pending_update.get("requested_fields", [])
            update_arguments = dict(update_request.get("updates", {}))
            missing_requested_fields = [
                field for field in requested_fields
                if field not in update_arguments or update_arguments.get(field) in (None, "", [])
            ]
            if update_arguments:
                tool_result = await _execute_acs_quote_tool_call(
                    "update_quote_info",
                    update_arguments,
                    call_connection_id,
                    conversation_history,
                    quote_state,
                )
                quote_state = _enrich_quote_state_with_delivery(tool_result.get("quote_state"))
                quote_updated = bool(tool_result.get("quote_updated"))
                remaining_fields = [
                    field for field in requested_fields
                    if field not in update_arguments or update_arguments.get(field) in (None, "", [])
                ]
                if remaining_fields:
                    _set_pending_quote_update(call_connection_id, {"requested_fields": remaining_fields})
                    answer_text = _build_quote_update_follow_up(remaining_fields)
                    _append_assistant_message(call_connection_id, answer_text)
                    logger.info("Answer text from partial pending-update resolution: %s", answer_text)
                    if _timer is not None:
                        _timer.set_path("pending_update_partial")
                    return answer_text, quote_updated, False

                _clear_pending_quote_update(call_connection_id)
                if quote_state.get("missing_fields"):
                    _clear_awaiting_quote_confirmation(call_connection_id)
                    answer_text = _generate_quote_collection_response(quote_state.get("missing_fields", []), quote_state)
                else:
                    _clear_pending_quote_recap(call_connection_id)
                    include_recap = not _is_awaiting_quote_confirmation(call_connection_id)
                    answer_text = _build_quote_confirmation_prompt(
                        quote_state,
                        include_recap=include_recap,
                    )
                    _set_awaiting_quote_confirmation(call_connection_id)
                _append_assistant_message(call_connection_id, answer_text)
                logger.info("Answer text from pending-update resolution: %s", answer_text)
                if _timer is not None:
                    _timer.set_path("pending_update_resolved")
                return answer_text, quote_updated, False

            _set_pending_quote_update(call_connection_id, {"requested_fields": requested_fields or pending_update.get("requested_fields", [])})
            answer_text = _build_quote_update_follow_up(requested_fields or pending_update.get("requested_fields", []))
            _append_assistant_message(call_connection_id, answer_text)
            logger.info("Answer text from pending-update follow-up: %s", answer_text)
            if _timer is not None:
                _timer.set_path("pending_update_followup")
            return answer_text, False, False

        # ── TOP-LEVEL INTENT GATE ──────────────────────────────────────────────
        # Classify the current utterance BEFORE touching the quote planner.
        # Non-quote intents (company_info, routing, general_qa) bypass quote
        # tools entirely and route straight to the receptionist path.
        if _timer is not None:
            _timer.mark_classification_start()
        top_intent = await _classify_top_level_intent(
            client,
            openai_deployment,
            user_text,
            conversation_history,
            has_active_quote_state=bool(quote_state),
            quote_complete=bool(quote_state.get("is_complete")),
        )
        if _timer is not None:
            _timer.mark_classification_end()
        logger.info(
            "%s Top intent: call=%s primary=%s secondary=%s enter_quote=%s preserve=%s reason=%s",
            _TEST_FLOW_LOG_PREFIX,
            call_connection_id,
            top_intent["primary_intent"],
            top_intent["secondary_intents"],
            top_intent["should_enter_quote_flow"],
            top_intent["should_preserve_quote_state"],
            top_intent["reason"],
        )

        if not _ENABLE_QUOTE and top_intent["should_enter_quote_flow"]:
            logger.info(
                "➡️  QUOTE DISABLED: overriding should_enter_quote_flow=True for call=%s (ENABLE_QUOTE=false)",
                call_connection_id,
            )
            top_intent["should_enter_quote_flow"] = False
            top_intent["primary_intent"] = "general_qa"

        if not top_intent["should_enter_quote_flow"]:
            logger.info("➡️  BRANCH: Non-quote intent (%s) — routing to receptionist path",
                        top_intent["primary_intent"])
            answer_text, already_played = await _run_receptionist_path(
                client,
                openai_deployment,
                openai_endpoint,
                user_text,
                conversation_history,
                call_connection_id,
            )
            # If a quote is in progress, append a brief continuation reminder
            # (only when the answer wasn't already sent via streaming)
            if (
                top_intent["should_preserve_quote_state"]
                and quote_state
                and not quote_state.get("is_complete")
                and not already_played
            ):
                missing = quote_state.get("missing_fields", [])
                if missing:
                    reminder = _generate_quote_collection_response(missing, quote_state)
                    # lower-case the first letter so it reads naturally after a period
                    answer_text = f"{answer_text} Also, {reminder[0].lower() + reminder[1:]}"
            _append_assistant_message(call_connection_id, answer_text)
            logger.info("%s Non-quote path resolved: call=%s answer_len=%d", _TEST_FLOW_LOG_PREFIX, call_connection_id, len(answer_text or ""))
            if _timer is not None:
                _timer.set_path("receptionist")
            return answer_text, False, already_played
        # ── END TOP-LEVEL INTENT GATE ──────────────────────────────────────────

        planned_tool_call = await _plan_acs_quote_tool_call(
            client,
            openai_deployment,
            user_text,
            conversation_history,
            quote_state,
        )
        quote_updated = False

        if planned_tool_call and call_connection_id:
            logger.info("%s Quote planner selected tool: call=%s tool=%s", _TEST_FLOW_LOG_PREFIX, call_connection_id, planned_tool_call["name"])
            tool_result = await _execute_acs_quote_tool_call(
                planned_tool_call["name"],
                planned_tool_call.get("arguments", {}),
                call_connection_id,
                conversation_history,
                quote_state,
            )
            quote_state = _enrich_quote_state_with_delivery(tool_result.get("quote_state"))
            quote_updated = bool(tool_result.get("quote_updated"))

            if planned_tool_call["name"] == "send_quote_email":
                if tool_result.get("success"):
                    quote_result = tool_result.get("quote_result") or {}
                    if tool_result.get("already_sent"):
                        answer_text = (
                            "Your quote email was already sent. "
                            "If you want me to resend it, just say resend the quote email."
                        )
                    else:
                        answer_text = (
                            f"Great, I've processed your confirmation. "
                            f"Your quote number is {quote_result.get('quote_number', 'N/A')}. "
                            "I've sent the quote email. Is there anything else I can help you with?"
                        )
                        if call_connection_id in _active_acs_calls:
                            _active_acs_calls[call_connection_id].pop("quote_state", None)
                    _clear_awaiting_quote_confirmation(call_connection_id)
                else:
                    if quote_state.get("is_complete"):
                        answer_text = (
                            "I wasn't able to send the quote just now. "
                            "Please try again in a moment."
                        )
                    else:
                        recap = _build_quote_targeted_recap(quote_state, [])
                        follow_up = _generate_quote_collection_response(quote_state.get("missing_fields", []), quote_state)
                        answer_text = f"{recap} {follow_up}"

                _append_assistant_message(call_connection_id, answer_text)
                logger.info("Answer text from GPT tool flow: %s", answer_text)
                if _timer is not None:
                    _timer.set_path("quote_tool_email")
                return answer_text, quote_updated, False

            missing_fields = quote_state.get("missing_fields", [])
            if missing_fields:
                _clear_awaiting_quote_confirmation(call_connection_id)
                answer_text = _generate_quote_collection_response(missing_fields, quote_state)
            else:
                _clear_pending_quote_recap(call_connection_id)
                include_recap = not _is_awaiting_quote_confirmation(call_connection_id)
                answer_text = _build_quote_confirmation_prompt(
                    quote_state,
                    include_recap=include_recap,
                )
                _set_awaiting_quote_confirmation(call_connection_id)

            _append_assistant_message(call_connection_id, answer_text)
            logger.info("Answer text from GPT tool flow: %s", answer_text)
            if _timer is not None:
                _timer.set_path("quote_tool_other")
            return answer_text, quote_updated, False

        behavior = await _classify_user_behavior_with_llm(
            client,
            openai_deployment,
            user_text,
            conversation_history,
            bool(quote_state),
            bool(quote_state.get("is_complete")),
        )

        if call_connection_id and behavior in {"quote_request", "modify_quote_info"}:
            logger.info(
                "➡️  BRANCH: LLM classified quote intent but planner returned no tool call; forcing fallback tool execution. behavior=%s",
                behavior,
            )
            if behavior == "modify_quote_info":
                update_request = await _extract_quote_update_request(
                    client,
                    openai_deployment,
                    user_text,
                    conversation_history,
                    quote_state,
                )
                requested_fields = update_request.get("requested_fields", [])
                update_arguments = dict(update_request.get("updates", {}))
                missing_requested_fields = [
                    field for field in requested_fields
                    if field not in update_arguments or update_arguments.get(field) in (None, "", [])
                ]
                if requested_fields and missing_requested_fields:
                    if update_arguments:
                        tool_result = await _execute_acs_quote_tool_call(
                            "update_quote_info",
                            update_arguments,
                            call_connection_id,
                            conversation_history,
                            quote_state,
                        )
                        quote_state = _enrich_quote_state_with_delivery(tool_result.get("quote_state"))
                        quote_updated = bool(tool_result.get("quote_updated"))
                    _set_pending_quote_update(call_connection_id, {"requested_fields": missing_requested_fields})
                    answer_text = _build_quote_update_follow_up(missing_requested_fields)
                    _append_assistant_message(call_connection_id, answer_text)
                    logger.info("Answer text from modify follow-up flow: %s", answer_text)
                    if _timer is not None:
                        _timer.set_path("quote_fallback_modify_partial")
                    return answer_text, quote_updated, False
                tool_result = await _execute_acs_quote_tool_call(
                    "update_quote_info",
                    update_arguments,
                    call_connection_id,
                    conversation_history,
                    quote_state,
                )
            else:
                tool_result = await _execute_acs_quote_tool_call(
                    "extract_quote_info",
                    {},
                    call_connection_id,
                    conversation_history,
                    quote_state,
                )
            quote_state = _enrich_quote_state_with_delivery(tool_result.get("quote_state"))
            quote_updated = bool(tool_result.get("quote_updated"))

            if quote_state.get("missing_fields"):
                _clear_awaiting_quote_confirmation(call_connection_id)
                answer_text = _generate_quote_collection_response(quote_state.get("missing_fields", []), quote_state)
            else:
                _clear_pending_quote_recap(call_connection_id)
                include_recap = not _is_awaiting_quote_confirmation(call_connection_id)
                answer_text = _build_quote_confirmation_prompt(
                    quote_state,
                    include_recap=include_recap,
                )
                _set_awaiting_quote_confirmation(call_connection_id)

            _append_assistant_message(call_connection_id, answer_text)
            logger.info("Answer text from fallback quote flow: %s", answer_text)
            if _timer is not None:
                _timer.set_path("quote_fallback")
            return answer_text, quote_updated, False

        if quote_state and behavior == "recall_quote_info":
            logger.info("➡️  BRANCH: Entering QUOTE RECALL branch (user asking for quote info)")
            recap_request = await _extract_quote_recap_request(
                client,
                openai_deployment,
                user_text,
                conversation_history,
            )
            requested_fields = recap_request.get("requested_fields", [])
            if recap_request.get("wants_all"):
                requested_fields = []
            if recap_request.get("needs_clarification") and requested_fields:
                _set_pending_quote_recap(call_connection_id, {"requested_fields": requested_fields})
                answer_text = _build_quote_recap_follow_up(requested_fields)
                _append_assistant_message(call_connection_id, answer_text)
                logger.info("Answer text from recap clarification flow: %s", answer_text)
                if _timer is not None:
                    _timer.set_path("recall_clarification")
                return answer_text, False, False
            if recap_request.get("needs_clarification") and not requested_fields:
                _set_pending_quote_recap(
                    call_connection_id,
                    {"requested_fields": ["customer_name", "contact_info", "quote_items", "expected_start_date", "notes"]},
                )
                answer_text = _build_quote_recap_follow_up([])
                _append_assistant_message(call_connection_id, answer_text)
                logger.info("Answer text from recap clarification flow: %s", answer_text)
                if _timer is not None:
                    _timer.set_path("recall_clarification_all")
                return answer_text, False, False
            recap = _build_quote_targeted_recap(quote_state, requested_fields)
            if quote_state.get("is_complete"):
                _clear_awaiting_quote_confirmation(call_connection_id)
                answer_text = (
                    f"{recap} Please say 'confirm' or 'yes' to create the quote, "
                    "or tell me what you'd like to change."
                )
            else:
                follow_up = _generate_quote_collection_response(quote_state.get("missing_fields", []), quote_state)
                answer_text = f"{recap} {follow_up}"

            _append_assistant_message(call_connection_id, answer_text)
            logger.info("Answer text from GPT recall flow: %s", answer_text)
            if _timer is not None:
                _timer.set_path("recall_resolved")
            return answer_text, False, False

        if quote_state and not quote_state.get("is_complete") and behavior != "general_qa":
            logger.info("➡️  BRANCH: Quote state exists but no tool call was planned; ask for remaining missing info")
            _clear_awaiting_quote_confirmation(call_connection_id)
            answer_text = _generate_quote_collection_response(quote_state.get("missing_fields", []), quote_state)
            _append_assistant_message(call_connection_id, answer_text)
            logger.info("Answer text from GPT pending-quote flow: %s", answer_text)
            if _timer is not None:
                _timer.set_path("pending_quote")
            return answer_text, False, False

        else:
            # quote_state is complete or was never started — plain receptionist turn
            logger.info("➡️  SUB-BRANCH: Regular Q&A (quote_state complete or absent)")
            answer_text, already_played = await _run_receptionist_path(
                client,
                openai_deployment,
                openai_endpoint,
                user_text,
                conversation_history,
                call_connection_id,
            )
            _append_assistant_message(call_connection_id, answer_text)
            logger.info("%s Receptionist path resolved: call=%s answer_len=%d", _TEST_FLOW_LOG_PREFIX, call_connection_id, len(answer_text or ""))
            if _timer is not None:
                _timer.set_path("receptionist_qa")
            return answer_text, quote_updated, already_played

        logger.info("Answer text from GPT: %s", answer_text)
        return answer_text, quote_updated, False
    except Exception as e:
        logger.error("Failed to generate answer text via Azure OpenAI: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())
        if _timer is not None:
            _timer.set_path("exception")
        return fallback, False, False


def _build_quote_confirmation_recap(quote_state: dict) -> str:
    """Build a concise recap sentence for collected quote info."""
    return shared_build_quote_confirmation_recap(quote_state)


async def _extract_recap_requested_fields(user_text: str, conversation_history: list) -> list[str]:
    """Use LLM to identify which quote fields user wants to recap; empty means recap all."""
    openai_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    openai_deployment = (
        os.environ.get("AZURE_OPENAI_EXTRACTION_DEPLOYMENT")
        or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        or "gpt-4o-mini"
    )
    llm_key = os.environ.get("AZURE_OPENAI_API_KEY")

    if not openai_endpoint:
        return []

    try:
        from azure.identity import DefaultAzureCredential
        from openai import AzureOpenAI

        if llm_key:
            client = AzureOpenAI(api_key=llm_key, api_version="2024-02-15-preview", azure_endpoint=openai_endpoint)
        else:
            token = DefaultAzureCredential().get_token("https://cognitiveservices.azure.com/.default").token
            client = AzureOpenAI(api_key=token, api_version="2024-02-15-preview", azure_endpoint=openai_endpoint)

        payload = {
            "latest_user_text": user_text,
            "recent_history": [
                {"role": ("assistant" if m.get("role") == "assistant" else "user"), "content": m.get("content", "")}
                for m in (conversation_history or [])[-6:]
                if isinstance(m, dict) and m.get("content")
            ],
        }

        response = client.chat.completions.create(
            model=openai_deployment,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Identify which quote fields the user wants to recap. "
                        "Return JSON only with key requested_fields. "
                        "Allowed field values: customer_name, contact_info, quote_items, expected_start_date, notes. "
                        "If user asks for all details or is ambiguous, return an empty array."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=64,
        )
        result = json.loads((response.choices[0].message.content or "{}").strip())
        requested = result.get("requested_fields")
        if isinstance(requested, list):
            allowed = {"customer_name", "contact_info", "quote_items", "expected_start_date", "notes"}
            return [f for f in requested if f in allowed]
    except Exception as e:
        logger.warning("Failed to extract recap requested fields: %s", str(e))

    return []


async def _extract_quote_recap_request(
    client,
    deployment: str,
    user_text: str,
    conversation_history: list,
    pending_fields: Optional[list[str]] = None,
) -> dict[str, Any]:
    payload = {
        "latest_user_text": user_text,
        "recent_history": [
            {"role": ("assistant" if m.get("role") == "assistant" else "user"), "content": m.get("content", "")}
            for m in (conversation_history or [])[-6:]
            if isinstance(m, dict) and m.get("content")
        ],
        "pending_fields": pending_fields or [],
    }

    prompt = (
        "Identify which quote fields the user wants to hear recapped. "
        "Return JSON only with keys requested_fields, wants_all, and needs_clarification. "
        "Allowed field values: customer_name, contact_info, quote_items, expected_start_date, notes. "
        "Set wants_all=true when the user clearly asks for all quote details. "
        "Set needs_clarification=true when the user asks for a recap but does not make clear which fields they want, and they are not clearly asking for all details. "
        "If pending_fields is provided, treat the latest user turn as a clarification of which of those fields they want."
    )

    try:
        response = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=96,
        )
        result = json.loads((response.choices[0].message.content or "{}").strip())
        requested_fields = result.get("requested_fields")
        if not isinstance(requested_fields, list):
            requested_fields = []
        allowed = {"customer_name", "contact_info", "quote_items", "expected_start_date", "notes"}
        requested_fields = [field for field in requested_fields if field in allowed]
        return {
            "requested_fields": requested_fields,
            "wants_all": bool(result.get("wants_all")),
            "needs_clarification": bool(result.get("needs_clarification")) and not bool(result.get("wants_all")),
        }
    except Exception as e:
        logger.warning("Failed to extract quote recap request: %s", str(e))
        return {"requested_fields": pending_fields or [], "wants_all": False, "needs_clarification": not bool(pending_fields)}


async def _extract_quote_update_request(
    client,
    deployment: str,
    user_text: str,
    conversation_history: list,
    quote_state: dict,
    pending_fields: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Extract which quote fields the user wants to modify and any new values they already provided."""
    payload = {
        "latest_user_text": user_text,
        "recent_history": [
            {"role": ("assistant" if m.get("role") == "assistant" else "user"), "content": m.get("content", "")}
            for m in (conversation_history or [])[-6:]
            if isinstance(m, dict) and m.get("content")
        ],
        "current_quote_state": _enrich_quote_state_with_delivery(quote_state),
        "pending_fields": pending_fields or [],
    }

    prompt = (
        "Extract quote modification intent from the latest phone-call turn. "
        "Return JSON only with these keys: requested_fields, updates, has_new_value. "
        "requested_fields must contain any of: customer_name, contact_info, quote_items, expected_start_date, notes. "
        "updates may contain only those same fields. "
        "If the user says they want to change a field but does not provide the new value yet, include the field in requested_fields, leave it out of updates, and set has_new_value to false. "
        "If pending_fields is provided, treat the latest user text as the likely new value for those fields unless impossible."
    )

    try:
        response = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=200,
        )
        content = (response.choices[0].message.content or "{}").strip()
        result = json.loads(content)
        requested_fields = result.get("requested_fields")
        updates = result.get("updates")
        if not isinstance(requested_fields, list):
            requested_fields = []
        if not isinstance(updates, dict):
            updates = {}
        allowed = {"customer_name", "contact_info", "quote_items", "expected_start_date", "notes"}
        filtered_fields = [field for field in requested_fields if field in allowed]
        filtered_updates = {key: value for key, value in updates.items() if key in allowed}
        if filtered_updates.get("contact_info"):
            normalized = normalize_and_match_quote_extracted_data(
                {},
                {"contact_info": filtered_updates["contact_info"]},
                [],
            ).get("contact_info")
            filtered_updates["contact_info"] = normalized or filtered_updates["contact_info"]
        return {
            "requested_fields": filtered_fields,
            "updates": filtered_updates,
            "has_new_value": bool(result.get("has_new_value")) or bool(filtered_updates),
        }
    except Exception as e:
        logger.warning("Failed to extract quote update request: %s", str(e))
        return {"requested_fields": pending_fields or [], "updates": {}, "has_new_value": False}


def _build_quote_update_follow_up(requested_fields: list[str]) -> str:
    if not requested_fields:
        return "Sure, what would you like to change in the quote details?"

    field = requested_fields[0]
    prompts = {
        "customer_name": "Sure, what name should I update it to?",
        "contact_info": "Sure, what email address should I update it to?",
        "quote_items": "Sure, which product or quantity would you like to change?",
        "expected_start_date": "Sure, what should the new start date be?",
        "notes": "Sure, what notes should I update?",
    }
    return prompts.get(field, "Sure, what would you like to change it to?")


def _build_quote_recap_follow_up(requested_fields: list[str]) -> str:
    if requested_fields:
        field_labels = {
            "customer_name": "name",
            "contact_info": "contact email",
            "quote_items": "products",
            "expected_start_date": "expected start date",
            "notes": "notes",
        }
        readable = [field_labels.get(field, field) for field in requested_fields]
        return f"Sure, which details would you like me to recap first: {', '.join(readable)}?"
    return "Sure, which quote details would you like me to recap: name, contact email, products, start date, or notes?"


def _build_quote_confirmation_prompt(quote_state: dict, include_recap: bool = True) -> str:
    if include_recap:
        recap = _build_quote_confirmation_recap(quote_state)
        return (
            f"{recap} "
            "Please say 'confirm' or 'yes' to create the quote, or tell me what you'd like to change."
        )
    return "Please say 'confirm' or 'yes' to create the quote, or tell me what you'd like to change."


def _build_quote_targeted_recap(quote_state: dict, requested_fields: list[str]) -> str:
    """Build recap text for requested fields; if none specified, fallback to full recap."""
    return shared_build_quote_targeted_recap(quote_state, requested_fields)


async def _plan_acs_quote_tool_call(
    client,
    deployment: str,
    user_text: str,
    conversation_history: list,
    quote_state: dict,
) -> Optional[dict[str, Any]]:
    """Ask the model to decide whether the ACS quote workflow should call a tool."""
    state = _enrich_quote_state_with_delivery(quote_state)
    tools = [
        _wrap_chat_tool_schema(_quote_extraction_tool_schema),
        _wrap_chat_tool_schema(_update_quote_info_tool_schema),
        _wrap_chat_tool_schema(_send_quote_email_tool_schema),
    ]
    recent_history = [
        {
            "role": ("assistant" if msg.get("role") == "assistant" else "user"),
            "content": msg.get("content", ""),
        }
        for msg in (conversation_history or [])[-8:]
        if isinstance(msg, dict) and msg.get("content")
    ]
    logger.info("%s Quote planner start: has_quote=%s history_len=%d", _TEST_FLOW_LOG_PREFIX, bool(state), len(recent_history))
    payload = {
        "current_quote_state": state,
        "recent_history": recent_history,
        "latest_user_text": user_text,
    }

    planner_prompt = (
        "You are deciding whether the latest phone-call turn should trigger a quote workflow tool. "
        "Use tools only for quote workflow actions. "
        "Tool rules: "
        "1) Use extract_quote_info when the user requests a quote or provides quote details in free-form speech. "
        "If the user says things like 'I need a quote', 'I want pricing', 'can I get a quote', "
        "'quote', 'quotation', 'estimate', 'how much', 'price', or 'cost', you should call extract_quote_info immediately. "
        "2) Use update_quote_info when the user explicitly changes or corrects already-collected quote fields. "
        "3) Use send_quote_email when the quote is complete and the user explicitly confirms, sends, proceeds, or asks to resend the quote email. "
        "4) Do not call any tool for general Q&A or for quote recap questions such as asking what details were already provided. "
        "5) Prefer the smallest necessary tool action for the latest turn. "
        "6) When the latest turn starts a quote flow, prefer calling a tool instead of answering conversationally."
    )

    try:
        response = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": planner_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            tools=tools,
            tool_choice="auto",
            temperature=0.0,
            max_tokens=128,
        )
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []
        if not tool_calls:
            return None

        tool_call = tool_calls[0]
        function = getattr(tool_call, "function", None)
        if not function or not getattr(function, "name", None):
            return None

        arguments_text = getattr(function, "arguments", "") or "{}"
        try:
            arguments = json.loads(arguments_text)
        except json.JSONDecodeError:
            logger.warning("Invalid tool arguments from ACS quote planner: %s", arguments_text)
            arguments = {}

        planned_call = {
            "name": function.name,
            "arguments": arguments,
        }
        logger.info("ACS tool planner selected tool: %s", planned_call)
        return planned_call
    except Exception as e:
        logger.warning("ACS quote tool planning failed: %s", str(e))
        return None


async def _execute_acs_quote_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    call_connection_id: Optional[str],
    conversation_history: list,
    quote_state: dict,
) -> dict[str, Any]:
    """Execute an ACS quote tool call against local call state."""
    state = _enrich_quote_state_with_delivery(quote_state)
    arguments = arguments or {}

    if tool_name == "extract_quote_info":
        updated_state = await _extract_quote_info_phone(conversation_history, state)
        updated_state["delivery"] = dict(state.get("delivery") or _default_quote_delivery())
        updated_state = _store_acs_quote_state(call_connection_id, updated_state, conversation_history)
        return {"tool_name": tool_name, "quote_state": updated_state, "quote_updated": True}

    if tool_name == "update_quote_info":
        meaningful_update = any(
            arguments.get(field) not in (None, "", [])
            for field in ["customer_name", "contact_info", "quote_items", "expected_start_date", "notes"]
        )
        if not meaningful_update:
            updated_state = await _extract_quote_info_phone(conversation_history, state)
            updated_state["delivery"] = dict(state.get("delivery") or _default_quote_delivery())
            updated_state = _store_acs_quote_state(call_connection_id, updated_state, conversation_history)
            return {"tool_name": tool_name, "quote_state": updated_state, "quote_updated": True}

        products = fetch_available_products()
        product_names = [product["name"] for product in products]
        extracted = normalize_and_match_quote_extracted_data(
            state.get("extracted", {}),
            arguments,
            products,
            replace_quote_items=bool(arguments.get("replace_quote_items", True)),
        )
        updated_state = build_quote_state(extracted, product_names)
        updated_state["delivery"] = dict(state.get("delivery") or _default_quote_delivery())
        updated_state = _store_acs_quote_state(call_connection_id, updated_state, conversation_history)
        return {"tool_name": tool_name, "quote_state": updated_state, "quote_updated": True}

    if tool_name == "send_quote_email":
        send_state = state
        override_email = arguments.get("email_address") or arguments.get("contact_info")
        if override_email:
            extracted = dict(send_state.get("extracted", {}))
            extracted["contact_info"] = override_email
            products = fetch_available_products()
            product_names = [product["name"] for product in products]
            send_state = build_quote_state(
                normalize_and_match_quote_extracted_data(extracted, {}, products),
                product_names,
            )
            send_state["delivery"] = dict(state.get("delivery") or _default_quote_delivery())

        if not send_state.get("is_complete"):
            send_state = _store_acs_quote_state(call_connection_id, send_state, conversation_history)
            return {
                "tool_name": tool_name,
                "success": False,
                "error": "Quote information is incomplete",
                "quote_state": send_state,
                "quote_updated": False,
            }

        delivery = dict(send_state.get("delivery") or _default_quote_delivery())
        force_resend = bool(arguments.get("force_resend", False))
        if delivery.get("email_sent") and not force_resend:
            send_state["delivery"] = delivery
            send_state = _store_acs_quote_state(call_connection_id, send_state, conversation_history)
            return {
                "tool_name": tool_name,
                "success": True,
                "already_sent": True,
                "quote_state": send_state,
                "quote_result": delivery,
                "quote_updated": False,
            }

        quote_result = await create_quote_from_extracted(send_state.get("extracted", {}), fallback_to_mock=False)
        if not quote_result:
            send_state = _store_acs_quote_state(call_connection_id, send_state, conversation_history)
            return {
                "tool_name": tool_name,
                "success": False,
                "error": "Quote creation failed",
                "quote_state": send_state,
                "quote_updated": False,
            }

        send_state["delivery"] = {
            **delivery,
            "quote_id": quote_result.get("quote_id"),
            "quote_number": quote_result.get("quote_number"),
            "quote_url": quote_result.get("quote_url"),
            "email_sent": bool(quote_result.get("email_sent")),
            "email_error": quote_result.get("email_error"),
        }
        send_state = _store_acs_quote_state(call_connection_id, send_state, conversation_history)
        return {
            "tool_name": tool_name,
            "success": True,
            "quote_state": send_state,
            "quote_result": quote_result,
            "quote_updated": False,
        }

    return {"tool_name": tool_name, "success": False, "error": f"Unsupported tool: {tool_name}", "quote_state": state}


async def _classify_user_behavior_with_llm(
    client,
    deployment: str,
    user_text: str,
    conversation_history: list,
    has_quote_state: bool,
    quote_complete: bool,
) -> str:
    """Use LLM to classify the user's current intent into a branch behavior."""
    recent_history = [
        {
            "role": ("assistant" if msg.get("role") == "assistant" else "user"),
            "content": msg.get("content", ""),
        }
        for msg in conversation_history[-6:]
        if isinstance(msg, dict) and msg.get("content")
    ]

    classifier_prompt = (
        "Classify the user's intent for call-flow branching. "
        "Return JSON only with field 'behavior'.\n"
        "Allowed behaviors:\n"
        "- quote_request: user wants a quote/pricing/estimate, or is providing/updating quote details.\n"
        "- recall_quote_info: user asks to repeat/recap one or more previously provided fields.\n"
        "- modify_quote_info: user wants to change one or more previously provided quote fields.\n"
        "- general_qa: regular Q&A not about quote flow.\n"
        "Rules:\n"
        "1) If user is explicitly asking for previously provided details, choose recall_quote_info.\n"
        "2) If user is explicitly changing/updating already provided fields, choose modify_quote_info.\n"
        "3) If user is giving initial details for quote flow or asking for a quote, choose quote_request.\n"
        "4) If not quote related, choose general_qa.\n"
        "5) Use conversation context, not keywords only."
    )

    payload = {
        "has_quote_state": has_quote_state,
        "quote_complete": quote_complete,
        "recent_history": recent_history,
        "latest_user_text": user_text,
    }

    try:
        response = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": classifier_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=64,
        )
        content = (response.choices[0].message.content or "{}").strip()
        result = json.loads(content)
        behavior = result.get("behavior")
        if behavior in {"quote_request", "recall_quote_info", "modify_quote_info", "general_qa"}:
            logger.info("LLM behavior classification: %s", behavior)
            return behavior
        logger.warning("Unknown behavior from classifier: %s", behavior)
    except Exception as e:
        logger.warning("LLM behavior classification failed, fallback to general_qa: %s", str(e))

    return "general_qa"


async def _classify_top_level_intent(
    client,
    deployment: str,
    user_text: str,
    conversation_history: list,
    has_active_quote_state: bool,
    quote_complete: bool,
) -> dict[str, Any]:
    """
    Top-level intent gate that must be cleared before entering the quote workflow.

    Classifies the CURRENT utterance into one of:
      quote | company_info | routing | general_qa

    Returns a dict with:
      primary_intent        – dominant intent of this turn
      secondary_intents     – list of additional intents in this turn
      should_enter_quote_flow  – True only when primary_intent == "quote"
      should_preserve_quote_state – True when caller asked a side-question mid-quote
      reason                – brief LLM explanation (for logging)
    """
    recent_history = [
        {
            "role": ("assistant" if msg.get("role") == "assistant" else "user"),
            "content": msg.get("content", ""),
        }
        for msg in (conversation_history or [])[-6:]
        if isinstance(msg, dict) and msg.get("content")
    ]

    prompt = (
        "You are a top-level intent classifier for an AI phone receptionist.\n"
        "Classify the caller's CURRENT utterance and return JSON only with these keys:\n"
        "  primary_intent, secondary_intents, should_enter_quote_flow, should_preserve_quote_state, reason\n\n"
        "Intent categories:\n"
        "- quote: The caller is actively requesting a price quote or cost estimate, OR is directly providing\n"
        "  information for an in-progress quote (name, email, product choice, quantity, start date).\n"
        "  Only classify as quote if the CURRENT utterance clearly relates to quoting.\n"
        "- company_info: The caller asks about fixed company information — office address, business hours,\n"
        "  website, phone number, email address, or a general company description.\n"
        "- routing: The caller wants to be transferred to a department or person, or describes a service\n"
        "  need that implies they want to speak to someone specific.\n"
        "- general_qa: Anything else — general knowledge, clarifications, or unclear intent.\n\n"
        "Rules (STRICTLY FOLLOW):\n"
        "1. Classify based on the SEMANTIC MEANING of the CURRENT utterance only.\n"
        "2. Do NOT classify as quote just because 'quote', 'price', or 'cost' appeared earlier in history.\n"
        "3. If the utterance is clearly company_info or routing, do NOT classify it as quote.\n"
        "4. If the utterance contains two or more distinct intents, set primary_intent to the most dominant\n"
        "   and list the others in secondary_intents (array of strings).\n"
        "5. If intent is unclear, prefer general_qa rather than quote.\n"
        "6. Set should_enter_quote_flow=true ONLY when primary_intent is 'quote'.\n"
        "7. Set should_preserve_quote_state=true when has_active_quote_state=true AND the current intent\n"
        "   is non-quote (the caller asked a side-question while mid-quote).\n"
        "8. If secondary_intents contains 'quote' and has_active_quote_state=true, also set\n"
        "   should_preserve_quote_state=true.\n"
        "Example: caller says 'what are your office hours?' during a quote flow\n"
        "  → primary_intent=company_info, should_enter_quote_flow=false, should_preserve_quote_state=true"
    )

    payload = {
        "latest_user_text": user_text,
        "recent_history": recent_history,
        "has_active_quote_state": has_active_quote_state,
        "quote_complete": quote_complete,
    }

    default: dict[str, Any] = {
        "primary_intent": "general_qa",
        "secondary_intents": [],
        "should_enter_quote_flow": False,
        "should_preserve_quote_state": has_active_quote_state and not quote_complete,
        "reason": "classifier fallback",
    }

    try:
        response = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=200,
        )
        raw = json.loads((response.choices[0].message.content or "{}").strip())

        primary = raw.get("primary_intent", "general_qa")
        if primary not in {"quote", "company_info", "routing", "general_qa"}:
            primary = "general_qa"

        secondary = [
            i for i in (raw.get("secondary_intents") or [])
            if isinstance(i, str)
            and i in {"quote", "company_info", "routing", "general_qa"}
            and i != primary
        ]

        should_enter_quote = primary == "quote"
        should_preserve = bool(
            raw.get("should_preserve_quote_state",
                    has_active_quote_state and not quote_complete and not should_enter_quote)
        )

        return {
            "primary_intent": primary,
            "secondary_intents": secondary,
            "should_enter_quote_flow": should_enter_quote,
            "should_preserve_quote_state": should_preserve,
            "reason": str(raw.get("reason", ""))[:200],
        }
    except Exception as e:
        logger.warning("Top-level intent classification failed, defaulting to general_qa: %s", str(e))
        return default


async def _run_receptionist_path(
    client,
    deployment: str,
    endpoint: str,
    user_text: str,
    conversation_history: list,
    call_connection_id: Optional[str],
) -> tuple[str, bool]:
    """
    Handle a non-quote turn: company_info, routing, caller_intro, or general_qa.

    Returns (answer_text, already_played).
    already_played is always False here (non-streaming path).
    """
    fallback = "I am sorry, I could not process your question. Please try again later."

    pending_route = _get_pending_receptionist_route(call_connection_id)
    logger.info(
        "%s Receptionist path start: call=%s pending_route=%s",
        _TEST_FLOW_LOG_PREFIX,
        call_connection_id,
        pending_route,
    )

    receptionist_intent = await _classify_receptionist_intent_with_llm(
        client,
        deployment,
        user_text,
        conversation_history,
        pending_route,
    )

    caller_name = receptionist_intent.get("caller_name")
    company_name = receptionist_intent.get("company_name")
    if call_connection_id and call_connection_id in _active_acs_calls:
        if caller_name:
            _active_acs_calls[call_connection_id]["caller_name"] = caller_name
        if company_name:
            _active_acs_calls[call_connection_id]["caller_company"] = company_name

    if receptionist_intent.get("intent") == "caller_intro" and caller_name:
        logger.info("%s Receptionist intent resolved: call=%s intent=caller_intro", _TEST_FLOW_LOG_PREFIX, call_connection_id)
        return build_name_acknowledgement(caller_name), False

    if receptionist_intent.get("intent") == "company_info" and receptionist_intent.get("info_topic"):
        answer = build_company_info_answer(receptionist_intent["info_topic"])
        if answer:
            logger.info("%s Receptionist intent resolved: call=%s intent=company_info topic=%s", _TEST_FLOW_LOG_PREFIX, call_connection_id, receptionist_intent.get("info_topic"))
            return answer, False

    if receptionist_intent.get("intent") == "routing_request" or pending_route:
        logger.info("%s Receptionist intent resolved: call=%s intent=routing_request", _TEST_FLOW_LOG_PREFIX, call_connection_id)
        answer = _resolve_receptionist_route(
            call_connection_id,
            receptionist_intent.get("department"),
            company_name,
            receptionist_intent.get("is_architectural_firm", False),
        )
        if answer:
            return answer, False

    # General Q&A fallback
    system_prompt = build_receptionist_prompt()
    context_messages = [
        {"role": "system", "content": system_prompt},
        *[
            {
                "role": "assistant" if m.get("role") == "assistant" else "user",
                "content": m.get("content", ""),
            }
            for m in (conversation_history or [])[-6:]
            if isinstance(m, dict) and m.get("content")
        ],
        {"role": "user", "content": user_text},
    ]
    try:
        logger.info("%s Receptionist GPT fallback start: call=%s", _TEST_FLOW_LOG_PREFIX, call_connection_id)
        response = client.chat.completions.create(
            model=deployment,
            messages=context_messages,
            temperature=0.4,
            max_tokens=128,
        )
        full_text = (response.choices[0].message.content or "").strip()
        if not full_text:
            logger.warning("GPT returned empty answer in receptionist path, using fallback.")
            return fallback, False
        if call_connection_id and call_connection_id in _active_acs_calls:
            conv = _active_acs_calls[call_connection_id].get("conversation_history", [])
            conv.append({"role": "assistant", "content": full_text})
            if len(conv) > 10:
                conv = conv[-10:]
            _active_acs_calls[call_connection_id]["conversation_history"] = conv
            _active_acs_calls[call_connection_id]["last_answer"] = full_text
        logger.info("%s Receptionist GPT fallback done: call=%s answer_len=%d", _TEST_FLOW_LOG_PREFIX, call_connection_id, len(full_text))
        return full_text, False
    except Exception as e:
        logger.error("GPT call failed in receptionist path: %s", str(e))
        return fallback, False


async def _extract_quote_info_phone(conversation_history: list, current_state: dict) -> dict:
    """Extract quote info from phone call conversation. Delegates to shared quote_workflow logic."""
    logger.info(
        "🔍 EXTRACTING QUOTE INFO FROM CONVERSATION (phone): %d messages, state=%s",
        len(conversation_history or []),
        json.dumps(current_state, ensure_ascii=False, default=str)[:200],
    )
    try:
        result = await extract_quote_from_conversation(conversation_history, current_state)
        logger.info(
            "✅ Phone extraction: is_complete=%s, missing=%s",
            result.get("is_complete"),
            result.get("missing_fields"),
        )
        return result
    except Exception as e:
        logger.error("Error extracting quote info (phone): %s", str(e))
        return {
            "extracted": dict(current_state.get("extracted") or {}),
            "missing_fields": ["customer_name", "contact_info", "quote_items"],
            "products_available": [],
            "is_complete": False,
        }


def _generate_quote_collection_response(missing_fields: list, quote_state: dict) -> str:
    """根据缺失字段生成收集报价信息的回答"""
    return shared_generate_quote_collection_response(missing_fields, quote_state)


async def create_quote_from_state(call_connection_id: str, quote_state: dict) -> Optional[dict]:
    """从报价状态创建 Salesforce 报价"""
    try:
        logger.info("=" * 80)
        logger.info("🏭 CREATING QUOTE FROM STATE")
        logger.info("  Call ID: %s", call_connection_id)
        
        extracted = quote_state.get("extracted", {})
        customer_name = extracted.get("customer_name")
        contact_info = extracted.get("contact_info")
        quote_items = extracted.get("quote_items", [])
        expected_start_date = extracted.get("expected_start_date")
        notes = extracted.get("notes")
        
        logger.info("  Quote Information:")
        logger.info("    - Customer Name: %s", customer_name)
        logger.info("    - Contact Info: %s", contact_info)
        logger.info("    - Quote Items: %d items", len(quote_items))
        for idx, item in enumerate(quote_items, 1):
            logger.info("        [%d] %s x %s", idx, item.get("product_package"), item.get("quantity"))
        logger.info("    - Expected Start Date: %s", expected_start_date or "Not set")
        logger.info("    - Notes: %s", notes or "Not set")
        logger.info("=" * 80)
        
        if not customer_name or not contact_info or not quote_items:
            logger.error("❌ Incomplete quote information: customer_name=%s, contact_info=%s, quote_items=%s",
                        customer_name, contact_info, quote_items)
            return None
        
        quote_result = await create_quote_from_extracted(extracted, fallback_to_mock=False)
        if not quote_result:
            logger.error("❌ Failed to create quote in Salesforce")
            return None

        logger.info("✅ Quote created successfully:")
        logger.info("    - Quote ID: %s", quote_result.get("quote_id"))
        logger.info("    - Quote Number: %s", quote_result.get("quote_number"))
        logger.info("    - Quote URL: %s", quote_result.get("quote_url"))
        if quote_result.get("email_sent"):
            logger.info("✅ Quote email sent successfully to %s", contact_info)
        elif quote_result.get("email_error"):
            logger.warning("⚠️  Quote email failed: %s", quote_result.get("email_error"))
        else:
            logger.info("ℹ️  Quote email not sent")
        
        logger.info("=" * 80)
        logger.info("✅ QUOTE CREATION COMPLETED SUCCESSFULLY")
        logger.info("  Quote ID: %s", quote_result.get("quote_id"))
        logger.info("  Quote Number: %s", quote_result.get("quote_number"))
        logger.info("=" * 80)
        return quote_result
        
    except Exception as e:
        logger.error("Error creating quote from state: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())
        return None


async def generate_welcome_text_with_gpt() -> str:
    """
    使用 Azure OpenAI (GPT‑4o 系列) 生成电话欢迎语文本。
    
    优先使用你在 .env 里配置的 Azure OpenAI：
    - AZURE_OPENAI_ENDPOINT
    - AZURE_OPENAI_DEPLOYMENT（或者其他兼容部署）
    
    如果环境变量未配置或调用失败，则回退到固定文案。
    """
    default_text = WELCOME_MESSAGE

    try:
        # 延迟导入，避免在没装 openai 包时直接崩溃
        from azure.core.credentials import AzureKeyCredential
        from azure.identity import DefaultAzureCredential
        from openai import AzureOpenAI
    except Exception as e:
        logger.warning("Azure OpenAI SDK not available, using default welcome text. Error: %s", str(e))
        return default_text

    openai_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    # 优先使用专门的对话部署，其次是通用部署
    openai_deployment = (
        os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        or os.environ.get("AZURE_OPENAI_EXTRACTION_DEPLOYMENT")
        or "gpt-4o"
    )
    llm_key = os.environ.get("AZURE_OPENAI_API_KEY")

    # 立即输出使用的模型信息
    logger.info("🤖 GPT Model Configuration (Welcome) - Deployment: %s, Endpoint: %s", openai_deployment, openai_endpoint or "NOT SET")

    if not openai_endpoint or not openai_deployment:
        logger.warning("Azure OpenAI endpoint/deployment not configured. Using default welcome text.")
        return default_text

    if llm_key:
        credential = AzureKeyCredential(llm_key)
    else:
        credential = DefaultAzureCredential()

    try:
        if isinstance(credential, AzureKeyCredential):
            client = AzureOpenAI(
                api_key=credential.key,
                api_version="2024-02-15-preview",
                azure_endpoint=openai_endpoint,
            )
        else:
            token = credential.get_token("https://cognitiveservices.azure.com/.default").token
            client = AzureOpenAI(
                api_key=token,
                api_version="2024-02-15-preview",
                azure_endpoint=openai_endpoint,
            )

        prompt = (
            "Generate one short English phone greeting for George Fethers reception. "
            f"Use this exact sentence: {WELCOME_MESSAGE} "
            "Return only the sentence."
        )

        logger.info("🤖 Using GPT model: %s (endpoint: %s)", openai_deployment, openai_endpoint)
        logger.info("Calling Azure OpenAI to generate welcome text using deployment: %s", openai_deployment)
        response = client.chat.completions.create(
            model=openai_deployment,
            messages=[
                {"role": "system", "content": "You write short phone greetings in natural, polite English."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=64,
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            logger.warning("GPT returned empty welcome text, using default.")
            return default_text

        logger.info("Welcome text from GPT: %s", text)
        return text
    except Exception as e:
        logger.error("Failed to generate welcome text via Azure OpenAI: %s", str(e))
        return default_text


async def play_welcome_message(call_connection_id: str) -> None:
    """
    播放欢迎语音消息（使用 ACS Call Automation TTS）
    
    这是 Azure 官方推荐的方式：
    - 不需要音频文件
    - 不需要托管文件
    - 100% PSTN 兼容
    - 官方长期支持
    
    Args:
        call_connection_id: 通话连接 ID
    """
    acs_client = get_acs_client()
    
    if not acs_client:
        logger.error("❌ ACS client not available, cannot play welcome message")
        return
    
    try:
        # 从 CallAutomationClient 获取 CallConnectionClient
        call_connection = acs_client.get_call_connection(call_connection_id)
        
        # 🎯 最小可行 TTS 测试：先用固定的简短英文欢迎语，排除 GPT 文本 / 字符集等因素
        # 如果这一步通过，再切回 GPT 生成文本
        welcome_text = WELCOME_MESSAGE
        
        logger.info("%s Starting welcome TTS: call=%s text_len=%d", _TEST_FLOW_LOG_PREFIX, call_connection_id, len(welcome_text))
        
        # 使用 TextSource 直接播放文本（官方推荐方式）
        # 根据 SDK 版本，TextSource 可能在不同的位置
        try:
            text_source = _build_acs_text_source(welcome_text, "welcome")
        except ImportError:
            logger.error("❌ TextSource not found in SDK")
            logger.error("   Please ensure azure-communication-callautomation is installed")
            logger.error("   Run: pip install azure-communication-callautomation")
            return
        
        # 执行播放
        # ✅ 关键：play_source 作为第一个位置参数传入，不是关键字参数
        # ✅ 添加 operation_context 用于追踪播放完成事件
        play_result = call_connection.play_media(
            text_source,  # 位置参数，不是 play_source=...
            operation_context="welcome-tts"
        )
        
        logger.info("%s Welcome TTS started: call=%s voice=%s locale=%s", _TEST_FLOW_LOG_PREFIX, call_connection_id, _ACS_TTS_VOICE, _ACS_TTS_LOCALE)
        if hasattr(play_result, 'operation_id'):
            logger.info("%s Welcome TTS operation_id=%s", _TEST_FLOW_LOG_PREFIX, play_result.operation_id)
        
        # 更新通话状态
        if call_connection_id in _active_acs_calls:
            _active_acs_calls[call_connection_id]["welcome_playing"] = True
            _active_acs_calls[call_connection_id]["welcome_text"] = welcome_text
            
    except ImportError as import_error:
        logger.error("❌ Failed to import TextSource: %s", str(import_error))
        logger.error("   Please ensure azure-communication-callautomation is installed")
        logger.error("   Run: pip install azure-communication-callautomation")
    except Exception as e:
        logger.error("❌ Error in play_welcome_message: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())


async def start_speech_recognition(call_connection_id: str) -> None:
    """
    启动一次语音识别（让 ACS + Speech 听用户说话），结果通过
    Microsoft.Communication.RecognizeCompleted 事件回调。

    使用 ACS Call Automation 推荐签名：
    start_recognizing_media(RecognizeInputType.SPEECH, target_participant, ...)
    """
    acs_client = get_acs_client()
    if not acs_client:
        logger.error("❌ ACS client not available, cannot start speech recognition")
        return

    try:
        if RecognizeInputType is None or PhoneNumberIdentifier is None:
            logger.error("❌ SDK missing RecognizeInputType/PhoneNumberIdentifier, cannot start recognition")
            await speak_error_message(call_connection_id, debug_tag="start-recognize-sdk-missing")
            return

        call_connection = acs_client.get_call_connection(call_connection_id)
        call_info = _active_acs_calls.get(call_connection_id, {})
        
        # 优先使用保存的真正电话号码
        caller_phone = call_info.get("caller_phone")
        
        # 兜底：如果只有 rawId（如 "4:+613..."），strip 掉 "4:" 前缀
        if not caller_phone:
            caller_raw_id = call_info.get("caller_raw_id", "")
            if isinstance(caller_raw_id, str) and caller_raw_id.startswith("4:"):
                caller_phone = caller_raw_id[2:]  # 去掉 "4:" 前缀，得到 "+613..."
                logger.warning("Using caller_phone extracted from rawId (stripped '4:'): %s", caller_phone)
            else:
                logger.error("❌ Missing caller phone for call %s (caller_phone=%s, caller_raw_id=%s)", 
                           call_connection_id, caller_phone, caller_raw_id)
                await speak_error_message(call_connection_id, debug_tag="start-recognize-missing-caller")
                return

        # 使用真正的电话号码构造 PhoneNumberIdentifier（不能用 rawId）
        caller_identifier = PhoneNumberIdentifier(caller_phone)  # type: ignore[call-arg]
        logger.info("%s Starting recognition: call=%s caller_phone=%s", _TEST_FLOW_LOG_PREFIX, call_connection_id, caller_phone)

        call_connection.start_recognizing_media(
            RecognizeInputType.SPEECH,  # type: ignore[name-defined]
            caller_identifier,
            speech_language="en-US",  # 改为 en-US 匹配你的 TTS 配置
            initial_silence_timeout=10,  # 等对方开口的秒数
            end_silence_timeout=2,  # 停顿多久算一句结束
            operation_context="user-speech",
        )
        logger.info("%s Recognition started: call=%s", _TEST_FLOW_LOG_PREFIX, call_connection_id)

    except Exception as e:
        logger.error("❌ Error in start_speech_recognition: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())
        await speak_error_message(call_connection_id, debug_tag="start-recognize-exception")


async def play_answer_message(call_connection_id: str, answer_text: str) -> None:
    """
    播放 GPT 生成的回答文本（电话问答的“说回去”步骤）
    """
    acs_client = get_acs_client()
    if not acs_client:
        logger.error("❌ ACS client not available, cannot play answer message")
        return

    try:
        call_connection = acs_client.get_call_connection(call_connection_id)

        logger.info("%s Starting answer TTS: call=%s text_len=%d", _TEST_FLOW_LOG_PREFIX, call_connection_id, len(answer_text or ""))

        try:
            text_source = _build_acs_text_source(answer_text, "answer")
        except ImportError:
            logger.error("❌ TextSource not found in SDK (answer)")
            logger.error("   Please ensure azure-communication-callautomation is installed")
            return

        play_result = call_connection.play_media(
            text_source,
            operation_context="answer-tts",
        )

        logger.info("%s Answer TTS started: call=%s", _TEST_FLOW_LOG_PREFIX, call_connection_id)
        if hasattr(play_result, "operation_id"):
            logger.info("%s Answer TTS operation_id=%s", _TEST_FLOW_LOG_PREFIX, play_result.operation_id)

        if call_connection_id in _active_acs_calls:
            _active_acs_calls[call_connection_id]["last_answer"] = answer_text

    except Exception as e:
        if "8501" in str(e):
            logger.warning(
                "⚠️  play_answer_message skipped: call=%s is no longer in Established state (transferred or ended).",
                call_connection_id,
            )
        else:
            logger.error("❌ Error in play_answer_message: %s", str(e))
            import traceback
            logger.error("Traceback: %s", traceback.format_exc())


async def speak_error_message(call_connection_id: Optional[str], debug_tag: str = "") -> None:
    """
    在电话中简单播报“系统出错，用于调试”的提示，方便你感知到错误点。
    - 为避免递归错误，这里自己做一次独立的 TTS 调用，失败只记日志不再重试。
    """
    if not call_connection_id:
        return

    acs_client = get_acs_client()
    if not acs_client:
        logger.error("❌ ACS client not available, cannot speak_error_message (tag=%s)", debug_tag)
        return

    try:
        call_connection = acs_client.get_call_connection(call_connection_id)
        error_text = "Sorry, there was an internal error while handling your request. This call is for debugging."

        logger.info("📢 Speaking error message (tag=%s) on call %s", debug_tag, call_connection_id)

        try:
            text_source = _build_acs_text_source(error_text, f"error-{debug_tag or 'generic'}")
        except ImportError:
            logger.error("❌ TextSource not available when trying to speak error (tag=%s)", debug_tag)
            return

        try:
            call_connection.play_media(
                text_source,
                operation_context=f"error-tts-{debug_tag or 'generic'}",
            )
            logger.info("✅ Error message playback started (tag=%s)", debug_tag)
        except Exception as play_err:
            if "8501" in str(play_err):
                logger.warning("⚠️  speak_error_message skipped: call no longer Established (tag=%s).", debug_tag)
            else:
                logger.error("Failed to play error message (tag=%s): %s", debug_tag, str(play_err))
                import traceback
                logger.error("Traceback: %s", traceback.format_exc())

    except Exception as e:
        logger.error("❌ speak_error_message failed (tag=%s): %s", debug_tag, str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())


async def handle_acs_webhook(request: web.Request) -> web.Response:
    """
    处理 ACS Call Automation 的 webhook 事件
    
    这是主要的 webhook 端点，ACS 会将所有事件发送到这里。
    注意：ACS/Event Grid 可能一次 POST 一个事件，也可能 POST 事件数组，这里会逐个处理。
    """
    try:
        # 解析事件数据
        raw_data = await request.json()
        
        # 统一转换为事件列表，方便逐个处理
        if isinstance(raw_data, list):
            events = raw_data
            if not events:
                logger.warning("Received empty event array")
                return web.json_response({"status": "received", "message": "Empty event array"}, status=200)
            logger.info("%s Webhook batch received: count=%d", _TEST_FLOW_LOG_PREFIX, len(events))
        else:
            events = [raw_data]
        
        for event_data in events:
            # 记录收到的事件
            # Event Grid 使用 eventType，ACS Call Automation 使用 type 或 kind
            event_type = event_data.get("eventType") or event_data.get("type") or event_data.get("kind") or "Unknown"
            logger.info("%s Webhook event: type=%s", _TEST_FLOW_LOG_PREFIX, event_type)
            
            # 处理 Event Grid 订阅验证事件（重要！）
            if event_type == "Microsoft.EventGrid.SubscriptionValidationEvent":
                # Event Grid 验证事件的数据结构
                event_data_obj = event_data.get("data", {})
                validation_code = event_data_obj.get("validationCode")
                
                if validation_code:
                    logger.info("✅ Event Grid subscription validation received")
                    logger.info("   Validation Code: %s", validation_code)
                    # 返回验证码以完成订阅验证
                    # Event Grid 期望的响应格式：{"validationResponse": "code"}
                    response_data = {
                        "validationResponse": validation_code
                    }
                    logger.info("   Sending validation response: %s", response_data)
                    # 验证事件只会单独发，这里可以直接返回
                    return web.json_response(response_data, status=200)
                else:
                    logger.warning("⚠️  Validation event received but no validationCode found")
                    logger.warning("   Event data structure: %s", json.dumps(event_data, indent=2))
                    continue
            
            # 处理来电事件
            if event_type == "Microsoft.Communication.IncomingCall":
                await handle_incoming_call_event(event_data)
            
            # 处理通话连接事件
            elif event_type == "Microsoft.Communication.CallConnected":
                await handle_call_connected_event(event_data)
            
            # 处理通话断开事件
            elif event_type == "Microsoft.Communication.CallDisconnected":
                await handle_call_disconnected_event(event_data)
            
            # 处理播放完成事件
            elif event_type == "Microsoft.Communication.PlayCompleted":
                await handle_play_completed_event(event_data)
            
            # 处理播放失败事件
            elif event_type == "Microsoft.Communication.PlayFailed":
                await handle_play_failed_event(event_data)
            
            # 处理媒体流建立事件
            elif event_type == "Microsoft.Communication.MediaStreamingStarted":
                data = event_data.get("data", {}) or {}
                call_connection_id = data.get("callConnectionId")
                logger.info("✅ Media streaming started for call: %s", call_connection_id)

            # 处理语音识别完成事件（旧版 ACS 识别+TTS 流程，默认关闭）
            elif event_type == "Microsoft.Communication.RecognizeCompleted":
                if _use_legacy_acs_recognize_flow():
                    await handle_recognize_completed(event_data)
                else:
                    logger.info("Ignoring RecognizeCompleted because ACS_USE_LEGACY_RECOGNIZE is disabled; using GPT-4o Realtime bridge.")

            # 处理语音识别失败事件（旧版流程）
            elif event_type == "Microsoft.Communication.RecognizeFailed":
                if _use_legacy_acs_recognize_flow():
                    await handle_recognize_failed_event(event_data)
                else:
                    logger.info("Ignoring RecognizeFailed because ACS_USE_LEGACY_RECOGNIZE is disabled; using GPT-4o Realtime bridge.")
            
            # 其他事件类型
            else:
                logger.info("ℹ️  Unhandled event type: %s", event_type)
        
        # 所有事件处理完统一返回 200
        return web.json_response({"status": "received"}, status=200)
        
    except json.JSONDecodeError as e:
        logger.error("❌ Failed to parse JSON: %s", str(e))
        return web.json_response({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.error("❌ Error processing webhook: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())
        return web.json_response({"error": str(e)}, status=500)


async def handle_acs_ping(request: web.Request) -> web.Response:
    """测试路由 - 验证 ACS 路由是否已注册"""
    return web.json_response({
        "status": "ok",
        "message": "ACS routes are registered",
        "timestamp": time.time()
    })


async def handle_get_active_calls(request: web.Request) -> web.Response:
    """获取当前活跃的 ACS 通话列表"""
    return web.json_response({
        "active_calls": list(_active_acs_calls.values()),
        "count": len(_active_acs_calls)
    })


async def handle_get_call_status(request: web.Request) -> web.Response:
    """获取特定通话的状态"""
    call_connection_id = request.match_info.get("call_connection_id")
    
    if not call_connection_id:
        return web.json_response({"error": "Missing call_connection_id"}, status=400)
    
    if call_connection_id in _active_acs_calls:
        return web.json_response(_active_acs_calls[call_connection_id])
    else:
        return web.json_response({"error": "Call not found"}, status=404)


async def handle_hangup_call(request: web.Request) -> web.Response:
    """挂断指定的通话"""
    call_connection_id = request.match_info.get("call_connection_id")
    
    if not call_connection_id:
        return web.json_response({"error": "Missing call_connection_id"}, status=400)
    
    acs_client = get_acs_client()
    if not acs_client:
        return web.json_response({"error": "ACS client not configured"}, status=503)
    
    try:
        # 获取 CallConnectionClient
        call_connection_client = acs_client.get_call_connection(call_connection_id)
        
        # 挂断通话
        call_connection_client.hang_up(is_for_everyone=True)
        
        # 清理通话记录
        if call_connection_id in _active_acs_calls:
            _active_acs_calls.pop(call_connection_id)
        
        logger.info("Call hung up - Connection ID: %s", call_connection_id)
        
        return web.json_response({
            "success": True,
            "call_connection_id": call_connection_id,
            "message": "Call hung up successfully"
        })
        
    except Exception as e:
        logger.error("Error hanging up call: %s", str(e))
        return web.json_response({"error": str(e)}, status=500)


def register_acs_routes(app: web.Application) -> None:
    """
    注册 ACS 相关的路由到 aiohttp 应用

    使用示例：
        from acs_call_handler import register_acs_routes
        register_acs_routes(app)
    """
    # 非常显眼的日志，用于验证是否被调用
    logger.error("### ACS ROUTES REGISTER() CALLED ###")
    logger.info("Registering ACS call handler routes...")

    # 加载环境变量
    if not os.environ.get("RUNNING_IN_PRODUCTION"):
        load_dotenv()

    # ── 打印当前语音入口模式和 legacy fallback 状态 ──────────────────────────
    _voice_mode = os.environ.get("VOICE_ENTRY_MODE", "web").strip().lower()
    logger.info("Voice entry mode: %s", _voice_mode)
    _legacy_enabled = _use_legacy_acs_recognize_flow()
    if _legacy_enabled:
        logger.info(
            "ACS legacy recognize fallback: enabled "
            "(ACS Recognize → transcript → intent → TTS pipeline is active)"
        )
    else:
        logger.info(
            "ACS legacy recognize fallback: disabled "
            "(ACS audio bridged directly to GPT Realtime — lower latency path)"
        )
    # ─────────────────────────────────────────────────────────────────────────

    # 初始化 ACS 客户端（如果配置了）
    get_acs_client()
    
    # 注册路由
    try:
        app.router.add_get("/api/acs/ping", handle_acs_ping)  # 测试路由，用于验证路由是否注册
        logger.info("✓ Registered: GET /api/acs/ping")
    except Exception as e:
        logger.error("✗ Failed to register GET /api/acs/ping: %s", str(e))
    
    try:
        app.router.add_post("/api/acs/calls/events", handle_acs_webhook)
        logger.info("✓ Registered: POST /api/acs/calls/events")
    except Exception as e:
        logger.error("✗ Failed to register POST /api/acs/calls/events: %s", str(e))
    
    try:
        app.router.add_get("/api/acs/calls", handle_get_active_calls)
        logger.info("✓ Registered: GET /api/acs/calls")
    except Exception as e:
        logger.error("✗ Failed to register GET /api/acs/calls: %s", str(e))
    
    try:
        app.router.add_get("/api/acs/calls/{call_connection_id}", handle_get_call_status)
        logger.info("✓ Registered: GET /api/acs/calls/{call_connection_id}")
    except Exception as e:
        logger.error("✗ Failed to register GET /api/acs/calls/{call_connection_id}: %s", str(e))
    
    try:
        app.router.add_delete("/api/acs/calls/{call_connection_id}", handle_hangup_call)
        logger.info("✓ Registered: DELETE /api/acs/calls/{call_connection_id}")
    except Exception as e:
        logger.error("✗ Failed to register DELETE /api/acs/calls/{call_connection_id}: %s", str(e))
    
    # 验证路由是否真的被添加了
    all_routes = []
    for route in app.router.routes():
        if hasattr(route, 'method') and hasattr(route, 'path'):
            all_routes.append(f"{route.method} {route.path}")
        elif hasattr(route, '_method') and hasattr(route, '_path'):
            all_routes.append(f"{route._method} {route._path}")
    
    acs_routes = [r for r in all_routes if '/api/acs' in r]
    logger.info("ACS routes in router: %s", acs_routes)
    logger.info("Total routes in app: %d", len(all_routes))
    
    logger.info("ACS call handler routes registered")
    logger.error("### ACS ROUTES REGISTERED SUCCESSFULLY ###")


# 测试函数
async def test_acs_connection() -> bool:
    """测试 ACS 连接是否正常"""
    acs_client = get_acs_client()
    if not acs_client:
        logger.error("ACS client not available")
        return False
    
    logger.info("ACS client is available and ready")
    return True


if __name__ == "__main__":
    # 独立测试模式
    import asyncio
    
    async def main():
        # 加载环境变量
        load_dotenv()
        
        # 测试连接
        logger.info("Testing ACS connection...")
        success = await test_acs_connection()
        
        if success:
            logger.info("✓ ACS connection test passed")
        else:
            logger.error("✗ ACS connection test failed")
            logger.info("Please check your ACS_CONNECTION_STRING environment variable")
    
    asyncio.run(main())
