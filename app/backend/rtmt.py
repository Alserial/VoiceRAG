import asyncio
import json
import logging
import os
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

import aiohttp
from aiohttp import web
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

logger = logging.getLogger("voicerag")

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
    api_version: str = "2024-10-01-preview"
    _tools_pending = {}
    _token_provider = None
    _conversation_logs = {}  # Store conversation logs per session
    _quote_triggered = {}    # Track quote trigger per session to avoid duplicates

    def __init__(self, endpoint: str, deployment: str, credentials: AzureKeyCredential | DefaultAzureCredential, voice_choice: Optional[str] = None):
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

    async def _process_message_to_client(self, msg: str, client_ws: web.WebSocketResponse, server_ws: web.WebSocketResponse) -> Optional[str]:
        message = json.loads(msg.data)
        updated_message = msg.data
        if message is not None:
            # Debug: Log message types that might contain user input
            msg_type = message.get("type", "")
            if "input_audio" in msg_type or "transcription" in msg_type:
                logger.debug("Received message type: %s, content: %s", msg_type, json.dumps(message)[:200])
            match message["type"]:
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
                    updated_message = json.dumps(message)

                case "response.output_item.added":
                    if "item" in message and message["item"]["type"] == "function_call":
                        updated_message = None

                case "conversation.item.created":
                    if "item" in message and message["item"]["type"] == "function_call":
                        item = message["item"]
                        if item["call_id"] not in self._tools_pending:
                            self._tools_pending[item["call_id"]] = RTToolCall(item["call_id"], message["previous_item_id"])
                        updated_message = None
                    elif "item" in message and message["item"]["type"] == "function_call_output":
                        updated_message = None
                    elif "item" in message and message["item"]["type"] == "input_audio_transcription":
                        # Record user input transcription (when created as item)
                        session_id = getattr(client_ws, "session_id", None)
                        if session_id and session_id in self._conversation_logs:
                            transcript = message["item"].get("transcript", "")
                            if transcript:
                                logger.info("Captured user input from item: %s", transcript[:50])
                                self._conversation_logs[session_id]["messages"].append({
                                    "role": "user",
                                    "content": transcript,
                                    "timestamp": datetime.now().isoformat()
                                })
                                await self._maybe_trigger_quote_tool(session_id, transcript, client_ws, server_ws)
                
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
                            logger.info("Captured user input: %s", transcript[:50])
                            self._conversation_logs[session_id]["messages"].append({
                                "role": "user",
                                "content": transcript,
                                "timestamp": datetime.now().isoformat()
                            })
                            await self._maybe_trigger_quote_tool(session_id, transcript, client_ws, server_ws)
                        else:
                            logger.warning("User input transcription message received but no transcript found. Message keys: %s", list(message.keys()))
                            logger.debug("Full message: %s", json.dumps(message)[:500])

                case "response.function_call_arguments.delta":
                    updated_message = None
                
                case "response.function_call_arguments.done":
                    updated_message = None

                case "response.output_item.done":
                    if "item" in message and message["item"]["type"] == "function_call":
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
                                "output": result.to_text() if result.destination == ToolResultDirection.TO_SERVER else ""
                            }
                        })
                        if result.destination == ToolResultDirection.TO_CLIENT:
                            # Send tool result to client for display
                            logger.info("Sending tool result to client: tool_name=%s", item["name"])
                            await client_ws.send_json({
                                "type": "extension.middle_tier_tool_response",
                                "previous_item_id": tool_call.previous_id,
                                "tool_name": item["name"],
                                "tool_result": result.to_text()
                            })
                        updated_message = None

                case "response.done":
                    if len(self._tools_pending) > 0:
                        self._tools_pending.clear() # Any chance tool calls could be interleaved across different outstanding responses?
                        await server_ws.send_json({
                            "type": "response.create"
                        })
                    if "response" in message:
                        replace = False
                        for i, output in enumerate(reversed(message["response"]["output"])):
                            if output["type"] == "function_call":
                                message["response"]["output"].pop(i)
                                replace = True
                        if replace:
                            updated_message = json.dumps(message)
                    # Record assistant response
                    session_id = getattr(client_ws, "session_id", None)
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
                
                case "response.audio_transcript.delta":
                    # Record assistant transcript delta
                    session_id = getattr(client_ws, "session_id", None)
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

        return updated_message

    async def _process_message_to_server(self, msg: str, ws: web.WebSocketResponse) -> Optional[str]:
        message = json.loads(msg.data)
        updated_message = msg.data
        if message is not None:
            msg_type = message.get("type", "")
            # Debug logging for user input messages
            if "input_audio" in msg_type or "transcription" in msg_type:
                logger.debug("Processing message type: %s, full message: %s", msg_type, json.dumps(message)[:300])
            
            match message["type"]:
                case "session.update":
                    session = message["session"]
                    if self.system_message is not None:
                        session["instructions"] = self.system_message
                    if self.temperature is not None:
                        session["temperature"] = self.temperature
                    if self.max_tokens is not None:
                        session["max_response_output_tokens"] = self.max_tokens
                    if self.disable_audio is not None:
                        session["disable_audio"] = self.disable_audio
                    if self.voice_choice is not None:
                        session["voice"] = self.voice_choice
                    session["tool_choice"] = "auto" if len(self.tools) > 0 else "none"
                    session["tools"] = [tool.schema for tool in self.tools.values()]
                    updated_message = json.dumps(message)

        return updated_message

    async def _forward_messages(self, ws: web.WebSocketResponse):
        session_id = getattr(ws, "session_id", str(uuid4()))
        ws.session_id = session_id
        
        async with aiohttp.ClientSession(base_url=self.endpoint) as session:
            params = { "api-version": self.api_version, "deployment": self.deployment}
            headers = {}
            if "x-ms-client-request-id" in ws.headers:
                headers["x-ms-client-request-id"] = ws.headers["x-ms-client-request-id"]
            if self.key is not None:
                headers = { "api-key": self.key }
            else:
                headers = { "Authorization": f"Bearer {self._token_provider()}" } # NOTE: no async version of token provider, maybe refresh token on a timer?
            async with session.ws_connect("/openai/realtime", headers=headers, params=params) as target_ws:
                async def from_client_to_server():
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            new_msg = await self._process_message_to_server(msg, ws)
                            if new_msg is not None:
                                await target_ws.send_str(new_msg)
                        else:
                            print("Error: unexpected message type:", msg.type)
                    
                    # Means it is gracefully closed by the client then time to close the target_ws
                    if target_ws:
                        print("Closing OpenAI's realtime socket connection.")
                        await target_ws.close()
                        
                async def from_server_to_client():
                    async for msg in target_ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            new_msg = await self._process_message_to_client(msg, ws, target_ws)
                            if new_msg is not None:
                                await ws.send_str(new_msg)
                        else:
                            print("Error: unexpected message type:", msg.type)

                try:
                    await asyncio.gather(from_client_to_server(), from_server_to_client())
                except ConnectionResetError:
                    # Ignore the errors resulting from the client disconnecting the socket
                    pass
                finally:
                    # Save conversation and send email when session ends
                    await self._save_and_send_conversation(session_id)

    async def _websocket_handler(self, request: web.Request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await self._forward_messages(ws)
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
                logger.info("Sending tool result to client: tool_name=%s, result_length=%d, preview: %s", 
                           tool_name, len(result_text), result_text[:200])
                await client_ws.send_json({
                    "type": "extension.middle_tier_tool_response",
                    "previous_item_id": None,
                    "tool_name": tool_name,
                    "tool_result": result_text
                })
                logger.info("Tool result sent to client successfully for tool: %s", tool_name)
        except Exception as e:
            logger.error("Error forwarding tool result: %s", str(e))

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
    
    def attach_to_app(self, app, path):
        app.router.add_get(path, self._websocket_handler)
