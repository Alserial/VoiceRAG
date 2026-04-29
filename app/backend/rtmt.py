import asyncio
import json
import logging
import os
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

import aiohttp
from aiohttp import web
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

logger = logging.getLogger("voicerag")

_ACS_EXPECTED_AUDIO_ENCODING = "pcm"
_ACS_EXPECTED_AUDIO_SAMPLE_RATE = 24000
_ACS_EXPECTED_AUDIO_CHANNELS = 1

class ToolResultDirection(Enum):
    TO_SERVER = 1
    TO_CLIENT = 2

class ToolResult:
    text: str
    destination: ToolResultDirection

    def __init__(self, text: str, destination: ToolResultDirection):
        self.text = text
        self.destination = destination

    def to_text(self) -> str:
        if self.text is None:
            return ""
        return self.text if type(self.text) == str else json.dumps(self.text)

class Tool:
    target: Callable[..., ToolResult]
    schema: Any

    def __init__(self, target: Any, schema: Any):
        self.target = target
        self.schema = schema

class RTToolCall:
    tool_call_id: str
    previous_id: str

    def __init__(self, tool_call_id: str, previous_id: str):
        self.tool_call_id = tool_call_id
        self.previous_id = previous_id

class RTMiddleTier:
    endpoint: str
    deployment: str
    key: Optional[str] = None
    
    # Tools are server-side only for now, though the case could be made for client-side tools
    # in addition to server-side tools that are invisible to the client
    tools: dict[str, Tool] = {}

    # Server-enforced configuration, if set, these will override the client's configuration
    # Typically at least the model name and system message will be set by the server
    model: Optional[str] = None
    system_message: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    disable_audio: Optional[bool] = None
    voice_choice: Optional[str] = None
    _tools_pending = {}
    _token_provider = None
    _conversation_logs = {}  # Store conversation logs per session
    _quote_triggered = {}    # Track quote trigger per session to avoid duplicates
    _user_registered = {}    # Track user registration status per session
    _quote_states = {}       # Track structured quote state per session
    _user_states = {}        # Track structured user registration state per session
    intent_classifier: Optional[Callable[["RTMiddleTier", Optional[str], str], dict[str, Any]]] = None
    acs_phone_turn_handler: Optional[Callable[[str, Optional[str], Optional[str], aiohttp.ClientWebSocketResponse], Awaitable[None]]] = None

    def __init__(
        self,
        endpoint: str,
        deployment: str,
        credentials: AzureKeyCredential | DefaultAzureCredential,
        voice_choice: Optional[str] = None,
    ):
        self.endpoint = endpoint
        self.deployment = deployment
        self.voice_choice = voice_choice
        if voice_choice is not None:
            logger.info("Realtime voice choice set to %s", voice_choice)
        if isinstance(credentials, AzureKeyCredential):
            self.key = credentials.key
        else:
            self._token_provider = get_bearer_token_provider(credentials, "https://cognitiveservices.azure.com/.default")
            self._token_provider() # Warm up during startup so we have a token cached when the first request arrives

    @staticmethod
    def _get_client_source(ws: web.WebSocketResponse) -> str:
        return getattr(ws, "client_source", "web")

    def _client_can_receive_internal_events(self, ws: web.WebSocketResponse) -> bool:
        return self._get_client_source(ws) == "web"

    def _apply_session_defaults(self, session: dict[str, Any]) -> dict[str, Any]:
        session["type"] = "realtime"
        if self.system_message is not None:
            session["instructions"] = self.system_message
        if self.temperature is not None:
            session["temperature"] = self.temperature
        if self.max_tokens is not None:
            session["max_response_output_tokens"] = self.max_tokens
        if self.voice_choice is not None:
            session.setdefault("audio", {}).setdefault("output", {})["voice"] = self.voice_choice
        # GA Realtime does not support turn_detection or disable_audio — strip them if present
        session.pop("turn_detection", None)
        session.pop("disable_audio", None)
        session["tool_choice"] = "auto" if len(self.tools) > 0 else "none"
        session["tools"] = [tool.schema for tool in self.tools.values()]
        return session

    def _build_acs_session_update_event(self) -> dict[str, Any]:
        session: dict[str, Any] = {
            "type": "realtime",
            "instructions": (
                "You are the audio transport layer for an ACS phone call. "
                "Do not decide the business flow yourself. "
                "Wait for server-issued response.create instructions."
            ),
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                },
            },
            "tool_choice": "none",
            "tools": [],
        }
        if self.voice_choice is not None:
            session["audio"]["output"]["voice"] = self.voice_choice
        return {
            "type": "session.update",
            "session": session,
        }

    @staticmethod
    def _is_acs_request(request: web.Request) -> bool:
        header_markers = (
            "x-ms-call-connection-id",
            "x-ms-call-correlation-id",
            "x-ms-call-media-streaming-operation-context",
        )
        if any(request.headers.get(header) for header in header_markers):
            return True
        return request.query.get("source", "").strip().lower() == "acs"

    @staticmethod
    def _extract_acs_value(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
        for key in keys:
            if key in payload:
                return payload[key]
        return default

    async def _open_realtime_target_ws(self, ws: web.WebSocketResponse) -> Optional[aiohttp.ClientWebSocketResponse]:
        params = {"model": self.deployment}
        headers: dict[str, str]
        if self.key is not None:
            headers = {"api-key": self.key}
        else:
            headers = {"Authorization": f"Bearer {self._token_provider()}"}
        request_headers = getattr(ws, "request_headers", {})
        if "x-ms-client-request-id" in request_headers:
            headers["x-ms-client-request-id"] = request_headers["x-ms-client-request-id"]

        logger.info("Realtime upstream: endpoint=%s path=/openai/v1/realtime params=%s", self.endpoint, params)
        session = aiohttp.ClientSession(base_url=self.endpoint)
        max_attempts = 4
        try:
            for attempt in range(1, max_attempts + 1):
                try:
                    target_ws = await session.ws_connect("/openai/v1/realtime", headers=headers, params=params)
                    target_ws._owning_session = session  # type: ignore[attr-defined]
                    return target_ws
                except aiohttp.WSServerHandshakeError as exc:
                    if exc.status == 429 and attempt < max_attempts:
                        delay_seconds = min(8, 2 ** (attempt - 1))
                        logger.warning(
                            "Azure OpenAI realtime handshake hit 429 (attempt %d/%d). Retrying in %ss.",
                            attempt,
                            max_attempts,
                            delay_seconds,
                        )
                        await asyncio.sleep(delay_seconds)
                        continue
                    logger.error(
                        "Failed to connect to Azure OpenAI realtime websocket: status=%s, message=%s, source=%s",
                        exc.status,
                        str(exc),
                        self._get_client_source(ws),
                    )
                    if self._client_can_receive_internal_events(ws):
                        await ws.send_json({
                            "type": "extension.middle_tier_error",
                            "error": "realtime_handshake_failed",
                            "status": exc.status,
                        })
                    await ws.close(code=1013, message=b"Upstream realtime unavailable")
                    await session.close()
                    return None
            await ws.close(code=1013, message=b"Upstream realtime unavailable")
            await session.close()
            return None
        except Exception:
            await session.close()
            raise

    @staticmethod
    async def _close_realtime_target_ws(target_ws: aiohttp.ClientWebSocketResponse) -> None:
        owning_session = getattr(target_ws, "_owning_session", None)
        try:
            if not target_ws.closed:
                await target_ws.close()
        finally:
            if owning_session is not None and not owning_session.closed:
                await owning_session.close()

    async def _process_message_to_client(self, msg: str, client_ws: web.WebSocketResponse, server_ws: web.WebSocketResponse) -> Optional[str]:
        message = json.loads(msg.data)
        updated_message = msg.data
        client_source = self._get_client_source(client_ws)
        session_id = getattr(client_ws, "session_id", None)
        if message is not None:
            msg_type = message.get("type", "")
            logger.info("Azure->client event: type=%s source=%s", msg_type, client_source)
            if "input_audio" in msg_type or "transcription" in msg_type:
                logger.debug("Received message type: %s, content: %s", msg_type, json.dumps(message)[:200])
            match message.get("type"):
                case None:
                    logger.warning("Realtime server message missing 'type': keys=%s", list(message.keys()))
                    return updated_message
                case "session.created":
                    session = message["session"]
                    # Hide the instructions, tools and max tokens from clients, if we ever allow client-side 
                    # tools, this will need updating
                    session["instructions"] = ""
                    session["tools"] = []
                    session["voice"] = self.voice_choice
                    session["tool_choice"] = "none"
                    session["max_response_output_tokens"] = None
                    # Initialize conversation log for this session
                    session_id = session.get("id", str(uuid4()))
                    if not hasattr(client_ws, "session_id"):
                        client_ws.session_id = session_id
                    else:
                        session_id = client_ws.session_id
                    self._conversation_logs[session_id] = {
                        "session_id": session_id,
                        "start_time": datetime.now().isoformat(),
                        "messages": []
                    }
                    # Initialize user registration status
                    self._user_registered[session_id] = False
                    if client_source == "acs":
                        logger.info(
                            "ACS realtime session.created: session_id=%s upstream_session_id=%s voice=%s",
                            session_id,
                            session.get("id"),
                            session.get("voice"),
                        )
                    updated_message = json.dumps(message)

                case "response.output_item.added":
                    if "item" in message and message["item"].get("type") == "function_call":
                        updated_message = None

                case "conversation.item.created":
                    if "item" in message and message["item"].get("type") == "function_call":
                        item = message["item"]
                        if item["call_id"] not in self._tools_pending:
                            self._tools_pending[item["call_id"]] = RTToolCall(item["call_id"], message["previous_item_id"])
                        updated_message = None
                    elif "item" in message and message["item"].get("type") == "function_call_output":
                        updated_message = None
                    elif "item" in message and message["item"].get("type") == "input_audio_transcription":
                        # Record user input transcription (when created as item)
                        session_id = getattr(client_ws, "session_id", None)
                        if session_id and session_id in self._conversation_logs:
                            transcript = message["item"].get("transcript", "")
                            if transcript:
                                logger.debug("Captured user input transcription item: %s", transcript[:50])
                                self._conversation_logs[session_id]["messages"].append({
                                    "role": "user",
                                    "content": transcript,
                                    "timestamp": datetime.now().isoformat()
                                })
                                await self._handle_web_intent_turn(session_id, transcript, client_ws, server_ws)
                
                case "conversation.item.input_audio_transcription.completed":
                    # Record user input transcription
                    session_id = getattr(client_ws, "session_id", None)
                    if session_id and session_id in self._conversation_logs:
                        # Try different possible locations for transcript
                        transcript = message.get("transcript", "")
                        if not transcript and "item" in message:
                            item = message["item"]
                            transcript = item.get("transcript", "") or item.get("text", "")
                        if not transcript and "transcription" in message:
                            transcript = message["transcription"].get("transcript", "")
                        # Also check if transcript is in a nested structure
                        if not transcript:
                            # Try to find transcript anywhere in the message
                            def find_transcript(obj, depth=0):
                                if depth > 3:  # Limit recursion
                                    return ""
                                if isinstance(obj, dict):
                                    if "transcript" in obj:
                                        return obj["transcript"]
                                    if "text" in obj:
                                        return obj["text"]
                                    for v in obj.values():
                                        result = find_transcript(v, depth + 1)
                                        if result:
                                            return result
                                elif isinstance(obj, list):
                                    for item in obj:
                                        result = find_transcript(item, depth + 1)
                                        if result:
                                            return result
                                return ""
                            
                            transcript = find_transcript(message)
                        
                        if transcript:
                            logger.info(
                                "TEST_FLOW User transcript received: source=%s session_id=%s call_connection_id=%s transcript=%s",
                                client_source,
                                session_id,
                                getattr(client_ws, "call_connection_id", None),
                                transcript[:200],
                            )
                            self._conversation_logs[session_id]["messages"].append({
                                "role": "user",
                                "content": transcript,
                                "timestamp": datetime.now().isoformat()
                            })
                            if client_source == "acs" and self.acs_phone_turn_handler is not None:
                                call_connection_id = getattr(client_ws, "call_connection_id", None)
                                logger.info(
                                    "Dispatching ACS phone turn to ACS business logic: session_id=%s call_connection_id=%s transcript=%s",
                                    session_id,
                                    call_connection_id,
                                    transcript[:160],
                                )
                                await self.acs_phone_turn_handler(transcript, session_id, call_connection_id, server_ws)
                            else:
                                await self._handle_web_intent_turn(session_id, transcript, client_ws, server_ws)
                        else:
                            logger.warning("User input transcription message received but no transcript found. Message keys: %s", list(message.keys()))
                            logger.debug("Full message: %s", json.dumps(message)[:500])

                case "response.function_call_arguments.delta":
                    updated_message = None
                
                case "response.function_call_arguments.done":
                    updated_message = None

                case "response.output_item.done":
                    if "item" in message and message["item"].get("type") == "function_call":
                        item = message["item"]
                        tool_name = item["name"]
                        logger.info("Tool called: %s, call_id: %s, args: %s", tool_name, item.get("call_id"), item.get("arguments", "")[:200])
                        tool_call = self._tools_pending[message["item"]["call_id"]]
                        tool = self.tools[tool_name]
                        args = item["arguments"]
                        # Pass session_id to tools that need it
                        session_id = getattr(client_ws, "session_id", None)
                        if session_id:
                            self._current_session_id = session_id
                        # Check if tool target accepts session_id parameter
                        import inspect
                        try:
                            sig = inspect.signature(tool.target)
                            if "session_id" in sig.parameters:
                                result = await tool.target(json.loads(args), session_id=session_id)
                            else:
                                result = await tool.target(json.loads(args))
                        except (TypeError, ValueError) as e:
                            # If signature inspection fails, try without session_id
                            logger.debug("Tool signature inspection failed: %s, trying without session_id", str(e))
                            result = await tool.target(json.loads(args))
                        await server_ws.send_json({
                            "type": "conversation.item.create",
                            "item": {
                                "type": "function_call_output",
                                "call_id": item["call_id"],
                                # Always return output to the LLM so it can drive the next turn
                                "output": result.to_text()
                            }
                        })
                        if result.destination == ToolResultDirection.TO_CLIENT and self._client_can_receive_internal_events(client_ws):
                            # Send tool result to client for display
                            logger.info("Sending tool result to client: tool_name=%s", item["name"])
                            await client_ws.send_json({
                                "type": "extension.middle_tier_tool_response",
                                "previous_item_id": tool_call.previous_id,
                                "tool_name": item["name"],
                                "tool_result": result.to_text()
                            })
                        elif result.destination == ToolResultDirection.TO_CLIENT:
                            logger.info(
                                "Tool result kept server-side for non-web client: source=%s tool_name=%s",
                                self._get_client_source(client_ws),
                                item["name"],
                            )
                        updated_message = None

                case "response.done":
                    if len(self._tools_pending) > 0:
                        self._tools_pending.clear() # Any chance tool calls could be interleaved across different outstanding responses?
                        followup_instructions = getattr(client_ws, "intent_followup_instructions", None)
                        followup_response: dict[str, Any] = {"modalities": ["audio", "text"]}
                        if followup_instructions:
                            followup_response["instructions"] = followup_instructions
                        await server_ws.send_json({
                            "type": "response.create",
                            "response": followup_response,
                        })
                    if "response" in message:
                        replace = False
                        for i, output in enumerate(reversed(message["response"]["output"])):
                            if output.get("type") == "function_call":
                                message["response"]["output"].pop(i)
                                replace = True
                        if replace:
                            updated_message = json.dumps(message)
                    if client_source == "acs":
                        response = message.get("response", {}) or {}
                        outputs = response.get("output") or []
                        output_types = [
                            output.get("type")
                            for output in outputs
                            if isinstance(output, dict)
                        ]
                        assistant_reply_text = ""
                        for output in outputs:
                            if not isinstance(output, dict):
                                continue
                            if output.get("type") == "text" and output.get("text"):
                                assistant_reply_text += str(output.get("text") or "")
                            elif output.get("type") == "audio" and output.get("transcript"):
                                assistant_reply_text += str(output.get("transcript") or "")
                        if not assistant_reply_text:
                            assistant_reply_text = str(getattr(client_ws, "acs_audio_transcript_text", "") or "")
                        logger.info(
                            "TEST_FLOW AI reply completed: session_id=%s status=%s output_types=%s audio_chunks=%s text=%s",
                            session_id,
                            response.get("status"),
                            output_types,
                            getattr(client_ws, "acs_audio_delta_count", 0),
                            assistant_reply_text[:500],
                        )
                        client_ws.acs_audio_delta_count = 0
                        client_ws.acs_audio_transcript_preview = ""
                        client_ws.acs_audio_transcript_text = ""
                    # Record assistant response
                    if session_id and session_id in self._conversation_logs:
                        response_text = ""
                        if "response" in message and "output" in message["response"]:
                            for output in message["response"]["output"]:
                                if output.get("type") == "text" and "text" in output:
                                    response_text += output["text"]
                                elif output.get("type") == "audio" and "transcript" in output:
                                    response_text += output.get("transcript", "")
                        if response_text:
                            self._conversation_logs[session_id]["messages"].append({
                                "role": "assistant",
                                "content": response_text,
                                "timestamp": datetime.now().isoformat()
                            })
                
                case "response.output_audio_transcript.delta":
                    # Record assistant transcript delta
                    if session_id and session_id in self._conversation_logs:
                        transcript_delta = message.get("delta", "")
                        if transcript_delta:
                            # Append to last assistant message or create new one
                            messages = self._conversation_logs[session_id]["messages"]
                            if messages and messages[-1].get("role") == "assistant":
                                messages[-1]["content"] += transcript_delta
                            else:
                                messages.append({
                                    "role": "assistant",
                                    "content": transcript_delta,
                                    "timestamp": datetime.now().isoformat()
                                })
                    if client_source == "acs":
                        transcript_delta = message.get("delta", "")
                        if transcript_delta:
                            preview = f"{getattr(client_ws, 'acs_audio_transcript_preview', '')}{transcript_delta}"
                            full_text = f"{getattr(client_ws, 'acs_audio_transcript_text', '')}{transcript_delta}"
                            client_ws.acs_audio_transcript_preview = preview[:200]
                            client_ws.acs_audio_transcript_text = full_text[:2000]

                case "response.output_audio.delta":
                    if client_source == "acs":
                        chunk_count = int(getattr(client_ws, "acs_audio_delta_count", 0) or 0) + 1
                        client_ws.acs_audio_delta_count = chunk_count

                case "error":
                    if client_source == "acs":
                        logger.error(
                            "ACS realtime upstream error event: session_id=%s payload=%s",
                            session_id,
                            json.dumps(message),
                        )

        return updated_message

    async def _process_message_to_server(self, msg: str, ws: web.WebSocketResponse) -> Optional[str]:
        message = json.loads(msg.data)
        updated_message = msg.data
        if message is not None:
            msg_type = message.get("type", "")
            # Debug logging for user input messages
            if "input_audio" in msg_type or "transcription" in msg_type:
                logger.debug("Processing message type: %s, full message: %s", msg_type, json.dumps(message)[:300])
            
            match message.get("type"):
                case None:
                    logger.warning("Realtime client message missing 'type': keys=%s", list(message.keys()))
                    return updated_message
                case "session.update":
                    session = self._apply_session_defaults(message["session"])
                    updated_message = json.dumps(message)

        return updated_message

    async def _forward_messages(self, ws: web.WebSocketResponse):
        session_id = getattr(ws, "session_id", str(uuid4()))
        ws.session_id = session_id
        target_ws = await self._open_realtime_target_ws(ws)
        if target_ws is None:
            return

        try:
            async def from_client_to_server():
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        new_msg = await self._process_message_to_server(msg, ws)
                        if new_msg is not None:
                            await target_ws.send_str(new_msg)
                    else:
                        logger.warning("Unexpected client websocket message type: %s", msg.type)

                logger.info("Closing Azure OpenAI realtime websocket for source=%s", self._get_client_source(ws))
                await target_ws.close()

            async def from_server_to_client():
                async for msg in target_ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        new_msg = await self._process_message_to_client(msg, ws, target_ws)
                        if new_msg is not None:
                            await ws.send_str(new_msg)
                    else:
                        logger.warning("Unexpected realtime server websocket message type: %s", msg.type)

            try:
                await asyncio.gather(from_client_to_server(), from_server_to_client())
            except ConnectionResetError:
                pass
            finally:
                await self._save_and_send_conversation(session_id)
        finally:
            await self._close_realtime_target_ws(target_ws)

    async def _send_acs_session_update(self, target_ws: aiohttp.ClientWebSocketResponse, ws: web.WebSocketResponse) -> None:
        if getattr(ws, "acs_session_initialized", False):
            return

        session_update = self._build_acs_session_update_event()
        await target_ws.send_json(session_update)
        ws.acs_session_initialized = True
        audio = session_update["session"].get("audio", {})
        logger.info(
            "ACS->Realtime translated event: session.update session_id=%s audio_input=%s audio_output=%s voice=%s",
            getattr(ws, "session_id", "unknown"),
            audio.get("input", {}).get("format"),
            audio.get("output", {}).get("format"),
            audio.get("output", {}).get("voice"),
        )

    async def _translate_acs_message_to_realtime(
        self,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
        target_ws: aiohttp.ClientWebSocketResponse,
    ) -> None:
        kind = self._extract_acs_value(payload, "kind", "Kind", default="Unknown")
        if kind != "AudioData":
            logger.info(
                "ACS websocket control message received: session_id=%s kind=%s keys=%s",
                getattr(ws, "session_id", "unknown"),
                kind,
                list(payload.keys()),
            )

        if kind == "AudioMetadata":
            metadata = self._extract_acs_value(payload, "audioMetadata", "AudioMetadata", default={}) or {}
            encoding = str(self._extract_acs_value(metadata, "encoding", "Encoding", default="")).lower()
            sample_rate = self._extract_acs_value(metadata, "sampleRate", "SampleRate", default=None)
            channels = self._extract_acs_value(metadata, "channels", "Channels", default=None)
            ws.acs_audio_metadata = {
                "encoding": encoding,
                "sample_rate": sample_rate,
                "channels": channels,
            }
            logger.info(
                "ACS audio metadata: session_id=%s encoding=%s sample_rate=%s channels=%s expected_encoding=%s expected_sample_rate=%s expected_channels=%s",
                getattr(ws, "session_id", "unknown"),
                encoding or "unknown",
                sample_rate,
                channels,
                _ACS_EXPECTED_AUDIO_ENCODING,
                _ACS_EXPECTED_AUDIO_SAMPLE_RATE,
                _ACS_EXPECTED_AUDIO_CHANNELS,
            )
            if encoding and encoding != _ACS_EXPECTED_AUDIO_ENCODING:
                logger.warning("ACS audio encoding mismatch: got=%s expected=%s", encoding, _ACS_EXPECTED_AUDIO_ENCODING)
            if sample_rate not in (None, _ACS_EXPECTED_AUDIO_SAMPLE_RATE):
                logger.warning("ACS audio sample rate mismatch: got=%s expected=%s", sample_rate, _ACS_EXPECTED_AUDIO_SAMPLE_RATE)
            if channels not in (None, _ACS_EXPECTED_AUDIO_CHANNELS):
                logger.warning("ACS audio channel mismatch: got=%s expected=%s", channels, _ACS_EXPECTED_AUDIO_CHANNELS)
            await self._send_acs_session_update(target_ws, ws)
            return

        if kind == "AudioData":
            await self._send_acs_session_update(target_ws, ws)
            audio_wrapper = self._extract_acs_value(payload, "audioData", "AudioData", default={}) or {}
            audio_b64 = self._extract_acs_value(audio_wrapper, "data", "Data", default="")
            silent = self._extract_acs_value(audio_wrapper, "silent", "Silent", default=None)
            participant = self._extract_acs_value(audio_wrapper, "participantRawID", "ParticipantRawID", default=None)
            if not audio_b64:
                logger.warning("ACS audio translation failed: missing audio payload session_id=%s", getattr(ws, "session_id", "unknown"))
                return
            ws.acs_buffer_has_audio = True
            translated_event = {
                "type": "input_audio_buffer.append",
                "audio": audio_b64,
            }
            await target_ws.send_json(translated_event)
            logger.debug(
                "ACS->Realtime translated event: type=%s session_id=%s bytes_b64=%d silent=%s participant=%s",
                translated_event["type"],
                getattr(ws, "session_id", "unknown"),
                len(audio_b64),
                silent,
                participant,
            )
            return

        logger.info("ACS websocket message ignored: session_id=%s kind=%s", getattr(ws, "session_id", "unknown"), kind)

    @staticmethod
    def _build_acs_audio_message(audio_b64: str) -> dict[str, Any]:
        return {
            "Kind": "AudioData",
            "AudioData": {
                "Data": audio_b64,
            },
            "StopAudio": None,
        }

    @staticmethod
    def _build_acs_stop_audio_message() -> dict[str, Any]:
        return {
            "Kind": "StopAudio",
            "AudioData": None,
            "StopAudio": {},
        }

    async def _translate_realtime_message_to_acs(
        self,
        msg: aiohttp.WSMessage,
        ws: web.WebSocketResponse,
        target_ws: aiohttp.ClientWebSocketResponse,
    ) -> None:
        await self._process_message_to_client(msg, ws, target_ws)
        message = json.loads(msg.data)
        msg_type = message.get("type")

        if msg_type == "response.output_audio.delta":
            audio_b64 = message.get("delta")
            if not audio_b64:
                logger.warning("Realtime->ACS translation failed: empty response.output_audio.delta session_id=%s", getattr(ws, "session_id", "unknown"))
                return
            await ws.send_json(self._build_acs_audio_message(audio_b64))
            return

        if msg_type in {"input_audio_buffer.speech_started", "response.cancelled"}:
            await ws.send_json(self._build_acs_stop_audio_message())
            logger.info(
                "Realtime->ACS translated event: source_type=%s target_kind=StopAudio session_id=%s",
                msg_type,
                getattr(ws, "session_id", "unknown"),
            )
            return

        if msg_type in {"input_audio_buffer.committed", "input_audio_buffer.speech_stopped"}:
            ws.acs_buffer_has_audio = False
            logger.info(
                "Realtime buffer event: type=%s session_id=%s",
                msg_type,
                getattr(ws, "session_id", "unknown"),
            )
            return

        if msg_type == "error":
            logger.error("Realtime upstream error for ACS session %s: %s", getattr(ws, "session_id", "unknown"), json.dumps(message))

    async def _forward_acs_messages(self, ws: web.WebSocketResponse) -> None:
        session_id = getattr(ws, "session_id", str(uuid4()))
        ws.session_id = session_id
        ws.client_source = "acs"
        ws.acs_session_initialized = False
        ws.acs_buffer_has_audio = False
        ws.acs_audio_delta_count = 0
        ws.acs_audio_transcript_preview = ""
        ws.acs_audio_transcript_text = ""

        target_ws = await self._open_realtime_target_ws(ws)
        if target_ws is None:
            return

        try:
            async def from_acs_to_realtime():
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        logger.warning("Unexpected ACS websocket message type: %s", msg.type)
                        continue
                    try:
                        payload = json.loads(msg.data)
                    except json.JSONDecodeError:
                        logger.warning("ACS websocket JSON parse failed: session_id=%s payload_preview=%s", session_id, msg.data[:200])
                        continue
                    try:
                        await self._translate_acs_message_to_realtime(payload, ws, target_ws)
                    except Exception as exc:
                        logger.exception("ACS->Realtime translation failed: session_id=%s error=%s", session_id, str(exc))

                if getattr(ws, "acs_buffer_has_audio", False):
                    try:
                        await target_ws.send_json({"type": "input_audio_buffer.commit"})
                        logger.info("ACS->Realtime translated event: input_audio_buffer.commit session_id=%s reason=socket_closed", session_id)
                    except Exception as exc:
                        logger.warning("Failed to flush ACS audio buffer on close: session_id=%s error=%s", session_id, str(exc))
                await target_ws.close()

            async def from_realtime_to_acs():
                async for msg in target_ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        logger.warning("Unexpected realtime websocket message type for ACS bridge: %s", msg.type)
                        continue
                    try:
                        await self._translate_realtime_message_to_acs(msg, ws, target_ws)
                    except Exception as exc:
                        logger.exception("Realtime->ACS translation failed: session_id=%s error=%s", session_id, str(exc))

            try:
                await asyncio.gather(from_acs_to_realtime(), from_realtime_to_acs())
            except ConnectionResetError:
                pass
            finally:
                await self._save_and_send_conversation(session_id)
        finally:
            await self._close_realtime_target_ws(target_ws)

    async def _websocket_handler(self, request: web.Request):
        if self._is_acs_request(request):
            logger.info(
                "Detected ACS websocket request on %s; routing through ACS adapter. session=%s call_connection_id=%s",
                request.path,
                request.query.get("session"),
                request.headers.get("x-ms-call-connection-id"),
            )
            return await self._acs_websocket_handler(request)

        ws = web.WebSocketResponse()
        await ws.prepare(request)
        ws.client_source = "web"
        ws.request_headers = request.headers
        logger.info("Web realtime websocket connected: path=%s session=%s", request.path, request.query.get("session"))
        # Allow callers (for example ACS media bridge) to pin a stable session id.
        requested_session_id = request.query.get("session")
        if requested_session_id:
            ws.session_id = requested_session_id
        await self._forward_messages(ws)
        return ws

    async def _acs_websocket_handler(self, request: web.Request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        ws.client_source = "acs"
        ws.request_headers = request.headers
        requested_session_id = request.query.get("session") or request.headers.get("x-ms-call-connection-id")
        if requested_session_id:
            ws.session_id = requested_session_id
        ws.call_connection_id = request.headers.get("x-ms-call-connection-id")
        logger.info(
            "ACS media websocket connected: path=%s session=%s call_connection_id=%s correlation_id=%s operation_context=%s",
            request.path,
            getattr(ws, "session_id", None),
            request.headers.get("x-ms-call-connection-id"),
            request.headers.get("x-ms-call-correlation-id"),
            request.headers.get("x-ms-call-media-streaming-operation-context"),
        )
        await self._forward_acs_messages(ws)
        return ws

    async def _invoke_tool(self, tool_name: str, args: Any, session_id: str, client_ws: web.WebSocketResponse, server_ws: web.WebSocketResponse):
        """Invoke a tool manually and forward the result to client or server."""
        try:
            if tool_name not in self.tools:
                logger.warning("Tool %s not found", tool_name)
                return

            tool = self.tools[tool_name]
            # Pass session_id if the tool accepts it
            import inspect
            result = None
            try:
                sig = inspect.signature(tool.target)
                if "session_id" in sig.parameters:
                    result = await tool.target(args, session_id=session_id)
                else:
                    result = await tool.target(args)
            except Exception as e:
                logger.error("Error invoking tool %s: %s", tool_name, str(e))
                return

            if result is None:
                return

            if result.destination == ToolResultDirection.TO_SERVER:
                await server_ws.send_json({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": f"manual_{tool_name}",
                        "output": result.to_text()
                    }
                })
            elif result.destination == ToolResultDirection.TO_CLIENT:
                result_text = result.to_text()
                # Also forward to LLM so it has the same state (keeps loop consistent)
                await server_ws.send_json({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": f"manual_{tool_name}",
                        "output": result_text
                    }
                })
                if self._client_can_receive_internal_events(client_ws):
                    logger.info("Sending tool result to client: tool_name=%s, result_length=%d, preview: %s", 
                               tool_name, len(result_text), result_text[:200])
                    await client_ws.send_json({
                        "type": "extension.middle_tier_tool_response",
                        "previous_item_id": None,
                        "tool_name": tool_name,
                        "tool_result": result_text
                    })
                    logger.info("Tool result sent to client successfully for tool: %s", tool_name)
                else:
                    logger.info(
                        "Skipped extension.middle_tier_tool_response for non-web client: source=%s tool_name=%s",
                        self._get_client_source(client_ws),
                        tool_name,
                    )
        except Exception as e:
            logger.error("Error forwarding tool result: %s", str(e))

    async def _handle_web_intent_turn(
        self,
        session_id: str,
        transcript: str,
        client_ws: web.WebSocketResponse,
        server_ws: aiohttp.ClientWebSocketResponse,
    ) -> None:
        """Classify a web utterance first, then ask realtime to run the selected handler."""
        if self._get_client_source(client_ws) != "web":
            return

        if self.intent_classifier is None:
            logger.warning("No intent classifier configured; falling back to realtime model response.")
            await server_ws.send_json({"type": "response.create"})
            return

        try:
            intent_result = self.intent_classifier(self, session_id, transcript)
        except Exception as exc:
            logger.exception("Intent classification failed: session_id=%s error=%s", session_id, str(exc))
            intent_result = {
                "intent": "unsupported_or_other",
                "action": "unknown",
                "confidence": 0.0,
                "fields": {},
                "reason": "classification_failed",
                "session_state": {},
            }

        logger.info("Intent classified: session_id=%s result=%s", session_id, json.dumps(intent_result, default=str))

        if self._client_can_receive_internal_events(client_ws):
            await client_ws.send_json({
                "type": "extension.intent_classification",
                "intent": intent_result,
                "transcript": transcript,
            })

        instructions = self._build_intent_response_instructions(intent_result)
        client_ws.intent_followup_instructions = instructions
        await server_ws.send_json({
            "type": "response.create",
            "response": {
                "modalities": ["audio", "text"],
                "instructions": instructions,
            },
        })

    def _build_intent_response_instructions(self, intent_result: dict[str, Any]) -> str:
        intent = intent_result.get("intent")
        action = intent_result.get("action")

        common = (
            "You are handling a routed web voice turn. "
            "Follow the route below exactly. Keep the spoken reply in English and concise. "
            "Never mention internal intent names, JSON, tool names, file names, or keys to the user. "
            "If a function output is already available for this routed turn, use it to speak next and do not repeat the same function call."
        )

        if intent == "registration":
            if action == "confirm":
                return (
                    f"{common}\n"
                    "Route: registration.confirm. Acknowledge that the user confirmed their registration details. "
                    "Do not call tools. The client confirmation flow will save the user details."
                )
            if action == "cancel":
                return (
                    f"{common}\n"
                    "Route: registration.cancel. Acknowledge the cancellation and ask for the correct name and email if they still want to continue. "
                    "Do not call tools."
                )
            if action == "status":
                return (
                    f"{common}\n"
                    "Route: registration.status. Call extract_user_info to recover the current name and email from conversation history, then answer with those values if present."
                )
            return (
                f"{common}\n"
                "Route: registration. Registration is required before any other web task. "
                "Call extract_user_info after the user's latest message. "
                "If the result is incomplete, ask only for the missing name or email. "
                "If complete, restate the name and email and ask the user to review the dialog on screen or say confirm/yes."
            )

        if intent == "quote":
            if action == "confirm":
                return (
                    f"{common}\n"
                    "Route: quote.confirm. Acknowledge that the user confirmed the quote details. "
                    "Do not call send_quote_email for the first send; the client confirmation flow handles creating and sending the quote."
                )
            if action == "cancel":
                return (
                    f"{common}\n"
                    "Route: quote.cancel. Acknowledge that the current quote was cancelled. Do not call tools."
                )
            if action == "resend":
                return (
                    f"{common}\n"
                    "Route: quote.resend. If the current quote state is complete or already sent, call send_quote_email. "
                    "If quote details are incomplete, call extract_quote_info and ask only for missing fields."
                )
            if action == "update_info":
                return (
                    f"{common}\n"
                    "Route: quote.update_info. The user is changing an existing quote detail. "
                    "Call update_quote_info with only the fields the user changed. "
                    "Then use the returned state to confirm changed values or ask only for remaining missing fields."
                )
            if action == "status":
                return (
                    f"{common}\n"
                    "Route: quote.status. Call extract_quote_info to recover current quote state from conversation history, then summarize collected and missing details."
                )
            return (
                f"{common}\n"
                "Route: quote.start_or_provide_info. Call extract_quote_info immediately. "
                "If incomplete, ask only for missing_fields one at a time. "
                "If complete, restate customer name, email, each product and quantity, expected start date, and notes, then ask the user to review the on-screen dialog and confirm."
            )

        if intent == "knowledge_question":
            return (
                f"{common}\n"
                "Route: knowledge_question. Use the search tool first. "
                "If relevant information is found, answer from the knowledge base and call report_grounding for the actual sources used. "
                "If no relevant knowledge-base information is found, first clearly say the knowledge base does not contain relevant information, "
                "then answer from your own general knowledge. Make it clear which part is not sourced from the knowledge base."
            )

        if intent == "conversation_control":
            if action == "repeat":
                return f"{common}\nRoute: conversation_control.repeat. Briefly repeat your last useful answer. Do not call tools."
            if action == "slower":
                return f"{common}\nRoute: conversation_control.slower. Acknowledge and continue more slowly and simply. Do not call tools."
            if action == "restart":
                return (
                    f"{common}\n"
                    "Route: conversation_control.restart. Acknowledge the restart request and ask for the user's name and email again. Do not call tools yet."
                )
            if action == "stop":
                return f"{common}\nRoute: conversation_control.stop. Acknowledge that you will stop. Do not call tools."
            return f"{common}\nRoute: conversation_control. Help the user recover the conversation briefly. Do not call tools."

        return (
            f"{common}\n"
            "Route: unsupported_or_other. Ask one short clarifying question. Do not call tools unless the user clearly asks a supported business or knowledge-base question."
        )

    async def _maybe_trigger_quote_tool(self, session_id: str, transcript: str, client_ws: web.WebSocketResponse, server_ws: web.WebSocketResponse):
        """Trigger quote extraction tool based on keywords, only once per session."""
        # TEMPORARILY DISABLED: Let the model auto-tool-call instead to avoid duplicate invocations
        # The model with tool_choice=auto will automatically call extract_quote_info when it detects quote requests
        # keywords = ["quote", "quotation", "price", "pricing", "estimate", "estimate", "need a quote", "get a quote"]
        # text = transcript.lower()
        # if any(k in text for k in keywords):
        #     if session_id not in self._quote_triggered or not self._quote_triggered[session_id]:
        #         self._quote_triggered[session_id] = True
        #         logger.info("Keyword detected for quote, invoking tool for session %s", session_id)
        #         await self._invoke_tool("extract_quote_info", {}, session_id, client_ws, server_ws)
        pass  # Disabled manual trigger - relying on model auto tool-call
    
    async def _save_and_send_conversation(self, session_id: str):
        """Save conversation to file and send via email."""
        if session_id not in self._conversation_logs:
            return
        
        conversation = self._conversation_logs[session_id]
        if not conversation["messages"]:
            # No messages to save
            del self._conversation_logs[session_id]
            return
        
        try:
            # Create conversations directory if it doesn't exist
            conversations_dir = Path(__file__).parent / "conversations"
            conversations_dir.mkdir(exist_ok=True)
            
            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"conversation_{session_id[:8]}_{timestamp}.txt"
            filepath = conversations_dir / filename
            
            # Format conversation content
            content_lines = [
                f"Conversation Session: {session_id}",
                f"Start Time: {conversation['start_time']}",
                f"End Time: {datetime.now().isoformat()}",
                f"Total Messages: {len(conversation['messages'])}",
                "",
                "=" * 60,
                "",
            ]
            
            for msg in conversation["messages"]:
                role = msg["role"].upper()
                content = msg["content"]
                timestamp = msg.get("timestamp", "")
                content_lines.append(f"[{timestamp}] {role}:")
                content_lines.append(content)
                content_lines.append("")
            
            # Write to file
            filepath.write_text("\n".join(content_lines), encoding="utf-8")
            logger.info("Conversation saved to %s", filepath)
            
            # Send email with attachment
            from email_service import send_conversation_email
            email_sent = await send_conversation_email(
                to_email="2529044604@qq.com",
                conversation_file=str(filepath),
                session_id=session_id
            )
            
            if email_sent:
                logger.info("Conversation email sent successfully")
            else:
                logger.warning("Failed to send conversation email")
            
        except Exception as e:
            logger.error("Error saving/sending conversation: %s", str(e))
        finally:
            # Clean up conversation log
            if session_id in self._conversation_logs:
                del self._conversation_logs[session_id]
    
    def attach_to_app(self, app, path, acs_path: Optional[str] = None):
        app.router.add_get(path, self._websocket_handler)
        if acs_path:
            app.router.add_get(acs_path, self._acs_websocket_handler)
