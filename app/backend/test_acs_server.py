"""
独立的 ACS 来电处理测试服务器（本地测试版本）

这个文件可以独立运行，用于在本地测试 ACS Call Automation 的来电处理功能。

🚀 快速开始（本地测试）：

1. 配置环境变量（在 .env 文件中）：
   - ACS_CONNECTION_STRING=endpoint=https://...;accesskey=...

2. 启动服务器（第一个终端）：
   python test_acs_server.py

3. 启动 ngrok 隧道（第二个终端）：
   ngrok http 8766
   
   复制 ngrok 生成的 HTTPS URL（例如：https://abc123.ngrok-free.app）

4. 更新环境变量并重启服务器：
   ACS_CALLBACK_URL=https://abc123.ngrok-free.app/api/acs/calls/events
   python test_acs_server.py

5. 在 Azure Portal 中配置电话号码的来电路由指向你的回调 URL

6. 拨打你的电话号码进行测试！

详细说明请参考：docs/acs_local_testing.md
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional

import aiohttp
from aiohttp import web
from dotenv import load_dotenv

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("acs_test")

# 存储活跃通话
active_calls: Dict[str, Dict[str, Any]] = {}

# ACS 客户端（延迟导入，如果 SDK 不可用也不会立即报错）
acs_client = None


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
    # 优先使用专门的对话部署，其次是通用部署；如果你希望强制用 realtime 部署名，也可以改成优先 REALTIME
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


def init_acs_client():
    """初始化 ACS 客户端"""
    global acs_client
    
    try:
        from azure.communication.callautomation import CallAutomationClient
        
        connection_string = os.environ.get("ACS_CONNECTION_STRING")
        if not connection_string:
            logger.warning("ACS_CONNECTION_STRING not configured")
            return None
        
        # 验证连接字符串格式
        if "endpoint=" not in connection_string or "accesskey=" not in connection_string:
            logger.error("❌ ACS_CONNECTION_STRING format is incorrect")
            logger.error("   Expected format: endpoint=https://xxx.communication.azure.com/;accesskey=xxx")
            logger.error("   Your value: %s...", connection_string[:50])
            return None
        
        # 尝试解析 access key 以验证格式
        try:
            import base64
            # 提取 access key
            parts = connection_string.split(";")
            access_key = None
            for part in parts:
                if part.startswith("accesskey="):
                    access_key = part.split("=", 1)[1]
                    break
            
            if access_key:
                # 尝试 base64 解码以验证格式
                # Base64 字符串长度应该是 4 的倍数，如果不是，可能需要补齐 padding
                padding_needed = len(access_key) % 4
                if padding_needed:
                    access_key_test = access_key + "=" * (4 - padding_needed)
                else:
                    access_key_test = access_key
                
                try:
                    base64.b64decode(access_key_test, validate=True)
                except Exception as decode_error:
                    logger.error("❌ ACS_CONNECTION_STRING access key format is invalid")
                    logger.error("   Error: %s", str(decode_error))
                    logger.error("   Please check your access key in Azure Portal")
                    logger.error("   Make sure you copied the complete access key")
                    return None
        except Exception as validation_error:
            logger.warning("Could not validate access key format: %s", str(validation_error))
            # 继续尝试初始化，让 SDK 自己报错
        
        acs_client = CallAutomationClient.from_connection_string(connection_string)
        logger.info("✓ ACS Call Automation client initialized successfully")
        return acs_client
    except ImportError as e:
        logger.error("Failed to import ACS SDK. Please install: pip install azure-communication-callautomation")
        logger.error("Error: %s", str(e))
        return None
    except Exception as e:
        error_msg = str(e)
        logger.error("Failed to initialize ACS client: %s", error_msg)
        
        # 提供更详细的错误信息
        if "Incorrect padding" in error_msg or "base64" in error_msg.lower():
            logger.error("")
            logger.error("=" * 60)
            logger.error("❌ ACS_CONNECTION_STRING 配置错误")
            logger.error("=" * 60)
            logger.error("问题：access key 格式不正确（base64 解码失败）")
            logger.error("")
            logger.error("解决方案：")
            logger.error("1. 前往 Azure Portal → Communication Services → Keys")
            logger.error("2. 点击 'Show' 显示完整的 access key")
            logger.error("3. 完整复制 access key（不要遗漏任何字符）")
            logger.error("4. 确保连接字符串格式为：")
            logger.error("   endpoint=https://xxx.communication.azure.com/;accesskey=完整的key")
            logger.error("")
            logger.error("当前连接字符串（隐藏敏感信息）：")
            if connection_string:
                # 只显示 endpoint 部分
                endpoint_part = connection_string.split(";")[0] if ";" in connection_string else connection_string[:50]
                logger.error("   %s...", endpoint_part)
            logger.error("=" * 60)
        
        return None


async def handle_acs_webhook(request: web.Request) -> web.Response:
    """
    处理 ACS Call Automation 的 webhook 事件
    
    这是主要的 webhook 端点，ACS 会将所有事件发送到这里
    """
    try:
        # 解析事件数据
        raw_data = await request.json()
        
        # ACS 可能发送单个事件对象或事件数组
        # 如果是列表，取第一个元素；如果是字典，直接使用
        if isinstance(raw_data, list):
            if len(raw_data) > 0:
                event_data = raw_data[0]  # 取第一个事件
                logger.info("📞 Received ACS Event Array with %d event(s)", len(raw_data))
            else:
                logger.warning("Received empty event array")
                return web.json_response({"status": "received", "message": "Empty event array"}, status=200)
        else:
            event_data = raw_data
        
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
                return web.json_response(response_data, status=200)
            else:
                logger.warning("⚠️  Validation event received but no validationCode found")
                logger.warning("   Event data structure: %s", json.dumps(event_data, indent=2))
                return web.json_response({"status": "received"}, status=200)
        
        # 处理来电事件
        elif event_type == "Microsoft.Communication.IncomingCall":
            result = await handle_incoming_call(event_data)
            return web.json_response(result, status=200)
        
        # 处理通话连接事件
        elif event_type == "Microsoft.Communication.CallConnected":
            # callConnectionId 在 data 字段中
            event_data_obj = event_data.get("data", {})
            call_connection_id = event_data_obj.get("callConnectionId")
            logger.info("✅ Call Connected - Connection ID: %s", call_connection_id)
            if call_connection_id and call_connection_id in active_calls:
                active_calls[call_connection_id]["status"] = "connected"
                logger.info("   Updated call status to 'connected'")
                
                # 播放欢迎语音
                await play_welcome_message(call_connection_id)
            else:
                logger.warning("   Call connection ID not found in active calls")
            return web.json_response({"status": "received"}, status=200)
        
        # 处理通话断开事件
        elif event_type == "Microsoft.Communication.CallDisconnected":
            # callConnectionId 在 data 字段中
            event_data_obj = event_data.get("data", {})
            call_connection_id = event_data_obj.get("callConnectionId")
            result_info = event_data_obj.get("resultInformation", {})
            disconnect_reason = result_info.get("message", "Unknown reason")
            logger.info("❌ Call Disconnected - Connection ID: %s", call_connection_id)
            logger.info("   Reason: %s", disconnect_reason)
            if call_connection_id and call_connection_id in active_calls:
                call_info = active_calls.pop(call_connection_id)
                logger.info("   Removed call from active calls: %s", call_connection_id)
            else:
                logger.warning("   Call connection ID not found in active calls")
            return web.json_response({"status": "received"}, status=200)
        
        # 处理参与者更新事件
        elif event_type == "Microsoft.Communication.ParticipantsUpdated":
            event_data_obj = event_data.get("data", {})
            call_connection_id = event_data_obj.get("callConnectionId")
            participants = event_data_obj.get("participants", [])
            logger.info("👥 Participants Updated - Connection ID: %s", call_connection_id)
            logger.info("   Participants count: %d", len(participants))
            for i, participant in enumerate(participants):
                identifier = participant.get("identifier", {})
                raw_id = identifier.get("rawId", "unknown")
                is_muted = participant.get("isMuted", False)
                is_on_hold = participant.get("isOnHold", False)
                logger.info("   Participant %d: %s (muted: %s, on hold: %s)", 
                           i + 1, raw_id, is_muted, is_on_hold)
            return web.json_response({"status": "received"}, status=200)
        
        # 处理播放完成事件
        elif event_type == "Microsoft.Communication.PlayCompleted":
            event_data_obj = event_data.get("data", {})
            call_connection_id = event_data_obj.get("callConnectionId")
            logger.info("🎵 Play Completed - Connection ID: %s", call_connection_id)
            if call_connection_id and call_connection_id in active_calls:
                active_calls[call_connection_id]["welcome_played"] = True
            return web.json_response({"status": "received"}, status=200)
        
        # 处理播放失败事件
        elif event_type == "Microsoft.Communication.PlayFailed":
            event_data_obj = event_data.get("data", {})
            call_connection_id = event_data_obj.get("callConnectionId")
            result_info = event_data_obj.get("resultInformation", {})
            error_message = result_info.get("message", "Unknown error")
            logger.warning("⚠️  Play Failed - Connection ID: %s, Error: %s", call_connection_id, error_message)
            return web.json_response({"status": "received"}, status=200)
        
        # 其他事件类型
        else:
            logger.info("ℹ️  Unhandled event type: %s", event_type)
            return web.json_response({"status": "received"}, status=200)
        
    except json.JSONDecodeError as e:
        logger.error("❌ Failed to parse JSON: %s", str(e))
        return web.json_response({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.error("❌ Error processing webhook: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())
        return web.json_response({"error": str(e)}, status=500)


async def handle_incoming_call(event_data: Dict[str, Any]) -> Dict[str, Any]:
    """处理来电事件 - 自动接听电话"""
    global acs_client
    
    if not acs_client:
        logger.error("❌ ACS client not available")
        return {"error": "ACS client not configured"}
    
    try:
        # 解析来电信息
        # incomingCallContext 是一个 JWT token 字符串，不是对象
        incoming_call_context = event_data.get("data", {}).get("incomingCallContext", "")
        if not incoming_call_context:
            incoming_call_context = event_data.get("incomingCallContext", "")
        
        # 从事件数据中提取来电信息
        event_data_obj = event_data.get("data", {})
        from_info = event_data_obj.get("from", {})
        caller_id = from_info.get("rawId", from_info.get("phoneNumber", {}).get("value", "unknown"))
        to_info = event_data_obj.get("to", {})
        recipient_id = to_info.get("rawId", to_info.get("phoneNumber", {}).get("value", "unknown"))
        
        logger.info("📞 Incoming Call:")
        logger.info("   Caller: %s", caller_id)
        logger.info("   Recipient: %s", recipient_id)
        logger.info("   Incoming Call Context: %s...", incoming_call_context[:50] if incoming_call_context else "None")
        
        if not incoming_call_context:
            logger.error("❌ No incomingCallContext found in event data")
            return {"error": "No incomingCallContext in event"}
        
        # 获取回调 URL
        callback_url = os.environ.get("ACS_CALLBACK_URL")
        if not callback_url:
            logger.error("❌ ACS_CALLBACK_URL not configured")
            return {"error": "Callback URL not configured"}
        
        # 确保回调 URL 以 /events 结尾
        if not callback_url.endswith("/events"):
            callback_url = f"{callback_url.rstrip('/')}/events"
        
        logger.info("   Callback URL: %s", callback_url)
        
        # 接听电话
        # ACS SDK 的 answer_call 方法直接接受 incoming_call_context (JWT token) 和 callback_url
        logger.info("📞 Answering call...")
        answer_result = acs_client.answer_call(
            incoming_call_context=incoming_call_context,
            callback_url=callback_url
        )
        
        if answer_result and hasattr(answer_result, 'call_connection_id'):
            call_connection_id = answer_result.call_connection_id
            
            # 记录活跃通话
            active_calls[call_connection_id] = {
                "call_connection_id": call_connection_id,
                "caller_id": caller_id,
                "recipient_id": recipient_id,
                "status": "answered",
                "started_at": str(asyncio.get_event_loop().time())
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
    global acs_client
    
    if not acs_client:
        logger.error("❌ ACS client not available, cannot play welcome message")
        return
    
    try:
        # 从 CallAutomationClient 获取 CallConnectionClient
        call_connection = acs_client.get_call_connection(call_connection_id)
        
        # 欢迎语音文本：优先从 GPT-4o 生成，如果失败再回退到默认文案
        # 使用澳洲口音播放，匹配澳洲电话号码
        welcome_text = await generate_welcome_text_with_gpt()
        
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
                voice_name="en-AU-NatashaNeural"
            )
            logger.info("   Using TextSource from main module")
        except ImportError:
            # 方法 2: 尝试从 models 导入
            try:
                from azure.communication.callautomation.models import TextSource
                text_source = TextSource(
                    text=welcome_text,
                    voice_name="en-AU-NatashaNeural"
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
        if call_connection_id in active_calls:
            active_calls[call_connection_id]["welcome_playing"] = True
            active_calls[call_connection_id]["welcome_text"] = welcome_text
            
    except ImportError as import_error:
        logger.error("❌ Failed to import TextSource: %s", str(import_error))
        logger.error("   Please ensure azure-communication-callautomation is installed")
        logger.error("   Run: pip install azure-communication-callautomation")
    except Exception as e:
        logger.error("❌ Error in play_welcome_message: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())


async def handle_get_active_calls(request: web.Request) -> web.Response:
    """获取当前活跃的通话列表"""
    return web.json_response({
        "active_calls": list(active_calls.values()),
        "count": len(active_calls)
    })


async def handle_health(request: web.Request) -> web.Response:
    """健康检查端点"""
    return web.json_response({
        "status": "healthy",
        "acs_configured": acs_client is not None,
        "active_calls": len(active_calls)
    })


async def handle_root(request: web.Request) -> web.Response:
    """处理根路径请求（用于调试）"""
    try:
        if request.method == "POST":
            # 记录 POST 请求的详细信息
            body = await request.read()
            logger.warning("=" * 60)
            logger.warning("⚠️  Received POST request to root path (/)")
            logger.warning("This should be sent to /api/acs/calls/events")
            logger.warning("Request headers: %s", dict(request.headers))
            try:
                body_json = await request.json()
                logger.warning("Request body: %s", json.dumps(body_json, indent=2, ensure_ascii=False))
            except:
                logger.warning("Request body (raw): %s", body.decode('utf-8', errors='ignore')[:500])
            logger.warning("=" * 60)
            
            # 尝试处理（可能是 ACS 事件）
            try:
                raw_data = await request.json()
                if isinstance(raw_data, list) and len(raw_data) > 0:
                    raw_data = raw_data[0]
                elif isinstance(raw_data, list):
                    return web.json_response({"status": "received", "message": "Empty event array"}, status=200)
                
                # 如果是事件数据，转发到正确的处理器
                event_type = raw_data.get("type") or raw_data.get("kind") or "Unknown"
                if "Communication" in event_type or "Call" in event_type:
                    logger.info("Detected ACS event, processing...")
                    return await handle_acs_webhook(request)
            except:
                pass
            
            return web.json_response({
                "error": "Please use /api/acs/calls/events endpoint",
                "message": "ACS events should be sent to /api/acs/calls/events"
            }, status=400)
        else:
            return web.json_response({
                "status": "ACS Test Server",
                "endpoints": {
                    "webhook": "/api/acs/calls/events",
                    "health": "/health",
                    "active_calls": "/api/acs/calls"
                }
            })
    except Exception as e:
        logger.error("Error handling root request: %s", str(e))
        return web.json_response({"error": str(e)}, status=500)


def create_app() -> web.Application:
    """创建 aiohttp 应用"""
    app = web.Application()
    
    # 注册路由
    app.router.add_post("/", handle_root)  # 根路径处理（用于调试）
    app.router.add_post("/api/acs/calls/events", handle_acs_webhook)
    app.router.add_get("/api/acs/calls", handle_get_active_calls)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/", handle_root)  # GET 请求也处理
    
    return app


def main():
    """主函数"""
    # 加载环境变量
    load_dotenv()
    
    # 检查必要的环境变量
    required_vars = ["ACS_CONNECTION_STRING", "ACS_CALLBACK_URL"]
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    
    if missing_vars:
        logger.error("❌ Missing required environment variables:")
        for var in missing_vars:
            logger.error("   - %s", var)
        logger.error("\n请在 .env 文件中配置这些变量")
        return
    
    # 验证回调 URL 不是示例值
    callback_url = os.environ.get("ACS_CALLBACK_URL", "")
    if "your-ngrok-url.com" in callback_url or "xxx" in callback_url:
        logger.error("")
        logger.error("=" * 60)
        logger.error("⚠️  ACS_CALLBACK_URL 还是示例值！")
        logger.error("=" * 60)
        logger.error("请更新为你的实际 ngrok URL：")
        logger.error("1. 启动 ngrok: ngrok http 8766")
        logger.error("2. 复制 HTTPS URL（例如：https://abc123.ngrok-free.app）")
        logger.error("3. 更新 .env 文件中的 ACS_CALLBACK_URL：")
        logger.error("   ACS_CALLBACK_URL=https://abc123.ngrok-free.app/api/acs/calls/events")
        logger.error("4. 重启服务器")
        logger.error("=" * 60)
        logger.error("")
    
    # 初始化 ACS 客户端
    init_acs_client()
    
    if not acs_client:
        logger.error("❌ Failed to initialize ACS client. Please check your configuration.")
        return
    
    # 创建应用
    app = create_app()
    
    # 启动服务器
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8755))
    
    logger.info("=" * 60)
    logger.info("🚀 Starting ACS Test Server")
    logger.info("=" * 60)
    logger.info("Server URL: http://%s:%s", host, port)
    logger.info("Webhook endpoint: http://%s:%s/api/acs/calls/events", host, port)
    logger.info("Health check: http://%s:%s/health", host, port)
    logger.info("Active calls: http://%s:%s/api/acs/calls", host, port)
    logger.info("=" * 60)
    logger.info("📞 Ready to receive calls!")
    logger.info("=" * 60)
    
    web.run_app(app, host=host, port=port)


if __name__ == "__main__":
    main()

