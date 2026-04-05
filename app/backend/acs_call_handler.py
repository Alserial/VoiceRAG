"""
Azure Communication Services (ACS) Call Automation Handler
用于测试和处理来自 ACS 的电话来电

这个模块实现了：
1. 接收 ACS Call Automation 的 webhook 事件
2. 自动接听来电
3. 建立 ACS + /realtime WebSocket 音频桥接（mixed-mono）
4. 记录通话状态

环境变量配置：
- ACS_CONNECTION_STRING: Azure Communication Services 连接字符串
- ACS_CALLBACK_URL: 你的公网可访问的回调 URL (例如: https://yourapp.com/api/acs/calls/events)
- ACS_PHONE_NUMBER: 你的 ACS 电话号码 (例如: +1234567890)
- ACS_REALTIME_WS_URL: 可选，显式指定媒体桥接 WebSocket 地址（默认根据 ACS_CALLBACK_URL 推导为 wss://<host>/realtime）
- ACS_USE_LEGACY_RECOGNIZE: 可选，默认 true；控制是否使用旧版 ACS 识别+TTS 流程
"""

import asyncio
import json
import inspect
import logging
import os
import re
import tempfile
import time
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
    fetch_available_products,
    generate_quote_collection_response as shared_generate_quote_collection_response,
    normalize_and_match_quote_extracted_data,
)

# 先获取 logger，供后续导入失败时使用
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voicerag")

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
    """是否启用旧版 ACS 识别+TTS 逻辑（默认开启）。"""
    return os.environ.get("ACS_USE_LEGACY_RECOGNIZE", "true").strip().lower() in {"1", "true", "yes", "on"}


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
    """构造 ACS 媒体流目标 WebSocket（默认 /realtime，callerId 作为 session）。"""
    explicit_ws_url = os.environ.get("ACS_REALTIME_WS_URL", "").strip()
    if explicit_ws_url:
        separator = "&" if "?" in explicit_ws_url else "?"
        return f"{explicit_ws_url}{separator}session={quote(session_key)}"

    callback_url = os.environ.get("ACS_CALLBACK_URL", "").strip()
    if not callback_url:
        raise ValueError("ACS_CALLBACK_URL is required to derive /realtime websocket url")

    parsed = urlparse(callback_url)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    realtime_path = "/realtime"
    return urlunparse((ws_scheme, parsed.netloc, realtime_path, "", f"session={quote(session_key)}", ""))


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


def _build_acs_progress_summary(call_connection_id: str) -> str:
    call_info = _active_acs_calls.get(call_connection_id, {})
    quote_state = _enrich_quote_state_with_delivery(call_info.get("quote_state"))
    extracted = quote_state.get("extracted", {})
    conversation_history = call_info.get("conversation_history", [])

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
            "Recent conversation:",
        ]
    )

    for message in conversation_history[-10:]:
        if isinstance(message, dict):
            role = str(message.get("role", "unknown")).upper()
            content = str(message.get("content", "")).strip()
            if content:
                lines.append(f"{role}: {content}")

    return "\n".join(lines).strip() + "\n"


async def _send_acs_progress_email_if_available(call_connection_id: str) -> bool:
    call_info = _active_acs_calls.get(call_connection_id, {})
    quote_state = _enrich_quote_state_with_delivery(call_info.get("quote_state"))
    contact_info = str(quote_state.get("extracted", {}).get("contact_info") or "").strip()
    if "@" not in contact_info:
        logger.info("Skipping ACS progress email because no valid email is available for call %s", call_connection_id)
        return False

    temp_path = None
    try:
        summary_text = _build_acs_progress_summary(call_connection_id)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as temp_file:
            temp_file.write(summary_text)
            temp_path = temp_file.name

        sent = await send_conversation_email(contact_info, temp_path, call_connection_id)
        logger.info("ACS progress email send result for %s: %s", call_connection_id, sent)
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
    """启动 ACS -> /realtime WebSocket 媒体桥接。"""
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
                "started_at": time.time()
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
            _active_acs_calls.pop(call_connection_id)
            logger.info("   Removed call from active calls: %s", call_connection_id)
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
        
        logger.info("🎵 Play Completed - Connection ID: %s, Operation Context: %s", call_connection_id, operation_context)
        
        if call_connection_id and call_connection_id in _active_acs_calls:
            if operation_context == "welcome-tts":
                # 欢迎语播放完成，启动第一次语音识别
                _active_acs_calls[call_connection_id]["welcome_played"] = True
                logger.info("Welcome message playback completed, starting first speech recognition...")
                await start_speech_recognition(call_connection_id)
            elif operation_context == "answer-tts":
                # 回答播放完成，重新启动识别，实现多轮对话
                if _active_acs_calls[call_connection_id].get("hangup_after_playback"):
                    logger.info("Answer playback completed and hangup_after_playback is set; ending call.")
                    await _hang_up_acs_call(call_connection_id)
                else:
                    logger.info("Answer playback completed, restarting speech recognition for next question...")
                    await start_speech_recognition(call_connection_id)
            elif operation_context == "answer-tts-stream":
                # 流式回答的一段播完，播下一段或结束并重新启动识别
                logger.info("[STREAM] PlayCompleted (answer-tts-stream), call=%s", call_connection_id)
                if call_connection_id in _active_acs_calls:
                    _active_acs_calls[call_connection_id]["answer_stream_playing"] = False
                started = await _play_next_answer_chunk(call_connection_id)
                if not started:
                    logger.info("[STREAM] No more chunks, restarting speech recognition for next turn")
                    await start_speech_recognition(call_connection_id)
            else:
                # 其他播放完成事件（可能是错误提示等），不重新启动识别
                logger.info("Play completed for context: %s (not restarting recognition)", operation_context)
        
    except Exception as e:
        logger.error("Error handling play completed event: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())


async def handle_play_failed_event(event_data: dict[str, Any]) -> None:
    """处理音频播放失败事件（详细打印 Cognitive Services 错误信息）"""
    try:
        data = event_data.get("data", {}) or {}
        call_connection_id = data.get("callConnectionId") or event_data.get("callConnectionId")

        result_info = data.get("resultInformation", {}) or {}
        logger.warning("🔊 Play failed - call=%s", call_connection_id)
        logger.warning("resultInformation=%s", json.dumps(result_info, ensure_ascii=False))

        # 有时更深一层 details 里还有具体的 speechErrorCode / subcode
        if isinstance(result_info, dict) and "details" in result_info:
            logger.warning("resultInformation.details=%s", json.dumps(result_info["details"], ensure_ascii=False))

        # 为了能完整还原问题，这里暂时把整个 event 打出来（截断到 5000 字符）
        try:
            logger.warning("raw event=%s", json.dumps(event_data, ensure_ascii=False)[:5000])
        except Exception:
            logger.warning("raw event=<unserializable>")

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
        data = event_data.get("data", {}) or {}
        call_connection_id = data.get("callConnectionId")

        logger.info("🗣️ RecognizeCompleted for call: %s", call_connection_id)
        logger.info("Recognize event data: %s", json.dumps(data, ensure_ascii=False))

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
            logger.warning("RecognizeCompleted received but no transcript text found.")
            if call_connection_id:
                empty_count = _get_empty_recognition_count(call_connection_id) + 1
                _set_empty_recognition_count(call_connection_id, empty_count)
                logger.info("Transcript was empty; consecutive empty recognition count=%d", empty_count)
                if empty_count >= 10:
                    await _end_call_after_repeated_empty_input(call_connection_id)
                else:
                    await play_answer_message(call_connection_id, "I didn't catch that. Could you please say that again?")
            return

        logger.info("User said (transcript): %s", user_text)
        _reset_empty_recognition_count(call_connection_id)

        # 初始化通话的报价状态（如果还没有）
        if call_connection_id and call_connection_id not in _active_acs_calls:
            _active_acs_calls[call_connection_id] = {
                "call_connection_id": call_connection_id,
                "status": "active",
            }
            logger.info("📞 Initialized new call state for: %s", call_connection_id)
        
        # 处理报价逻辑
        if call_connection_id:
            call_info = _active_acs_calls.get(call_connection_id, {})
            quote_state = call_info.get("quote_state", {})
            conversation_history = call_info.get("conversation_history", [])
            
            # 打印当前对话历史
            logger.info("=" * 80)
            logger.info("📝 CONVERSATION HISTORY (call: %s, messages: %d)", call_connection_id, len(conversation_history))
            for idx, msg in enumerate(conversation_history[-5:], 1):  # 只打印最近 5 条
                role = msg.get("role", "unknown")
                content = msg.get("content", "")[:100]  # 截断到 100 字符
                logger.info("  [%d] %s: %s", idx, role.upper(), content)
            logger.info("=" * 80)
            
            # 打印当前报价状态
            if quote_state:
                logger.info("📋 CURRENT QUOTE STATE (call: %s)", call_connection_id)
                extracted = quote_state.get("extracted", {})
                logger.info("  - Customer Name: %s", extracted.get("customer_name") or "NOT SET")
                logger.info("  - Contact Info: %s", extracted.get("contact_info") or "NOT SET")
                quote_items = extracted.get("quote_items", [])
                if quote_items:
                    logger.info("  - Quote Items (%d):", len(quote_items))
                    for item in quote_items:
                        logger.info("      * %s x %s", item.get("product_package", "N/A"), item.get("quantity", "N/A"))
                else:
                    logger.info("  - Quote Items: NOT SET")
                logger.info("  - Expected Start Date: %s", extracted.get("expected_start_date") or "NOT SET")
                logger.info("  - Notes: %s", extracted.get("notes") or "NOT SET")
                logger.info("  - Missing Fields: %s", quote_state.get("missing_fields", []))
                logger.info("  - Is Complete: %s", quote_state.get("is_complete", False))
            else:
                logger.info("📋 NO QUOTE STATE (call: %s) - Regular conversation", call_connection_id)
            
            # 先更新报价状态（提取信息）；普通问答为流式播报，返回 already_played 表示是否已边生成边播
            answer_text, quote_updated, already_played = await generate_answer_text_with_gpt(
                user_text, call_connection_id
            )
            
            # 重新获取更新后的报价状态
            updated_call_info = _active_acs_calls.get(call_connection_id, {})
            quote_state = updated_call_info.get("quote_state", {})
            updated_conversation = updated_call_info.get("conversation_history", [])
            
            # 打印更新后的对话历史
            if len(updated_conversation) > len(conversation_history):
                logger.info("📝 UPDATED CONVERSATION HISTORY (call: %s, total messages: %d)", 
                          call_connection_id, len(updated_conversation))
                for idx, msg in enumerate(updated_conversation[-3:], len(updated_conversation) - 2):
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")[:100]
                    logger.info("  [%d] %s: %s", idx, role.upper(), content)
            
            # 打印更新后的报价状态
            if quote_state:
                logger.info("📋 UPDATED QUOTE STATE (call: %s)", call_connection_id)
                extracted = quote_state.get("extracted", {})
                logger.info("  - Customer Name: %s", extracted.get("customer_name") or "NOT SET")
                logger.info("  - Contact Info: %s", extracted.get("contact_info") or "NOT SET")
                quote_items = extracted.get("quote_items", [])
                if quote_items:
                    logger.info("  - Quote Items (%d):", len(quote_items))
                    for item in quote_items:
                        logger.info("      * %s x %s", item.get("product_package", "N/A"), item.get("quantity", "N/A"))
                logger.info("  - Missing Fields: %s", quote_state.get("missing_fields", []))
                logger.info("  - Is Complete: %s", quote_state.get("is_complete", False))
            
            logger.info(
                "🔍 ACS response generated via tool-planned flow or regular Q&A - quote_updated=%s, quote_complete=%s",
                quote_updated,
                quote_state.get("is_complete", False),
            )
        else:
            logger.info("➡️  BRANCH: Entering SIMPLE MODE branch (no call_connection_id)")
            # 没有 call_connection_id，使用简单模式
            answer_text, _, already_played = await generate_answer_text_with_gpt(user_text, None)
            already_played = False  # 简单模式无流式播报

        # 播放回答（流式分支已在 generate 中边生成边播，此处跳过）
        if call_connection_id and not already_played:
            queued = _queue_answer_text_for_tts(call_connection_id, answer_text)
            if not queued:
                await play_answer_message(call_connection_id, answer_text)
        elif call_connection_id and already_played:
            logger.info("[STREAM] Answer already played via stream, skipping play_answer_message")
        elif not call_connection_id:
            logger.warning("No call_connection_id in RecognizeCompleted event; cannot play answer.")

    except Exception as e:
        logger.error("Error handling RecognizeCompleted event: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())
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


# 流式回答分块：按句或按长度切分，减少首字延迟
_STREAM_MIN_CHUNK_LEN = 25
_STREAM_MAX_CHUNK_LEN = 80
_SENTENCE_END_RE = re.compile(r"[.!?]\s*")


def _flush_stream_buffer(buffer: str, call_connection_id: Optional[str]) -> str:
    """
    将 buffer 中可播报的句子/片段取出，加入队列并触发播放，返回剩余部分。
    """
    if not call_connection_id or call_connection_id not in _active_acs_calls:
        return buffer
    remainder = buffer
    while True:
        remainder = remainder.lstrip()
        if len(remainder) < _STREAM_MIN_CHUNK_LEN:
            return remainder
        # 优先在句号、问号、感叹号处切分
        match = _SENTENCE_END_RE.search(remainder)
        if match:
            end = match.end()
            chunk = remainder[:end].strip()
            remainder = remainder[end:]
            if len(chunk) >= _STREAM_MIN_CHUNK_LEN:
                _ensure_answer_stream_state(call_connection_id)
                _active_acs_calls[call_connection_id]["answer_chunk_queue"].append(chunk)
                qlen = len(_active_acs_calls[call_connection_id]["answer_chunk_queue"])
                logger.info("[STREAM] Flush chunk (sentence) -> queue len=%d, text=%s", qlen, chunk[:60] + ("..." if len(chunk) > 60 else ""))
                asyncio.create_task(_play_next_answer_chunk(call_connection_id))
                continue
        # 无句号则按长度在空格处切
        if len(remainder) >= _STREAM_MAX_CHUNK_LEN:
            idx = remainder.rfind(" ", _STREAM_MIN_CHUNK_LEN, _STREAM_MAX_CHUNK_LEN + 1)
            if idx <= 0:
                idx = min(_STREAM_MAX_CHUNK_LEN, len(remainder))
            chunk = remainder[: idx + 1 if idx > 0 else idx].strip()
            remainder = remainder[idx + 1 if idx > 0 else idx :].lstrip()
            if chunk:
                _ensure_answer_stream_state(call_connection_id)
                _active_acs_calls[call_connection_id]["answer_chunk_queue"].append(chunk)
                qlen = len(_active_acs_calls[call_connection_id]["answer_chunk_queue"])
                logger.info("[STREAM] Flush chunk (length) -> queue len=%d, text=%s", qlen, chunk[:60] + ("..." if len(chunk) > 60 else ""))
                asyncio.create_task(_play_next_answer_chunk(call_connection_id))
            continue
        return remainder


def _chunk_text_for_tts(answer_text: str) -> list[str]:
    """将完整回答切成较短 TTS 片段，优先按句切分。"""
    remainder = (answer_text or "").strip()
    if not remainder:
        return []

    chunks: list[str] = []
    while remainder:
        remainder = remainder.lstrip()
        if not remainder:
            break

        match = _SENTENCE_END_RE.search(remainder)
        if match and match.end() <= _STREAM_MAX_CHUNK_LEN:
            chunk = remainder[:match.end()].strip()
            remainder = remainder[match.end():]
            if chunk:
                chunks.append(chunk)
            continue

        if len(remainder) <= _STREAM_MAX_CHUNK_LEN:
            chunks.append(remainder)
            break

        idx = remainder.rfind(" ", _STREAM_MIN_CHUNK_LEN, _STREAM_MAX_CHUNK_LEN + 1)
        if idx <= 0:
            idx = _STREAM_MAX_CHUNK_LEN
        chunk = remainder[:idx].strip()
        remainder = remainder[idx:].lstrip()
        if chunk:
            chunks.append(chunk)

    return chunks


def _queue_answer_text_for_tts(call_connection_id: str, answer_text: str) -> bool:
    """将回答加入 TTS 队列，实现旧版 ACS 链路的分块即时播报。"""
    if call_connection_id not in _active_acs_calls:
        return False

    chunks = _chunk_text_for_tts(answer_text)
    if not chunks:
        return False

    _ensure_answer_stream_state(call_connection_id)
    _active_acs_calls[call_connection_id]["answer_chunk_queue"] = chunks
    _active_acs_calls[call_connection_id]["answer_stream_playing"] = False
    logger.info("[STREAM] Queued %d TTS chunk(s) for call=%s", len(chunks), call_connection_id)
    asyncio.create_task(_play_next_answer_chunk(call_connection_id))
    return True


async def generate_answer_text_with_gpt(user_text: str, call_connection_id: Optional[str] = None) -> tuple[str, bool, bool]:
    """
    使用 Azure OpenAI 根据用户语音转成的文本生成回答（电话版 Q&A 核心逻辑）。
    
    支持报价功能：
    - 检测报价意图
    - 收集报价信息
    - 生成自然对话回答
    
    普通问答分支使用流式输出，边生成边加入 TTS 队列播报，降低首字延迟。
    
    Returns:
        tuple[str, bool, bool]: (回答文本, 报价状态是否更新, 是否已通过流式播报无需再 play_answer_message)
    """
    # 如果 GPT 不可用，就回个固定文案，避免电话静音
    fallback = "I am sorry, I could not process your question. Please try again later."

    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.identity import DefaultAzureCredential
        from openai import AzureOpenAI
    except Exception as e:
        logger.warning("Azure OpenAI SDK not available, using fallback answer. Error: %s", str(e))
        return fallback, False, False

    openai_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    openai_deployment = (
        os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        or os.environ.get("AZURE_OPENAI_EXTRACTION_DEPLOYMENT")
        or "gpt-4o-mini"
    )
    llm_key = os.environ.get("AZURE_OPENAI_API_KEY")

    # 立即输出使用的模型信息
    logger.info("🤖 GPT Model Configuration - Deployment: %s, Endpoint: %s", openai_deployment, openai_endpoint or "NOT SET")

    if not openai_endpoint or not openai_deployment:
        logger.warning("Azure OpenAI endpoint/deployment not configured. Using fallback answer.")
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
        if not conversation_history or conversation_history[-1].get("content") != user_text:
            conversation_history.append({"role": "user", "content": user_text})
            logger.info("💬 Added user message to conversation history (total: %d messages)", len(conversation_history))
        # 只保留最近 10 条消息
        if len(conversation_history) > 10:
            conversation_history = conversation_history[-10:]
            logger.info("💬 Trimmed conversation history to last 10 messages")
        
        # 更新通话状态中的对话历史
        if call_connection_id and call_connection_id in _active_acs_calls:
            _active_acs_calls[call_connection_id]["conversation_history"] = conversation_history
            logger.info("💾 Saved conversation history to call state (call: %s, messages: %d)", 
                       call_connection_id, len(conversation_history))

        quote_state = _enrich_quote_state_with_delivery(quote_state) if quote_state else {}
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
                return answer_text, quote_updated, False

            _set_pending_quote_update(call_connection_id, {"requested_fields": requested_fields or pending_update.get("requested_fields", [])})
            answer_text = _build_quote_update_follow_up(requested_fields or pending_update.get("requested_fields", []))
            _append_assistant_message(call_connection_id, answer_text)
            logger.info("Answer text from pending-update follow-up: %s", answer_text)
            return answer_text, False, False
        
        planned_tool_call = await _plan_acs_quote_tool_call(
            client,
            openai_deployment,
            user_text,
            conversation_history,
            quote_state,
        )
        quote_updated = False

        if planned_tool_call and call_connection_id:
            logger.info("➡️  BRANCH: Entering ACS TOOL-CALLING quote flow")
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
                return answer_text, False, False
            if recap_request.get("needs_clarification") and not requested_fields:
                _set_pending_quote_recap(
                    call_connection_id,
                    {"requested_fields": ["customer_name", "contact_info", "quote_items", "expected_start_date", "notes"]},
                )
                answer_text = _build_quote_recap_follow_up([])
                _append_assistant_message(call_connection_id, answer_text)
                logger.info("Answer text from recap clarification flow: %s", answer_text)
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
            return answer_text, False, False

        if quote_state and not quote_state.get("is_complete"):
            logger.info("➡️  BRANCH: Quote state exists but no tool call was planned; ask for remaining missing info")
            _clear_awaiting_quote_confirmation(call_connection_id)
            answer_text = _generate_quote_collection_response(quote_state.get("missing_fields", []), quote_state)
            _append_assistant_message(call_connection_id, answer_text)
            logger.info("Answer text from GPT pending-quote flow: %s", answer_text)
            return answer_text, False, False

        else:
            logger.info("➡️  SUB-BRANCH: Regular Q&A (no quote_state or quote_state is complete)")
            # 普通问答
            system_prompt = (
                "You are a helpful support assistant speaking on a phone call. "
                "Answer briefly and clearly in natural English. "
                "Keep each answer under 3 sentences. "
                "If the user asks about quotes, pricing, or estimates, help them request a quote."
            )

            logger.info("🤖 Using GPT model: %s (endpoint: %s)", openai_deployment, openai_endpoint)
            logger.info("Calling Azure OpenAI to generate phone answer using deployment: %s", openai_deployment)
            context_messages = [
                {"role": "system", "content": system_prompt},
                *[
                    {
                        "role": "assistant" if m.get("role") == "assistant" else "user",
                        "content": m.get("content", ""),
                    }
                    for m in conversation_history[-6:]
                    if isinstance(m, dict) and m.get("content")
                ],
                {"role": "user", "content": user_text},
            ]
            # 普通问答：流式输出，边生成边加入 TTS 队列播报，降低首字延迟
            logger.info("[STREAM] Starting GPT stream for call=%s (regular Q&A)", call_connection_id)
            stream = client.chat.completions.create(
                model=openai_deployment,
                messages=context_messages,
                temperature=0.4,
                max_tokens=128,
                stream=True,
            )
            full_parts: list[str] = []
            buffer = ""
            chunk_count = 0
            for chunk in stream:
                delta = (chunk.choices[0].delta.content or "") if chunk.choices else ""
                if not delta:
                    continue
                chunk_count += 1
                if chunk_count <= 3 or chunk_count % 20 == 0:
                    logger.info("[STREAM] GPT delta #%d: %s", chunk_count, repr(delta[:40]) + ("..." if len(delta) > 40 else ""))
                full_parts.append(delta)
                buffer += delta
                buffer = _flush_stream_buffer(buffer, call_connection_id)
            full_text = "".join(full_parts).strip()
            total_chunks = len(_active_acs_calls.get(call_connection_id, {}).get("answer_chunk_queue", []))
            logger.info("[STREAM] GPT stream finished: total_deltas=%d, full_len=%d, queue_chunks_so_far=%d", chunk_count, len(full_text), total_chunks)
            # 剩余 buffer 作为最后一段播报
            if buffer.strip():
                _ensure_answer_stream_state(call_connection_id)
                _active_acs_calls[call_connection_id]["answer_chunk_queue"].append(buffer.strip())
                logger.info("[STREAM] Last buffer queued: %s", buffer.strip()[:60] + ("..." if len(buffer.strip()) > 60 else ""))
                asyncio.create_task(_play_next_answer_chunk(call_connection_id))
            if not full_text:
                logger.warning("GPT stream returned empty answer text, using fallback.")
                return fallback, False, False
            # 更新对话历史（流式已播报，不需要再 play_answer_message）
            if call_connection_id and call_connection_id in _active_acs_calls:
                conv = _active_acs_calls[call_connection_id].get("conversation_history", [])
                conv.append({"role": "assistant", "content": full_text})
                if len(conv) > 10:
                    conv = conv[-10:]
                _active_acs_calls[call_connection_id]["conversation_history"] = conv
                _active_acs_calls[call_connection_id]["last_answer"] = full_text
            logger.info("[STREAM] Returning streamed answer (already_played=True), full_text=%s", full_text[:80] + ("..." if len(full_text) > 80 else ""))
            return full_text, quote_updated, True

        logger.info("Answer text from GPT: %s", answer_text)
        return answer_text, quote_updated, False
    except Exception as e:
        logger.error("Failed to generate answer text via Azure OpenAI: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())
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
    tools = [_quote_extraction_tool_schema, _update_quote_info_tool_schema, _send_quote_email_tool_schema]
    recent_history = [
        {
            "role": ("assistant" if msg.get("role") == "assistant" else "user"),
            "content": msg.get("content", ""),
        }
        for msg in (conversation_history or [])[-8:]
        if isinstance(msg, dict) and msg.get("content")
    ]
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


async def _extract_quote_info_phone(conversation_history: list, current_state: dict) -> dict:
    """
    从对话历史中提取报价信息（电话端版本）
    
    复用 quote_tools 的逻辑，但适配电话端的对话格式
    """
    try:
        logger.info("🔍 EXTRACTING QUOTE INFO FROM CONVERSATION")
        logger.info("  Conversation history length: %d messages", len(conversation_history))
        logger.info("  Current state: %s", json.dumps(current_state, ensure_ascii=False, default=str)[:200])
        
        # 构建对话文本
        conversation_text = "\n".join([
            f"{msg.get('role', 'user').upper()}: {msg.get('content', '')}"
            for msg in conversation_history[-10:]
        ])
        logger.info("  Conversation text length: %d characters", len(conversation_text))
        
        # 获取可用产品
        products = fetch_available_products()
        product_names = [p["name"] for p in products]
        if products:
            logger.info("  Found %d available products", len(products))
            logger.info("  Sample products: %s", ", ".join(product_names[:5]))
        else:
            logger.warning("  No products found in Salesforce")
        
        # 使用 GPT 提取信息
        from azure.core.credentials import AzureKeyCredential
        from azure.identity import DefaultAzureCredential
        from openai import AzureOpenAI
        
        openai_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        openai_deployment = (
            os.environ.get("AZURE_OPENAI_EXTRACTION_DEPLOYMENT")
            or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
            or "gpt-4o-mini"
        )
        llm_key = os.environ.get("AZURE_OPENAI_API_KEY")
        
        if not openai_endpoint or not openai_deployment:
            return {
                "extracted": current_state.get("extracted", {}),
                "missing_fields": ["customer_name", "contact_info", "quote_items"],
                "is_complete": False,
            }
        
        if llm_key:
            credential = AzureKeyCredential(llm_key)
        else:
            credential = DefaultAzureCredential()
        
        product_list_text = ", ".join(product_names) if product_names else "No products available"
        
        # 合并当前已提取的信息
        extracted_data = current_state.get("extracted", {}).copy()
        
        extraction_prompt = f"""Extract quote information from the following conversation. 
Return a JSON object with the following fields:
- customer_name: Customer's name (if mentioned)
- contact_info: Email address or phone number (if mentioned)
- quote_items: Array of items, each with {{"product_package": "product name", "quantity": number}}. 
  Support multiple products - if user mentions multiple products, include all of them.
- expected_start_date: Expected start date in format YYYY-MM-DD (if mentioned)
- notes: Any additional notes or requirements mentioned

Available products: {product_list_text}

Conversation:
{conversation_text}

Current extracted data (update only if new information is found):
{json.dumps(extracted_data, ensure_ascii=False)}

Return ONLY a valid JSON object, no other text. If a field is not found, use null for that field (use [] for quote_items if no products mentioned).
Merge with current extracted data - only update fields where new information is found."""
        
        if isinstance(credential, AzureKeyCredential):
            client = AzureOpenAI(
                api_key=credential.key,
                api_version="2024-02-15-preview",
                azure_endpoint=openai_endpoint
            )
        else:
            token = credential.get_token("https://cognitiveservices.azure.com/.default").token
            client = AzureOpenAI(
                api_key=token,
                api_version="2024-02-15-preview",
                azure_endpoint=openai_endpoint
            )
        
        logger.info("🤖 Calling GPT for quote extraction (deployment: %s)", openai_deployment)
        logger.info("  Prompt length: %d characters", len(extraction_prompt))
        
        response = client.chat.completions.create(
            model=openai_deployment,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that extracts structured information from conversations. Always return valid JSON only."},
                {"role": "user", "content": extraction_prompt}
            ],
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        
        logger.info("✅ GPT extraction response received")
        new_extracted = json.loads(response.choices[0].message.content)
        logger.info("  Extracted data: %s", json.dumps(new_extracted, ensure_ascii=False, default=str)[:300])
        
        logger.info("🔄 Merging extracted data with current state...")
        extracted_data = normalize_and_match_quote_extracted_data(
            extracted_data,
            new_extracted,
            products,
            replace_quote_items=False,
        )
        result = build_quote_state(extracted_data, product_names)
        logger.info(
            "✅ Extraction result: is_complete=%s, missing_fields=%s",
            result.get("is_complete"),
            result.get("missing_fields"),
        )
        logger.info("📋 Final quote state: %s", json.dumps(result, ensure_ascii=False, default=str)[:400])
        return result
        
    except Exception as e:
        logger.error("Error extracting quote info: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())
        return {
            "extracted": current_state.get("extracted", {}),
            "missing_fields": ["customer_name", "contact_info", "quote_items"],
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
    default_text = "Hello, thanks for calling. Please hold for a moment."

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
            "You are a helpful call center assistant. "
            "Generate one short, friendly English greeting sentence for an incoming phone call. "
            "The caller just dialed a support number. "
            "Return ONLY the sentence, without quotes, explanations or extra text."
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
        welcome_text = "Hi, I'm your voice assistant how can I help you today?"
        
        logger.info("🎵 Playing welcome message using TTS...")
        logger.info("   Text: %s", welcome_text)
        logger.info("   Connection ID: %s", call_connection_id)
        
        # 使用 TextSource 直接播放文本（官方推荐方式）
        # 根据 SDK 版本，TextSource 可能在不同的位置
        text_source = None
        
        # 方法 1: 尝试从主模块导入（最常见）
        try:
            from azure.communication.callautomation import TextSource
            text_source = TextSource(
                text=welcome_text,
                voice_name="en-US-JennyNeural",
                source_locale="en-US",
            )
            logger.info("   Using TextSource from main module")
        except ImportError:
            # 方法 2: 尝试从 models 导入（某些 SDK 版本可能在这里）
            try:
                from azure.communication.callautomation.models import (
                    TextSource,  # type: ignore
                )
                text_source = TextSource(
                    text=welcome_text,
                    voice_name="en-US-JennyNeural",
                    source_locale="en-US",
                )
                logger.info("   Using TextSource from models")
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
        
        logger.info("✅ Welcome message playback initiated")
        logger.info("   Voice: en-AU-NatashaNeural (Australian accent)")
        if hasattr(play_result, 'operation_id'):
            logger.info("   Operation ID: %s", play_result.operation_id)
        
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
        logger.info("🎧 Starting speech recognition for call %s, caller_phone=%s", call_connection_id, caller_phone)

        call_connection.start_recognizing_media(
            RecognizeInputType.SPEECH,  # type: ignore[name-defined]
            caller_identifier,
            speech_language="en-US",  # 改为 en-US 匹配你的 TTS 配置
            initial_silence_timeout=10,  # 等对方开口的秒数
            end_silence_timeout=2,  # 停顿多久算一句结束
            operation_context="user-speech",
        )
        logger.info("✅ Speech recognition started (waiting for RecognizeCompleted event)")

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

        logger.info("🎵 Playing answer message using TTS...")
        logger.info("   Text: %s", answer_text)
        logger.info("   Connection ID: %s", call_connection_id)

        text_source = None
        try:
            from azure.communication.callautomation import TextSource
            text_source = TextSource(
                text=answer_text,
                voice_name="en-US-JennyNeural",
                source_locale="en-US",
            )
            logger.info("   Using TextSource from main module for answer")
        except ImportError:
            try:
                from azure.communication.callautomation.models import (
                    TextSource,  # type: ignore
                )
                text_source = TextSource(
                    text=answer_text,
                    voice_name="en-US-JennyNeural",
                    source_locale="en-US",
                )
                logger.info("   Using TextSource from models for answer")
            except ImportError:
                logger.error("❌ TextSource not found in SDK (answer)")
                logger.error("   Please ensure azure-communication-callautomation is installed")
                return

        play_result = call_connection.play_media(
            text_source,
            operation_context="answer-tts",
        )

        logger.info("✅ Answer message playback initiated")
        if hasattr(play_result, "operation_id"):
            logger.info("   Answer Operation ID: %s", play_result.operation_id)

        if call_connection_id in _active_acs_calls:
            _active_acs_calls[call_connection_id]["last_answer"] = answer_text

    except Exception as e:
        logger.error("❌ Error in play_answer_message: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())


def _ensure_answer_stream_state(call_connection_id: str) -> None:
    """确保通话有流式回答队列和播放状态（用于 GPT 流式 + 分块播报）。"""
    if call_connection_id not in _active_acs_calls:
        return
    if "answer_chunk_queue" not in _active_acs_calls[call_connection_id]:
        _active_acs_calls[call_connection_id]["answer_chunk_queue"] = []
    if "answer_stream_playing" not in _active_acs_calls[call_connection_id]:
        _active_acs_calls[call_connection_id]["answer_stream_playing"] = False


async def _play_next_answer_chunk(call_connection_id: str) -> bool:
    """
    从 answer_chunk_queue 取出一段并播放（用于流式回答边收边播）。
    若队列为空或正在播放则直接返回。
    Returns True 表示已开始播放一段，False 表示未播放（队列空或正在播）。
    """
    acs_client = get_acs_client()
    if not acs_client:
        return False
    if call_connection_id not in _active_acs_calls:
        return False
    call_info = _active_acs_calls[call_connection_id]
    queue = call_info.get("answer_chunk_queue", [])
    if call_info.get("answer_stream_playing"):
        logger.debug("[STREAM] _play_next_answer_chunk skipped: already playing")
        return False
    if not queue:
        logger.debug("[STREAM] _play_next_answer_chunk skipped: queue empty")
        return False
    chunk = queue.pop(0).strip()
    if not chunk:
        return await _play_next_answer_chunk(call_connection_id)
    try:
        call_connection = acs_client.get_call_connection(call_connection_id)
        try:
            from azure.communication.callautomation import TextSource
            text_source = TextSource(
                text=chunk,
                voice_name="en-US-JennyNeural",
                source_locale="en-US",
            )
        except ImportError:
            from azure.communication.callautomation.models import TextSource  # type: ignore
            text_source = TextSource(
                text=chunk,
                voice_name="en-US-JennyNeural",
                source_locale="en-US",
            )
        call_connection.play_media(
            text_source,
            operation_context="answer-tts-stream",
        )
        _active_acs_calls[call_connection_id]["answer_stream_playing"] = True
        queue_left = len(_active_acs_calls[call_connection_id].get("answer_chunk_queue", []))
        logger.info("[STREAM] Playing chunk (queue left=%d): %s", queue_left, chunk[:50] + ("..." if len(chunk) > 50 else ""))
        return True
    except Exception as e:
        logger.error("❌ Error playing stream chunk: %s", str(e))
        _active_acs_calls[call_connection_id]["answer_stream_playing"] = False
        return False


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
            from azure.communication.callautomation import TextSource
            text_source = TextSource(
                text=error_text,
                voice_name="en-US-JennyNeural",
                source_locale="en-US",
            )
        except ImportError:
            try:
                from azure.communication.callautomation.models import (
                    TextSource,  # type: ignore
                )
                text_source = TextSource(
                    text=error_text,
                    voice_name="en-US-JennyNeural",
                    source_locale="en-US",
                )
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
            logger.info("📞 Received ACS Event Array with %d event(s)", len(events))
        else:
            events = [raw_data]
        
        for event_data in events:
            # 记录收到的事件
            # Event Grid 使用 eventType，ACS Call Automation 使用 type 或 kind
            event_type = event_data.get("eventType") or event_data.get("type") or event_data.get("kind") or "Unknown"
            logger.info("=" * 60)
            logger.info("📞 Received ACS Event: %s", event_type)
            logger.info("Event data: %s", json.dumps(event_data, indent=2, ensure_ascii=False))
            logger.info("=" * 60)
            
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
