"""
Azure Communication Services (ACS) Call Automation Handler
用于测试和处理来自 ACS 的电话来电

这个模块实现了：
1. 接收 ACS Call Automation 的 webhook 事件
2. 自动接听来电
3. 播放欢迎语音
4. 记录通话状态

环境变量配置：
- ACS_CONNECTION_STRING: Azure Communication Services 连接字符串
- ACS_CALLBACK_URL: 你的公网可访问的回调 URL (例如: https://yourapp.com/api/acs/calls/events)
- ACS_PHONE_NUMBER: 你的 ACS 电话号码 (例如: +1234567890)
"""

import json
import logging
import os
from typing import Any, Dict, Optional
from uuid import uuid4
import time
import aiohttp
from aiohttp import web
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv

# 先获取 logger，供后续导入失败时使用
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voicerag")

# 延迟导入 ACS SDK，避免导入失败导致模块无法加载
try:
    from azure.communication.callautomation import CallAutomationClient
    # 语音智能 / 识别相关类型（不同 SDK 版本可能略有差异，统一做兼容处理）
    try:
        from azure.communication.callautomation import (  # type: ignore
            CallMediaRecognizeSpeechOptions,
            RecognizeInputType,
        )
    except ImportError:
        CallMediaRecognizeSpeechOptions = None  # type: ignore[assignment]
        RecognizeInputType = None  # type: ignore[assignment]
        logger.info("CallMediaRecognizeSpeechOptions / RecognizeInputType not available; speech Q&A may be limited.")
    try:
        # 新版 SDK：使用 AnswerCallOptions + CallIntelligenceOptions，可以在接听时配置认知服务
        from azure.communication.callautomation import AnswerCallOptions, CallIntelligenceOptions  # type: ignore
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
_active_acs_calls: Dict[str, Dict[str, Any]] = {}

# ACS 客户端（全局单例）
_acs_client: Optional[CallAutomationClient] = None


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


async def handle_incoming_call_event(event_data: Dict[str, Any]) -> Dict[str, Any]:
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
        caller_id = from_info.get("rawId", from_info.get("phoneNumber", {}).get("value", "unknown"))
        to_info = data.get("to", {})
        recipient_id = to_info.get("rawId", to_info.get("phoneNumber", {}).get("value", "unknown"))
        
        logger.info("📞 Incoming Call:")
        logger.info("   Caller: %s", caller_id)
        logger.info("   Recipient: %s", recipient_id)
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
                    answer_result = acs_client.answer_call(
                        incoming_call_context=incoming_call_context,
                        callback_url=callback_url,
                        cognitive_services_endpoint=cog_endpoint,  # type: ignore[call-arg]
                    )
                except TypeError:
                    logger.warning("answer_call() does not accept cognitive_services_endpoint; falling back to basic answer_call.")
                    answer_result = acs_client.answer_call(
                        incoming_call_context=incoming_call_context,
                        callback_url=callback_url,
                    )
            else:
                # 未配置认知服务终结点，使用最基础的 answer_call（仍可接通，但可能无法使用某些智能特性）
                logger.warning("ACS_COGNITIVE_SERVICE_ENDPOINT not set; answering call without cognitive configuration.")
                answer_result = acs_client.answer_call(
                    incoming_call_context=incoming_call_context,
                    callback_url=callback_url,
                )
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
            
            # 记录活跃通话（保存完整的 caller 信息，用于后续语音识别）
            _active_acs_calls[call_connection_id] = {
                "call_connection_id": call_connection_id,
                "caller_id": caller_id,
                "caller_info": from_info,  # 保存完整的 from_info，用于构造 CommunicationIdentifier
                "recipient_id": recipient_id,
                "status": "answered",
                "started_at": time.time()
            }
            
            logger.info("✅ Call answered successfully!")
            logger.info("   Connection ID: %s", call_connection_id)
            
            return {
                "success": True,
                "call_connection_id": call_connection_id,
                "caller_id": caller_id,
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


async def handle_call_connected_event(event_data: Dict[str, Any]) -> None:
    """处理通话已连接事件"""
    try:
        # callConnectionId 在 data 字段中
        event_data_obj = event_data.get("data", {})
        call_connection_id = event_data_obj.get("callConnectionId")
        
        logger.info("✅ Call Connected - Connection ID: %s", call_connection_id)
        
        if call_connection_id and call_connection_id in _active_acs_calls:
            _active_acs_calls[call_connection_id]["status"] = "connected"
            logger.info("   Updated call status to 'connected'")
            
            # 播放欢迎语音（固定文案 / 之后可换成 GPT 文本）
            # 注意：识别会在欢迎语播放完成后自动启动（在 handle_play_completed_event 中处理）
            await play_welcome_message(call_connection_id)
        else:
            logger.warning("   Call connection ID not found in active calls")
        
    except Exception as e:
        logger.error("Error handling call connected event: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())


async def handle_call_disconnected_event(event_data: Dict[str, Any]) -> None:
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
            call_info = _active_acs_calls.pop(call_connection_id)
            logger.info("   Removed call from active calls: %s", call_connection_id)
        else:
            logger.warning("   Call connection ID not found in active calls")
        
    except Exception as e:
        logger.error("Error handling call disconnected event: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())


async def handle_play_completed_event(event_data: Dict[str, Any]) -> None:
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
                logger.info("Answer playback completed, restarting speech recognition for next question...")
                await start_speech_recognition(call_connection_id)
            else:
                # 其他播放完成事件（可能是错误提示等），不重新启动识别
                logger.info("Play completed for context: %s (not restarting recognition)", operation_context)
        
    except Exception as e:
        logger.error("Error handling play completed event: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())


async def handle_play_failed_event(event_data: Dict[str, Any]) -> None:
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


async def handle_recognize_completed_event(event_data: Dict[str, Any]) -> None:
    """
    处理语音识别完成事件：
    1. 从事件里拿到用户说的话（转成的文本）
    2. 调 GPT 生成回答
    3. 用 ACS TTS 播放回答
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
                for key in ("transcript", "text", "recognizedSpeech", "speechText", "displayText"):
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

        if not user_text:
            logger.warning("RecognizeCompleted received but no transcript text found.")
            return

        logger.info("User said (transcript): %s", user_text)

        # 调用 GPT 生成电话回答
        answer_text = await generate_answer_text_with_gpt(user_text)

        # 播放回答
        if call_connection_id:
            await play_answer_message(call_connection_id, answer_text)
        else:
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


async def handle_recognize_failed_event(event_data: Dict[str, Any]) -> None:
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


async def generate_answer_text_with_gpt(user_text: str) -> str:
    """
    使用 Azure OpenAI 根据用户语音转成的文本生成回答（电话版 Q&A 核心逻辑）。
    
    和网页版一样，本质是：用户一句话 -> GPT 生成一句 / 一小段回答文本。
    """
    # 如果 GPT 不可用，就回个固定文案，避免电话静音
    fallback = "I am sorry, I could not process your question. Please try again later."

    try:
        from openai import AzureOpenAI
        from azure.core.credentials import AzureKeyCredential
        from azure.identity import DefaultAzureCredential
    except Exception as e:
        logger.warning("Azure OpenAI SDK not available, using fallback answer. Error: %s", str(e))
        return fallback

    openai_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    openai_deployment = (
        os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        or os.environ.get("AZURE_OPENAI_EXTRACTION_DEPLOYMENT")
        or "gpt-4o"
    )
    llm_key = os.environ.get("AZURE_OPENAI_API_KEY")

    if not openai_endpoint or not openai_deployment:
        logger.warning("Azure OpenAI endpoint/deployment not configured. Using fallback answer.")
        return fallback

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

        system_prompt = (
            "You are a helpful support assistant speaking on a phone call. "
            "Answer briefly and clearly in natural English. "
            "Keep each answer under 3 sentences."
        )

        logger.info("Calling Azure OpenAI to generate phone answer using deployment: %s", openai_deployment)
        response = client.chat.completions.create(
            model=openai_deployment,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            temperature=0.4,
            max_tokens=128,
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            logger.warning("GPT returned empty answer text, using fallback.")
            return fallback

        logger.info("Answer text from GPT: %s", text)
        return text
    except Exception as e:
        logger.error("Failed to generate answer text via Azure OpenAI: %s", str(e))
        return fallback


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
        from openai import AzureOpenAI
        from azure.core.credentials import AzureKeyCredential
        from azure.identity import DefaultAzureCredential
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
        welcome_text = "I love you Karina, and I will love you forever and ever."
        
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
                from azure.communication.callautomation.models import TextSource  # type: ignore
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
    
    使用正确的 API：call_connection.start_recognizing_media() 直接传参。
    """
    acs_client = get_acs_client()
    if not acs_client:
        logger.error("❌ ACS client not available, cannot start speech recognition")
        return

    try:
        call_connection = acs_client.get_call_connection(call_connection_id)

        call_info = _active_acs_calls.get(call_connection_id, {})
        caller_id_str = call_info.get("caller_id")
        caller_info = call_info.get("caller_info", {})
        
        # 需要将 caller_id 字符串转换为 CommunicationIdentifier 对象
        # 尝试导入并构造
        caller = None
        try:
            from azure.communication.callautomation import CommunicationIdentifier, PhoneNumberIdentifier
            
            # 优先从 caller_info 构造（如果 SDK 支持）
            if caller_info and isinstance(caller_info, dict):
                # 尝试从 phoneNumber 构造
                phone_number = caller_info.get("phoneNumber", {}).get("value")
                if phone_number:
                    try:
                        caller = PhoneNumberIdentifier(phone_number)  # type: ignore[call-arg]
                        logger.info("   Constructed PhoneNumberIdentifier from phoneNumber: %s", phone_number)
                    except (TypeError, AttributeError):
                        pass
                
                # 如果 PhoneNumberIdentifier 失败，尝试从 rawId 构造
                if caller is None:
                    raw_id = caller_info.get("rawId") or caller_id_str
                    if raw_id:
                        try:
                            # 某些 SDK 版本可能支持 from_raw_id
                            if hasattr(CommunicationIdentifier, "from_raw_id"):
                                caller = CommunicationIdentifier.from_raw_id(raw_id)  # type: ignore[attr-defined]
                            else:
                                # 如果 SDK 不支持 from_raw_id，尝试直接传字符串
                                caller = raw_id
                        except (AttributeError, TypeError):
                            caller = raw_id
            
            # 如果上面都失败了，直接使用 caller_id_str
            if caller is None:
                caller = caller_id_str
                logger.warning("   Using caller_id string directly: %s", caller_id_str)
                
        except ImportError as import_err:
            # 如果无法导入 CommunicationIdentifier，尝试直接传字符串
            logger.warning("CommunicationIdentifier not available (%s), using caller_id string directly", str(import_err))
            caller = caller_id_str

        logger.info("🎧 Starting speech recognition for call: %s, target: %s", call_connection_id, caller)

        # 1️⃣ 优先使用 CallMediaRecognizeSpeechOptions（如果在当前 SDK 中可用）
        if "CallMediaRecognizeSpeechOptions" in globals() and CallMediaRecognizeSpeechOptions is not None:  # type: ignore[name-defined]
            try:
                kwargs: Dict[str, Any] = {
                    "target_participant": caller,
                }
                if "RecognizeInputType" in globals() and RecognizeInputType is not None:  # type: ignore[name-defined]
                    kwargs["input_type"] = RecognizeInputType.SPEECH  # type: ignore[assignment]
                # 识别语言
                kwargs["speech_language"] = "en-US"

                options = CallMediaRecognizeSpeechOptions(**kwargs)  # type: ignore[call-arg]
                logger.info("Using CallMediaRecognizeSpeechOptions to start recognition.")

                try:
                    call_connection.start_recognizing_media(options)  # type: ignore[arg-type,attr-defined]
                    logger.info("✅ Speech recognition started (with options, waiting for RecognizeCompleted event)")
                    return
                except Exception as start_err:
                    logger.error("Failed to start recognizing with options: %s", str(start_err))
                    import traceback
                    logger.error("Traceback: %s", traceback.format_exc())
                    # 退回到 kwargs 方式
            except TypeError as opt_err:
                logger.error("Failed to construct CallMediaRecognizeSpeechOptions, error=%s", str(opt_err))
                logger.error("Falling back to kwargs signature for start_recognizing_media().")

        # 2️⃣ 回退：直接使用 kwargs 调用 start_recognizing_media
        try:
            kwargs2: Dict[str, Any] = {
                "target_participant": caller,
                "speech_language": "en-US",
                "operation_context": "user-speech",
            }
            if "RecognizeInputType" in globals() and RecognizeInputType is not None:  # type: ignore[name-defined]
                kwargs2["input_type"] = RecognizeInputType.SPEECH  # type: ignore[assignment]

            call_connection.start_recognizing_media(**kwargs2)  # type: ignore[attr-defined]
            logger.info("✅ Speech recognition started (kwargs, waiting for RecognizeCompleted event)")
        except TypeError as type_err:
            logger.error("TypeError in start_recognizing_media, error=%s", str(type_err))
            logger.error("This might be due to parameter name mismatch. Please check SDK docs.")
            import traceback
            logger.error("Traceback: %s", traceback.format_exc())
            await speak_error_message(call_connection_id, debug_tag="start-recognize-call")
            return
        except Exception as start_err:
            logger.error("Failed to start recognizing: %s", str(start_err))
            import traceback
            logger.error("Traceback: %s", traceback.format_exc())
            await speak_error_message(call_connection_id, debug_tag="start-recognize-call")
            return

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
                from azure.communication.callautomation.models import TextSource  # type: ignore
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
                from azure.communication.callautomation.models import TextSource  # type: ignore
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
            
            # 处理语音识别完成事件（电话 Q&A 的入口）
            elif event_type == "Microsoft.Communication.RecognizeCompleted":
                await handle_recognize_completed_event(event_data)

            # 处理语音识别失败事件
            elif event_type == "Microsoft.Communication.RecognizeFailed":
                await handle_recognize_failed_event(event_data)
            
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




