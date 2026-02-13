"""
Azure Communication Services (ACS) Call Automation Handler

This module handles incoming phone calls from ACS.

This module implements:
1. Receiving ACS Call Automation webhook events
2. Automatically answering incoming calls
3. Playing welcome voice messages
4. Tracking call status

Environment variables:
- ACS_CONNECTION_STRING: Azure Communication Services connection string
- ACS_CALLBACK_URL: Your publicly accessible callback URL (e.g., https://yourapp.com/api/acs/calls/events)
- ACS_PHONE_NUMBER: Your ACS phone number (e.g., +1234567890)
"""

import json
import logging
import os
import re
import time
from typing import Any, Optional

from aiohttp import web
from dotenv import load_dotenv

# Get logger first for use when imports fail
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voicerag")

# Lazy import ACS SDK to avoid module loading failure if import fails
try:
    from azure.communication.callautomation import CallAutomationClient
    # Speech intelligence / recognition related types (different SDK versions may vary, unified compatibility handling)
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
        # New SDK: Use AnswerCallOptions + CallIntelligenceOptions to configure cognitive services when answering
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

# Store active calls
_active_acs_calls: dict[str, dict[str, Any]] = {}

# ACS client (global singleton)
_acs_client: Optional[CallAutomationClient] = None


def get_acs_client() -> Optional[CallAutomationClient]:
    """Get or create ACS Call Automation client"""
    global _acs_client
    
    if not _acs_sdk_available or CallAutomationClient is None:
        logger.warning("ACS SDK not available, cannot create client")
        return None
    
    if _acs_client is not None:
        return _acs_client
    
    connection_string = os.environ.get("ACS_CONNECTION_STRING")
    # Additional logging: print raw connection string repr to help debug format issues (spaces / quotes / invisible characters, etc.)
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
    Handle incoming call event - automatically answer the call
    
    Args:
        event_data: IncomingCall event data sent by ACS
        
    Returns:
        Processing result
    """
    acs_client = get_acs_client()
    if not acs_client:
        logger.error("ACS client not available, cannot handle incoming call")
        return {"error": "ACS client not configured"}
    
    try:
        # Correctly parse event data (incomingCallContext is a string token, not an object)
        data = event_data.get("data", {})
        incoming_call_context = data.get("incomingCallContext", "")
        if not incoming_call_context:
            incoming_call_context = event_data.get("incomingCallContext", "")
        
        # Extract caller information from event data
        from_info = data.get("from", {})
        to_info = data.get("to", {})
        
        # Extract actual phone number (for speech recognition target_participant)
        caller_phone = from_info.get("phoneNumber", {}).get("value")
        recipient_phone = to_info.get("phoneNumber", {}).get("value")
        
        # Also save rawId (for logging/debugging)
        caller_raw_id = from_info.get("rawId", "")
        recipient_raw_id = to_info.get("rawId", "")
        
        logger.info("Incoming Call:")
        logger.info("   Caller Phone: %s", caller_phone or "unknown")
        logger.info("   Caller RawId: %s", caller_raw_id or "unknown")
        logger.info("   Recipient Phone: %s", recipient_phone or "unknown")
        logger.info("   Incoming Call Context: %s...", incoming_call_context[:50] if incoming_call_context else "None")
        
        if not incoming_call_context:
            logger.error("No incomingCallContext found in event data")
            return {"error": "No incomingCallContext in event"}
        
        # Get callback URL (do not auto-append /events, use original URL)
        callback_url = os.environ.get("ACS_CALLBACK_URL")
        if not callback_url:
            logger.error("ACS_CALLBACK_URL not configured")
            return {"error": "Callback URL not configured"}
        
        logger.info("   Callback URL: %s", callback_url)
        
        # Prepare Cognitive Services configuration (to enable TTS capability during call setup)
        cog_endpoint = os.environ.get("ACS_COGNITIVE_SERVICE_ENDPOINT", "").strip()
        answer_result = None
        
        logger.info("   ACS_COGNITIVE_SERVICE_ENDPOINT: %r", cog_endpoint or "NOT SET")
        
        try:
            # Prefer new SDK's AnswerCallOptions + CallIntelligenceOptions
            if cog_endpoint and 'AnswerCallOptions' in globals() and AnswerCallOptions is not None and CallIntelligenceOptions is not None:  # type: ignore[name-defined]
                logger.info("Answering call with CallIntelligenceOptions (cognitive_services_endpoint)...")
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
                # Some SDK versions expose cognitive_services_endpoint parameter directly on answer_call
                logger.info("Answering call with cognitive_services_endpoint kwarg...")
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
                # Cognitive service endpoint not configured, use most basic answer_call (can still connect, but may not be able to use some intelligent features)
                logger.warning("ACS_COGNITIVE_SERVICE_ENDPOINT not set; answering call without cognitive configuration.")
                answer_result = acs_client.answer_call(
                    incoming_call_context=incoming_call_context,
                    callback_url=callback_url,
                )
        except Exception as e:
            logger.error("Error calling answer_call with cognitive configuration: %s", str(e))
            import traceback
            logger.error("Traceback: %s", traceback.format_exc())
            # Final fallback: try simplest signature
            try:
                logger.info("Retrying basic answer_call without cognitive configuration...")
                answer_result = acs_client.answer_call(
                    incoming_call_context=incoming_call_context,
                    callback_url=callback_url,
                )
            except Exception as e2:
                logger.error("Fallback basic answer_call also failed: %s", str(e2))
                import traceback as tb
                logger.error("Traceback: %s", tb.format_exc())
                return {"error": f"answer_call failed: {e2}"}
        
        if answer_result and hasattr(answer_result, 'call_connection_id'):
            call_connection_id = answer_result.call_connection_id
            
            # Record active call (save actual phone number for subsequent speech recognition target_participant)
            _active_acs_calls[call_connection_id] = {
                "call_connection_id": call_connection_id,
                "caller_phone": caller_phone,  # Actual phone number, e.g., "+8615397262726", for PhoneNumberIdentifier
                "caller_raw_id": caller_raw_id,  # rawId like "4:+613...", only for logging/debugging
                "caller_info": from_info,  # Save complete from_info as fallback
                "recipient_phone": recipient_phone,
                "recipient_raw_id": recipient_raw_id,
                "status": "answered",
                "started_at": time.time()
            }
            
            logger.info("Call answered successfully!")
            logger.info("   Connection ID: %s", call_connection_id)
            
            return {
                "success": True,
                "call_connection_id": call_connection_id,
                "caller_phone": caller_phone,
                "message": "Call answered successfully"
            }
        else:
            logger.error("Failed to answer call - no connection ID returned")
            logger.error("   Answer result: %s", answer_result)
            return {"error": "Failed to answer call"}
            
    except Exception as e:
        logger.error("Error handling incoming call: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())
        return {"error": str(e)}


async def handle_call_connected_event(event_data: dict[str, Any]) -> None:
    """Handle call connected event"""
    try:
        # callConnectionId is in the data field
        event_data_obj = event_data.get("data", {})
        call_connection_id = event_data_obj.get("callConnectionId")
        
        logger.info("Call Connected - Connection ID: %s", call_connection_id)
        
        if call_connection_id and call_connection_id in _active_acs_calls:
            _active_acs_calls[call_connection_id]["status"] = "connected"
            logger.info("   Updated call status to 'connected'")
            
            # Play welcome voice message (fixed text / can be replaced with GPT text later)
            # Recognition automatically starts after welcome message playback completes (handled in handle_play_completed_event)
            await play_welcome_message(call_connection_id)
        else:
            logger.warning("   Call connection ID not found in active calls")
        
    except Exception as e:
        logger.error("Error handling call connected event: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())


async def handle_call_disconnected_event(event_data: dict[str, Any]) -> None:
    """Handle call disconnected event"""
    try:
        # callConnectionId is in the data field
        event_data_obj = event_data.get("data", {})
        call_connection_id = event_data_obj.get("callConnectionId")
        result_info = event_data_obj.get("resultInformation", {})
        disconnect_reason = result_info.get("message", "Unknown reason")
        
        logger.info("Call Disconnected - Connection ID: %s", call_connection_id)
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
    """Handle audio playback completed event"""
    try:
        event_data_obj = event_data.get("data", {})
        call_connection_id = event_data_obj.get("callConnectionId")
        operation_context = event_data_obj.get("operationContext")
        
        logger.info("Play Completed - Connection ID: %s, Operation Context: %s", call_connection_id, operation_context)
        
        if call_connection_id and call_connection_id in _active_acs_calls:
            if operation_context == "welcome-tts":
                # Welcome message playback completed, start first speech recognition
                _active_acs_calls[call_connection_id]["welcome_played"] = True
                logger.info("Welcome message playback completed, starting first speech recognition...")
                await start_speech_recognition(call_connection_id)
            elif operation_context == "answer-tts":
                # Answer playback completed, restart recognition for multi-turn conversation
                logger.info("Answer playback completed, restarting speech recognition for next question...")
                await start_speech_recognition(call_connection_id)
            else:
                # Other playback completed events (may be error messages, etc.), do not restart recognition
                logger.info("Play completed for context: %s (not restarting recognition)", operation_context)
        
    except Exception as e:
        logger.error("Error handling play completed event: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())


async def handle_play_failed_event(event_data: dict[str, Any]) -> None:
    """Handle audio playback failed event (detailed logging of Cognitive Services error information)"""
    try:
        data = event_data.get("data", {}) or {}
        call_connection_id = data.get("callConnectionId") or event_data.get("callConnectionId")

        result_info = data.get("resultInformation", {}) or {}
        logger.warning("Play failed - call=%s", call_connection_id)
        logger.warning("resultInformation=%s", json.dumps(result_info, ensure_ascii=False))

        # Sometimes deeper in details there are specific speechErrorCode / subcode
        if isinstance(result_info, dict) and "details" in result_info:
            logger.warning("resultInformation.details=%s", json.dumps(result_info["details"], ensure_ascii=False))

        # To fully reproduce the issue, temporarily print the entire event here (truncated to 5000 characters)
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
    Handle speech recognition completed event:
    1. Get what the user said (converted to text) from the event
    2. Detect if it's a quote request, if so collect quote information
    3. Call GPT to generate answer
    4. Play answer using ACS TTS
    """
    try:
        data = event_data.get("data", {}) or {}
        call_connection_id = data.get("callConnectionId")

        logger.info("RecognizeCompleted for call: %s", call_connection_id)
        logger.info("Recognize event data: %s", json.dumps(data, ensure_ascii=False))

        # Different versions / modes may have recognition results in different fields, try to find compatibly
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
                # Common field names
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
            # Fallback: search once more in the entire event_data
            user_text = _find_transcript(event_data)

        if not user_text:
            logger.warning("RecognizeCompleted received but no transcript text found.")
            if call_connection_id:
                logger.info("Restarting speech recognition because transcript was empty.")
                await start_speech_recognition(call_connection_id)
            return

        logger.info("User said (transcript): %s", user_text)

        # Initialize quote state for the call (if not already initialized)
        if call_connection_id and call_connection_id not in _active_acs_calls:
            _active_acs_calls[call_connection_id] = {
                "call_connection_id": call_connection_id,
                "status": "active",
            }
            logger.info("Initialized new call state for: %s", call_connection_id)
        
        # Handle quote logic
        if call_connection_id:
            call_info = _active_acs_calls.get(call_connection_id, {})
            quote_state = call_info.get("quote_state", {})
            conversation_history = call_info.get("conversation_history", [])
            
            # Print current conversation history
            logger.info("=" * 80)
            logger.info("CONVERSATION HISTORY (call: %s, messages: %d)", call_connection_id, len(conversation_history))
            for idx, msg in enumerate(conversation_history[-5:], 1):  # Only print last 5 messages
                role = msg.get("role", "unknown")
                content = msg.get("content", "")[:100]  # Truncate to 100 characters
                logger.info("  [%d] %s: %s", idx, role.upper(), content)
            logger.info("=" * 80)
            
            # Print current quote state
            if quote_state:
                logger.info("CURRENT QUOTE STATE (call: %s)", call_connection_id)
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
                logger.info("NO QUOTE STATE (call: %s) - Regular conversation", call_connection_id)
            
            # First update quote state (extract information)
            answer_text, quote_updated = await generate_answer_text_with_gpt(
                user_text, call_connection_id
            )
            
            # Re-get updated quote state
            updated_call_info = _active_acs_calls.get(call_connection_id, {})
            quote_state = updated_call_info.get("quote_state", {})
            updated_conversation = updated_call_info.get("conversation_history", [])
            
            # Print updated conversation history
            if len(updated_conversation) > len(conversation_history):
                logger.info("UPDATED CONVERSATION HISTORY (call: %s, total messages: %d)", 
                          call_connection_id, len(updated_conversation))
                for idx, msg in enumerate(updated_conversation[-3:], len(updated_conversation) - 2):
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")[:100]
                    logger.info("  [%d] %s: %s", idx, role.upper(), content)
            
            # Print updated quote state
            if quote_state:
                logger.info("UPDATED QUOTE STATE (call: %s)", call_connection_id)
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
            
            # Check if it's a quote confirmation (LLM semantic classification; explicit yes/confirm fast path)
            is_confirmation = await _is_confirmation(user_text, updated_conversation, quote_state)
            logger.info("BRANCH: Confirmation check - user_text='%s', is_confirmation=%s, is_complete=%s", 
                       user_text, is_confirmation, quote_state.get("is_complete", False))
            
            if quote_state.get("is_complete") and is_confirmation:
                logger.info("BRANCH: Entering QUOTE CONFIRMATION branch (creating quote)")
                # User confirmed quote, create quote
                logger.info("=" * 80)
                logger.info("USER CONFIRMED QUOTE REQUEST - Creating quote in Salesforce...")
                logger.info("  Call ID: %s", call_connection_id)
                extracted = quote_state.get("extracted", {})
                logger.info("  Quote Details:")
                logger.info("    - Customer: %s", extracted.get("customer_name"))
                logger.info("    - Contact: %s", extracted.get("contact_info"))
                quote_items = extracted.get("quote_items", [])
                for item in quote_items:
                    logger.info("    - Product: %s x %s", item.get("product_package"), item.get("quantity"))
                logger.info("=" * 80)
                quote_result = await create_quote_from_state(call_connection_id, quote_state)
                if quote_result:
                    logger.info("SUB-BRANCH: Quote creation SUCCESS")
                    answer_text = (
                        f"Great! I've created your quote. "
                        f"The quote number is {quote_result.get('quote_number', 'N/A')}. "
                        f"An email with the quote details has been sent to your email address. "
                        f"Is there anything else I can help you with?"
                    )
                    # Clear quote state
                    if call_connection_id in _active_acs_calls:
                        _active_acs_calls[call_connection_id].pop("quote_state", None)
                        logger.info("Cleared quote_state after successful creation")
                else:
                    logger.info("SUB-BRANCH: Quote creation FAILED")
                    answer_text = (
                        "I'm sorry, I couldn't create the quote at this time. "
                        "Please try again later or contact our support team."
                    )
            elif quote_updated and quote_state.get("is_complete"):
                logger.info("BRANCH: Entering QUOTE COMPLETE (waiting for confirmation) branch")
                # Quote information is complete, provide full recap before confirmation
                recap = _build_quote_confirmation_recap(quote_state)
                answer_text = (
                    f"{recap} "
                    "Please say 'confirm' or 'yes' to create the quote, "
                    "or let me know if you'd like to make any changes."
                )
            else:
                logger.info("BRANCH: Entering REGULAR FLOW branch (no confirmation needed)")
        else:
            logger.info("BRANCH: Entering SIMPLE MODE branch (no call_connection_id)")
            # No call_connection_id, use simple mode
            answer_text, _ = await generate_answer_text_with_gpt(user_text, None)

        # Play answer
        if call_connection_id:
            await play_answer_message(call_connection_id, answer_text)
        else:
            logger.warning("No call_connection_id in RecognizeCompleted event; cannot play answer.")

    except Exception as e:
        logger.error("Error handling RecognizeCompleted event: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())
        # Inform caller that the Q&A flow has a problem, for debugging
        try:
            data = event_data.get("data", {}) or {}
            call_connection_id = data.get("callConnectionId") or event_data.get("callConnectionId")
        except Exception:
            call_connection_id = None
        await speak_error_message(call_connection_id, debug_tag="recognize-completed-exception")


async def handle_recognize_completed_event(event_data: dict[str, Any]) -> None:
    """Compatible with old call path, forward to new handler function."""
    await handle_recognize_completed(event_data)


async def handle_recognize_failed_event(event_data: dict[str, Any]) -> None:
    """Handle speech recognition failed event, mainly for log troubleshooting"""
    try:
        data = event_data.get("data", {}) or {}
        call_connection_id = data.get("callConnectionId")
        result_info = data.get("resultInformation", {}) or {}

        logger.warning("RecognizeFailed - call=%s", call_connection_id)
        logger.warning("resultInformation=%s", json.dumps(result_info, ensure_ascii=False))

        # Prompt once on the phone that "system error" occurred, so you know it's a recognition stage problem
        await speak_error_message(call_connection_id, debug_tag="recognize-failed")

    except Exception as e:
        logger.error("Error handling RecognizeFailed event: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())


async def generate_answer_text_with_gpt(user_text: str, call_connection_id: Optional[str] = None) -> tuple[str, bool]:
    """
    Use Azure OpenAI to generate answer based on user speech converted to text (phone Q&A core logic).
    
    Supports quote functionality:
    - Detect quote intent
    - Collect quote information
    - Generate natural conversation answers
    
    Returns:
        tuple[str, bool]: (answer text, whether quote state was updated)
    """
    # If GPT is not available, return a fixed message to avoid phone silence
    fallback = "I am sorry, I could not process your question. Please try again later."

    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.identity import DefaultAzureCredential
        from openai import AzureOpenAI
    except Exception as e:
        logger.warning("Azure OpenAI SDK not available, using fallback answer. Error: %s", str(e))
        return fallback, False

    openai_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    openai_deployment = (
        os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        or os.environ.get("AZURE_OPENAI_EXTRACTION_DEPLOYMENT")
        or "gpt-4o-mini"
    )
    llm_key = os.environ.get("AZURE_OPENAI_API_KEY")

    # Immediately output model information being used
    logger.info("GPT Model Configuration - Deployment: %s, Endpoint: %s", openai_deployment, openai_endpoint or "NOT SET")

    if not openai_endpoint or not openai_deployment:
        logger.warning("Azure OpenAI endpoint/deployment not configured. Using fallback answer.")
        return fallback, False

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

        # Get current call's conversation history (for quote information extraction)
        conversation_history = []
        quote_state = {}
        if call_connection_id and call_connection_id in _active_acs_calls:
            call_info = _active_acs_calls[call_connection_id]
            quote_state = call_info.get("quote_state", {})
            conversation_history = call_info.get("conversation_history", [])
        
        # Add current user message to history (if not already added)
        if not conversation_history or conversation_history[-1].get("content") != user_text:
            conversation_history.append({"role": "user", "content": user_text})
            logger.info("Added user message to conversation history (total: %d messages)", len(conversation_history))
        # Only keep last 10 messages
        if len(conversation_history) > 10:
            conversation_history = conversation_history[-10:]
            logger.info("Trimmed conversation history to last 10 messages")
        
        # Update conversation history in call state
        if call_connection_id and call_connection_id in _active_acs_calls:
            _active_acs_calls[call_connection_id]["conversation_history"] = conversation_history
            logger.info("Saved conversation history to call state (call: %s, messages: %d)", 
                       call_connection_id, len(conversation_history))
        
        behavior = await _classify_user_behavior_with_llm(
            client,
            openai_deployment,
            user_text,
            conversation_history,
            bool(quote_state),
            bool(quote_state.get("is_complete")),
        )

        # When user asks "what did I provide", prioritize using current extracted state to answer
        is_recall_question = behavior == "recall_quote_info"
        logger.info("BRANCH: Recall question check - behavior=%s, is_recall_question=%s, has_quote_state=%s", 
                   behavior, is_recall_question, bool(quote_state))
        if quote_state and is_recall_question:
            logger.info("BRANCH: Entering QUOTE RECALL branch (user asking for quote info)")
            requested_fields = await _extract_recap_requested_fields(user_text, conversation_history)
            recap = _build_quote_targeted_recap(quote_state, requested_fields)
            if quote_state.get("is_complete"):
                logger.info("SUB-BRANCH: Quote is complete, answering requested recap and asking for confirmation")
                return (
                    f"{recap} Please say 'confirm' or 'yes' to create the quote, "
                    "or tell me what you'd like to change.",
                    False,
                )

            logger.info("SUB-BRANCH: Quote incomplete, answering requested recap and asking for missing fields")
            missing_fields = quote_state.get("missing_fields", [])
            follow_up = _generate_quote_collection_response(missing_fields, quote_state)
            return f"{recap} {follow_up}", False

        # Detect if it's a quote request (LLM semantic classification)
        is_quote_request = behavior == "quote_request"
        logger.info("BRANCH: Quote intent detection - behavior=%s, is_quote_request=%s, call_connection_id=%s", 
                   behavior, is_quote_request, call_connection_id is not None)
        quote_updated = False
        
        if is_quote_request and call_connection_id:
            logger.info("BRANCH: Entering QUOTE REQUEST branch")
            # Extract quote information
            logger.info("=" * 80)
            logger.info("QUOTE REQUEST DETECTED - Extracting quote information...")
            logger.info("  Call ID: %s", call_connection_id)
            logger.info("  Conversation history length: %d", len(conversation_history))
            logger.info("  Current quote state: %s", json.dumps(quote_state, ensure_ascii=False, default=str)[:200])
            logger.info("=" * 80)
            
            quote_state = await _extract_quote_info_phone(conversation_history, quote_state)
            quote_updated = True
            
            # Print extraction results
            logger.info("QUOTE EXTRACTION RESULT:")
            extracted = quote_state.get("extracted", {})
            logger.info("  - Extracted Customer Name: %s", extracted.get("customer_name") or "None")
            logger.info("  - Extracted Contact Info: %s", extracted.get("contact_info") or "None")
            quote_items = extracted.get("quote_items", [])
            logger.info("  - Extracted Quote Items: %d items", len(quote_items))
            for idx, item in enumerate(quote_items, 1):
                logger.info("      [%d] %s x %s", idx, item.get("product_package"), item.get("quantity"))
            logger.info("  - Missing Fields: %s", quote_state.get("missing_fields", []))
            logger.info("  - Is Complete: %s", quote_state.get("is_complete", False))
            
            # Update call state
            if call_connection_id in _active_acs_calls:
                _active_acs_calls[call_connection_id]["quote_state"] = quote_state
                _active_acs_calls[call_connection_id]["conversation_history"] = conversation_history
                logger.info("Updated call state with quote information")
            
            # Generate answer based on missing fields
            missing_fields = quote_state.get("missing_fields", [])
            if missing_fields:
                logger.info("SUB-BRANCH: Quote collection - missing fields, asking for: %s", missing_fields)
                answer_text = _generate_quote_collection_response(missing_fields, quote_state)
            else:
                logger.info("SUB-BRANCH: Quote collection - all fields complete, asking for confirmation")
                # Information is complete, provide full recap before confirmation
                recap = _build_quote_confirmation_recap(quote_state)
                answer_text = (
                    f"{recap} "
                    "Please say 'confirm' or 'yes' to create the quote."
                )
        else:
            logger.info("BRANCH: Entering NON-QUOTE-REQUEST branch (regular Q&A or continuing quote collection)")
            # Regular Q&A or continue collecting quote information
            if quote_state and not quote_state.get("is_complete"):
                logger.info("SUB-BRANCH: Continuing quote collection (quote_state exists but incomplete)")
                # Currently collecting quote information, continue extraction
                logger.info("CONTINUING QUOTE COLLECTION - Extracting additional information...")
                logger.info("  Call ID: %s", call_connection_id)
                logger.info("  Previous missing fields: %s", quote_state.get("missing_fields", []))
                
                quote_state = await _extract_quote_info_phone(conversation_history, quote_state)
                quote_updated = True
                
                # Print updated state
                logger.info("QUOTE COLLECTION UPDATE:")
                extracted = quote_state.get("extracted", {})
                logger.info("  - Customer Name: %s", extracted.get("customer_name") or "NOT SET")
                logger.info("  - Contact Info: %s", extracted.get("contact_info") or "NOT SET")
                quote_items = extracted.get("quote_items", [])
                logger.info("  - Quote Items: %d items", len(quote_items))
                for item in quote_items:
                    logger.info("      * %s x %s", item.get("product_package"), item.get("quantity"))
                logger.info("  - Missing Fields: %s", quote_state.get("missing_fields", []))
                logger.info("  - Is Complete: %s", quote_state.get("is_complete", False))
                
                if call_connection_id and call_connection_id in _active_acs_calls:
                    _active_acs_calls[call_connection_id]["quote_state"] = quote_state
                    logger.info("Updated call state with new quote information")
                
                missing_fields = quote_state.get("missing_fields", [])
                if missing_fields:
                    logger.info("SUB-SUB-BRANCH: Still missing fields, asking for: %s", missing_fields)
                    answer_text = _generate_quote_collection_response(missing_fields, quote_state)
                else:
                    logger.info("SUB-SUB-BRANCH: All fields complete, asking for confirmation")
                    recap = _build_quote_confirmation_recap(quote_state)
                    answer_text = (
                        f"{recap} "
                        "Please say 'confirm' or 'yes' to create the quote."
                    )
            else:
                logger.info("SUB-BRANCH: Regular Q&A (no quote_state or quote_state is complete)")
                # Regular Q&A
                system_prompt = (
                    "You are a helpful support assistant speaking on a phone call. "
                    "Answer briefly and clearly in natural English. "
                    "Keep each answer under 3 sentences. "
                    "If the user asks about quotes, pricing, or estimates, help them request a quote."
                )
                
                logger.info("Using GPT model: %s (endpoint: %s)", openai_deployment, openai_endpoint)
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
                response = client.chat.completions.create(
                    model=openai_deployment,
                    messages=context_messages,
                    temperature=0.4,
                    max_tokens=128,
                )
                text = (response.choices[0].message.content or "").strip()
                if not text:
                    logger.warning("GPT returned empty answer text, using fallback.")
                    return fallback, False
                answer_text = text

        logger.info("Answer text from GPT: %s", answer_text)
        return answer_text, quote_updated
    except Exception as e:
        logger.error("Failed to generate answer text via Azure OpenAI: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())
        return fallback, False


async def _is_confirmation(user_text: str, conversation_history: list, quote_state: dict) -> bool:
    """Use LLM to classify final quote confirmation (keep very explicit yes/confirm fast path)."""
    user_lower = (user_text or "").lower().strip()
    normalized = re.sub(r"[^a-z\s]", " ", user_lower)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    if normalized in {"yes", "confirm"}:
        return True

    openai_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    openai_deployment = (
        os.environ.get("AZURE_OPENAI_EXTRACTION_DEPLOYMENT")
        or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        or "gpt-4o-mini"
    )
    llm_key = os.environ.get("AZURE_OPENAI_API_KEY")

    if not openai_endpoint:
        logger.warning("Confirmation classification skipped: missing AZURE_OPENAI_ENDPOINT")
        return False

    try:
        from openai import AzureOpenAI

        if llm_key:
            client = AzureOpenAI(
                api_key=llm_key,
                api_version="2024-02-15-preview",
                azure_endpoint=openai_endpoint,
            )
        else:
            from azure.identity import DefaultAzureCredential

            token = DefaultAzureCredential().get_token("https://cognitiveservices.azure.com/.default").token
            client = AzureOpenAI(
                api_key=token,
                api_version="2024-02-15-preview",
                azure_endpoint=openai_endpoint,
            )

        recent_history = [
            {
                "role": ("assistant" if msg.get("role") == "assistant" else "user"),
                "content": msg.get("content", ""),
            }
            for msg in (conversation_history or [])[-6:]
            if isinstance(msg, dict) and msg.get("content")
        ]

        payload = {
            "latest_user_text": user_text,
            "quote_state_complete": bool((quote_state or {}).get("is_complete")),
            "recent_history": recent_history,
            "task": "final_quote_confirmation",
        }

        response = client.chat.completions.create(
            model=openai_deployment,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify whether the user is explicitly confirming final quote creation right now. "
                        "Return JSON only with field 'state' and value confirm or other. "
                        "Use semantics and context, not keywords only. "
                        "If user is modifying details, asking questions, hesitating, or saying maybe, return other."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=48,
        )

        content = (response.choices[0].message.content or "{}").strip()
        result = json.loads(content)
        return result.get("state") == "confirm"
    except Exception as e:
        logger.warning("LLM confirmation classification failed: %s", str(e))
        return False


def _build_quote_confirmation_recap(quote_state: dict) -> str:
    """Build a concise recap sentence for collected quote info."""
    extracted = quote_state.get("extracted", {}) if isinstance(quote_state, dict) else {}
    customer_name = extracted.get("customer_name") or "not provided"
    contact_info = extracted.get("contact_info") or "not provided"
    expected_start_date = extracted.get("expected_start_date") or "not provided"
    notes = extracted.get("notes") or "none"

    quote_items = extracted.get("quote_items") or []
    valid_items = [
        item for item in quote_items
        if isinstance(item, dict) and item.get("product_package") and item.get("quantity")
    ]
    if valid_items:
        product_text = ", ".join(
            f"{item.get('product_package')} x{item.get('quantity')}" for item in valid_items
        )
    else:
        product_text = "not provided"

    return (
        f"Let me recap: name {customer_name}, contact {contact_info}, "
        f"products {product_text}, expected start date {expected_start_date}, notes {notes}."
    )


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


def _build_quote_targeted_recap(quote_state: dict, requested_fields: list[str]) -> str:
    """Build recap text for requested fields; if none specified, fallback to full recap."""
    if not requested_fields:
        return _build_quote_confirmation_recap(quote_state)

    extracted = quote_state.get("extracted", {}) if isinstance(quote_state, dict) else {}
    parts = []
    if "customer_name" in requested_fields:
        parts.append(f"name {extracted.get('customer_name') or 'not provided'}")
    if "contact_info" in requested_fields:
        parts.append(f"contact {extracted.get('contact_info') or 'not provided'}")
    if "quote_items" in requested_fields:
        items = extracted.get("quote_items") or []
        valid_items = [it for it in items if isinstance(it, dict) and it.get("product_package") and it.get("quantity")]
        product_text = ", ".join(f"{it.get('product_package')} x{it.get('quantity')}" for it in valid_items) if valid_items else "not provided"
        parts.append(f"products {product_text}")
    if "expected_start_date" in requested_fields:
        parts.append(f"expected start date {extracted.get('expected_start_date') or 'not provided'}")
    if "notes" in requested_fields:
        parts.append(f"notes {extracted.get('notes') or 'none'}")

    if not parts:
        return _build_quote_confirmation_recap(quote_state)
    return "Here is what I have: " + ", ".join(parts) + "."


def _is_quote_info_recall_question(user_text: str) -> bool:
    """Detect if user is asking to recall previously provided quote details."""
    normalized = re.sub(r"\s+", " ", (user_text or "").lower()).strip()
    if not normalized:
        return False

    recall_triggers = [
        "what did i provide",
        "what info did i provide",
        "what information did i provide",
        "what did i say",
        "what do you have",
        "what details do you have",
        "repeat",
        "recap",
        "my email",
        "my contact",
        "my name",
        "what is my",
    ]
    return any(trigger in normalized for trigger in recall_triggers)


async def _detect_quote_intent(user_text: str, conversation_history: list) -> bool:
    """Keep compatibility for old callsites; now uses LLM semantic classification."""
    openai_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    openai_deployment = (
        os.environ.get("AZURE_OPENAI_EXTRACTION_DEPLOYMENT")
        or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        or "gpt-4o-mini"
    )
    llm_key = os.environ.get("AZURE_OPENAI_API_KEY")

    if not openai_endpoint:
        return False

    try:
        from openai import AzureOpenAI

        if llm_key:
            client = AzureOpenAI(
                api_key=llm_key,
                api_version="2024-02-15-preview",
                azure_endpoint=openai_endpoint,
            )
        else:
            from azure.identity import DefaultAzureCredential

            token = DefaultAzureCredential().get_token("https://cognitiveservices.azure.com/.default").token
            client = AzureOpenAI(
                api_key=token,
                api_version="2024-02-15-preview",
                azure_endpoint=openai_endpoint,
            )

        behavior = await _classify_user_behavior_with_llm(
            client=client,
            deployment=openai_deployment,
            user_text=user_text,
            conversation_history=conversation_history,
            has_quote_state=False,
            quote_complete=False,
        )
        return behavior == "quote_request"
    except Exception:
        return False




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
        "- recall_quote_info: user asks to repeat/recap what they already provided (name/contact/product/quantity/date/notes).\n"
        "- general_qa: regular Q&A not about quote flow.\n"
        "Rules:\n"
        "1) If user is explicitly asking for previously provided details, choose recall_quote_info.\n"
        "2) If user is giving or modifying details for quote flow, choose quote_request.\n"
        "3) If not quote related, choose general_qa.\n"
        "4) Use conversation context, not keywords only."
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
        if behavior in {"quote_request", "recall_quote_info", "general_qa"}:
            logger.info("LLM behavior classification: %s", behavior)
            return behavior
        logger.warning("Unknown behavior from classifier: %s", behavior)
    except Exception as e:
        logger.warning("LLM behavior classification failed, fallback to general_qa: %s", str(e))

    return "general_qa"

async def _extract_quote_info_phone(conversation_history: list, current_state: dict) -> dict:
    """
    Extract quote information from conversation history (phone version)
    
    Reuse quote_tools logic, but adapt to phone conversation format
    """
    try:
        logger.info("EXTRACTING QUOTE INFO FROM CONVERSATION")
        logger.info("  Conversation history length: %d messages", len(conversation_history))
        logger.info("  Current state: %s", json.dumps(current_state, ensure_ascii=False, default=str)[:200])
        
        # Build conversation text
        conversation_text = "\n".join([
            f"{msg.get('role', 'user').upper()}: {msg.get('content', '')}"
            for msg in conversation_history[-10:]
        ])
        logger.info("  Conversation text length: %d characters", len(conversation_text))
        
        # Get available products
        from salesforce_service import get_salesforce_service
        sf_service = get_salesforce_service()
        products = []
        
        if sf_service.is_available():
            try:
                logger.info("Fetching available products from Salesforce...")
                result = sf_service.sf.query(
                    "SELECT Id, Name FROM Product2 WHERE IsActive = true ORDER BY Name LIMIT 100"
                )
                if result["totalSize"] > 0:
                    products = [
                        {"id": record["Id"], "name": record["Name"]}
                        for record in result["records"]
                    ]
                    logger.info("  Found %d available products", len(products))
                    product_names = [p["name"] for p in products[:5]]  # Only print first 5
                    logger.info("  Sample products: %s", ", ".join(product_names))
                else:
                    logger.warning("  No products found in Salesforce")
            except Exception as e:
                logger.error("Error fetching products: %s", str(e))
        else:
            logger.warning("Salesforce service not available, cannot fetch products")
        
        # Use GPT to extract information
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
        
        product_names = [p["name"] for p in products] if products else []
        product_list_text = ", ".join(product_names) if product_names else "No products available"
        
        # Merge currently extracted information
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
        
        logger.info("Calling GPT for quote extraction (deployment: %s)", openai_deployment)
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
        
        logger.info("GPT extraction response received")
        new_extracted = json.loads(response.choices[0].message.content)
        logger.info("  Extracted data: %s", json.dumps(new_extracted, ensure_ascii=False, default=str)[:300])
        
        # Merge extracted data (new data overwrites old data)
        logger.info("Merging extracted data with current state...")
        for key in ["customer_name", "contact_info", "expected_start_date", "notes"]:
            old_value = extracted_data.get(key)
            new_value = new_extracted.get(key)
            if new_value:
                extracted_data[key] = new_value
                if old_value != new_value:
                    logger.info("    Updated %s: '%s' -> '%s'", key, old_value, new_value)
        
        # Merge quote_items (append new items)
        if new_extracted.get("quote_items"):
            existing_items = extracted_data.get("quote_items", [])
            new_items = new_extracted["quote_items"]
            logger.info("  Merging quote_items: existing=%d, new=%d", len(existing_items), len(new_items))
            # Simple deduplication logic: if product name is the same, update quantity
            for new_item in new_items:
                if not isinstance(new_item, dict):
                    continue
                product_name = new_item.get("product_package")
                quantity = new_item.get("quantity")
                if product_name:
                    # Check if already exists
                    found = False
                    for existing_item in existing_items:
                        if isinstance(existing_item, dict) and existing_item.get("product_package") == product_name:
                            old_quantity = existing_item.get("quantity")
                            existing_item["quantity"] = quantity
                            found = True
                            if old_quantity != quantity:
                                logger.info("    Updated quantity for %s: %s -> %s", product_name, old_quantity, quantity)
                            break
                    if not found:
                        existing_items.append(new_item)
                        logger.info("    Added new product: %s x %s", product_name, quantity)
            extracted_data["quote_items"] = existing_items
            logger.info("  Final quote_items count: %d", len(extracted_data["quote_items"]))
        
        # Product matching (using quote_tools logic)
        if extracted_data.get("quote_items") and products:
            logger.info("Matching products with available products...")
            from quote_tools import _find_best_product_match
            matched_items = []
            for item in extracted_data["quote_items"]:
                if not isinstance(item, dict):
                    continue
                user_product = item.get("product_package")
                quantity = item.get("quantity")
                if user_product:
                    matched_product = _find_best_product_match(user_product, products)
                    if matched_product:
                        if matched_product != user_product:
                            logger.info("    Matched '%s' -> '%s'", user_product, matched_product)
                        matched_items.append({
                            "product_package": matched_product,
                            "quantity": quantity or 1
                        })
                    else:
                        logger.warning("    No match found for '%s' (keeping original)", user_product)
                        matched_items.append({
                            "product_package": user_product,
                            "quantity": quantity or 1
                        })
            extracted_data["quote_items"] = matched_items
            logger.info("  Product matching completed: %d items", len(matched_items))
        
        # Email normalization
        if extracted_data.get("contact_info"):
            from quote_tools import normalize_email
            original_contact = extracted_data["contact_info"]
            normalized_email = normalize_email(str(original_contact))
            if normalized_email:
                if normalized_email != original_contact:
                    logger.info("Normalized email: '%s' -> '%s'", original_contact, normalized_email)
                extracted_data["contact_info"] = normalized_email
            else:
                logger.warning("Could not normalize contact info: '%s'", original_contact)
        
        # Determine missing fields
        logger.info("Validating extracted data...")
        missing_fields = []
        if not extracted_data.get("customer_name"):
            missing_fields.append("customer_name")
            logger.info("    Missing: customer_name")
        if not extracted_data.get("contact_info"):
            missing_fields.append("contact_info")
            logger.info("    Missing: contact_info")
        
        # Check quote_items
        quote_items = extracted_data.get("quote_items", [])
        valid_items = [
            item for item in quote_items 
            if isinstance(item, dict) and 
               item.get("product_package") and 
               item.get("quantity") is not None and 
               item.get("quantity") > 0
        ]
        if not valid_items:
            missing_fields.append("quote_items")
            logger.info("    Missing: quote_items (or invalid)")
        else:
            logger.info("    Valid quote_items: %d items", len(valid_items))
        
        is_complete = len(missing_fields) == 0
        logger.info("Extraction result: is_complete=%s, missing_fields=%s", is_complete, missing_fields)
        
        result = {
            "extracted": extracted_data,
            "missing_fields": missing_fields,
            "products_available": product_names,
            "is_complete": is_complete,
        }
        logger.info("Final quote state: %s", json.dumps(result, ensure_ascii=False, default=str)[:400])
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
    """Generate response for collecting quote information based on missing fields"""
    extracted = quote_state.get("extracted", {})
    products_available = quote_state.get("products_available", [])
    
    if "customer_name" in missing_fields:
        return "I'd be happy to help you with a quote. May I have your name, please?"
    
    if "contact_info" in missing_fields:
        customer_name = extracted.get("customer_name", "")
        if customer_name:
            return f"Thank you, {customer_name}. What's your email address or phone number?"
        return "What's your email address or phone number?"
    
    if "quote_items" in missing_fields:
        if products_available:
            products_text = ", ".join(products_available[:5])  # Only list first 5
            return f"Which product would you like a quote for? Available products include: {products_text}. And how many would you need?"
        return "Which product would you like a quote for, and how many would you need?"
    
    return "I need a bit more information for your quote. Could you provide the missing details?"


async def create_quote_from_state(call_connection_id: str, quote_state: dict) -> Optional[dict]:
    """Create Salesforce quote from quote state"""
    try:
        logger.info("=" * 80)
        logger.info("CREATING QUOTE FROM STATE")
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
            logger.error("Incomplete quote information: customer_name=%s, contact_info=%s, quote_items=%s",
                        customer_name, contact_info, quote_items)
            return None
        
        # Call Salesforce to create quote
        from email_service import send_quote_email
        from salesforce_service import get_salesforce_service
        
        sf_service = get_salesforce_service()
        if not sf_service.is_available():
            logger.error("Salesforce service not available")
            return None
        
        # Create or get Account
        logger.info("Creating/getting Account in Salesforce...")
        account_id = sf_service.create_or_get_account(customer_name, contact_info)
        if not account_id:
            logger.warning("Failed to create/get Account, will create Quote without Account association")
        else:
            logger.info("Account ID: %s", account_id)
        
        # Create or get Contact
        contact_id = None
        if account_id:
            logger.info("Creating/getting Contact in Salesforce...")
            contact_id = sf_service.create_or_get_contact(account_id, customer_name, contact_info)
            if contact_id:
                logger.info("Contact ID: %s", contact_id)
        
        # Create Opportunity (optional)
        opportunity_id = None
        if os.environ.get("SALESFORCE_CREATE_OPPORTUNITY", "false").lower() == "true" and account_id:
            logger.info("Creating Opportunity in Salesforce...")
            opportunity_id = sf_service.create_opportunity(
                account_id,
                f"Opportunity for {customer_name}"
            )
            if opportunity_id:
                logger.info("Opportunity ID: %s", opportunity_id)
        
        # Create Quote
        logger.info("Creating Quote in Salesforce...")
        quote_result = sf_service.create_quote(
            account_id=account_id,
            opportunity_id=opportunity_id,
            customer_name=customer_name,
            quote_items=quote_items,
            expected_start_date=expected_start_date,
            notes=notes
        )
        
        if not quote_result:
            logger.error("Failed to create quote in Salesforce")
            return None
        
        logger.info("Quote created successfully:")
        logger.info("    - Quote ID: %s", quote_result.get("quote_id"))
        logger.info("    - Quote Number: %s", quote_result.get("quote_number"))
        logger.info("    - Quote URL: %s", quote_result.get("quote_url"))
        
        # Send email notification
        if "@" in contact_info:
            try:
                logger.info("Sending quote email notification...")
                product_summary = ", ".join([
                    f"{item.get('product_package')} (x{item.get('quantity')})" 
                    for item in quote_items
                ])
                total_quantity = sum([int(item.get("quantity", 0)) for item in quote_items])
                email_sent = await send_quote_email(
                    to_email=contact_info,
                    customer_name=customer_name,
                    quote_url=quote_result["quote_url"],
                    product_package=product_summary,
                    quantity=str(total_quantity),
                    expected_start_date=expected_start_date,
                    notes=notes
                )
                if email_sent:
                    logger.info("Quote email sent successfully to %s", contact_info)
                else:
                    logger.warning("Quote email sending returned False for %s", contact_info)
            except Exception as e:
                logger.error("Error sending quote email: %s", str(e))
                import traceback
                logger.error("Traceback: %s", traceback.format_exc())
        else:
            logger.info("Contact info is not an email address, skipping email notification")
        
        logger.info("=" * 80)
        logger.info("QUOTE CREATION COMPLETED SUCCESSFULLY")
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
    Use Azure OpenAI (GPT-4o series) to generate phone welcome message text.
    
    Prefer using Azure OpenAI configured in .env:
    - AZURE_OPENAI_ENDPOINT
    - AZURE_OPENAI_DEPLOYMENT (or other compatible deployment)
    
    If environment variables are not configured or call fails, fall back to fixed text.
    """
    default_text = "Hello, thanks for calling. Please hold for a moment."

    try:
        # Lazy import to avoid crash if openai package is not installed
        from azure.core.credentials import AzureKeyCredential
        from azure.identity import DefaultAzureCredential
        from openai import AzureOpenAI
    except Exception as e:
        logger.warning("Azure OpenAI SDK not available, using default welcome text. Error: %s", str(e))
        return default_text

    openai_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    # Prefer dedicated conversation deployment, then general deployment
    openai_deployment = (
        os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        or os.environ.get("AZURE_OPENAI_EXTRACTION_DEPLOYMENT")
        or "gpt-4o"
    )
    llm_key = os.environ.get("AZURE_OPENAI_API_KEY")

    # Immediately output model information being used
    logger.info("GPT Model Configuration (Welcome) - Deployment: %s, Endpoint: %s", openai_deployment, openai_endpoint or "NOT SET")

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

        logger.info("Using GPT model: %s (endpoint: %s)", openai_deployment, openai_endpoint)
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
    Play welcome voice message (using ACS Call Automation TTS)
    
    This is the Azure officially recommended approach:
    - No audio files needed
    - No file hosting needed
    - 100% PSTN compatible
    - Officially long-term supported
    
    Args:
        call_connection_id: Call connection ID
    """
    acs_client = get_acs_client()
    
    if not acs_client:
        logger.error("ACS client not available, cannot play welcome message")
        return
    
    try:
        # Get CallConnectionClient from CallAutomationClient
        call_connection = acs_client.get_call_connection(call_connection_id)
        
        # Minimal viable TTS test: use fixed short English welcome message first, exclude GPT text / character set factors
        # If this step passes, switch back to GPT-generated text
        welcome_text = "Hi, I'm your voice assistant how can I help you today?"
        
        logger.info("Playing welcome message using TTS...")
        logger.info("   Text: %s", welcome_text)
        logger.info("   Connection ID: %s", call_connection_id)
        
        # Use TextSource to directly play text (officially recommended approach)
        # Depending on SDK version, TextSource may be in different locations
        text_source = None
        
        # Method 1: Try importing from main module (most common)
        try:
            from azure.communication.callautomation import TextSource
            text_source = TextSource(
                text=welcome_text,
                voice_name="en-US-JennyNeural",
                source_locale="en-US",
            )
            logger.info("   Using TextSource from main module")
        except ImportError:
            # Method 2: Try importing from models (some SDK versions may have it here)
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
                logger.error("TextSource not found in SDK")
                logger.error("   Please ensure azure-communication-callautomation is installed")
                logger.error("   Run: pip install azure-communication-callautomation")
                return
        
        # Execute playback
        # Key: play_source is passed as first positional argument, not keyword argument
        # Add operation_context for tracking playback completion events
        play_result = call_connection.play_media(
            text_source,  # Positional argument, not play_source=...
            operation_context="welcome-tts"
        )
        
        logger.info("Welcome message playback initiated")
        logger.info("   Voice: en-US-JennyNeural")
        if hasattr(play_result, 'operation_id'):
            logger.info("   Operation ID: %s", play_result.operation_id)
        
        # Update call state
        if call_connection_id in _active_acs_calls:
            _active_acs_calls[call_connection_id]["welcome_playing"] = True
            _active_acs_calls[call_connection_id]["welcome_text"] = welcome_text
            
    except ImportError as import_error:
        logger.error("Failed to import TextSource: %s", str(import_error))
        logger.error("   Please ensure azure-communication-callautomation is installed")
        logger.error("   Run: pip install azure-communication-callautomation")
    except Exception as e:
        logger.error("Error in play_welcome_message: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())


async def start_speech_recognition(call_connection_id: str) -> None:
    """
    Start one speech recognition session (let ACS + Speech listen to user), results come through
    Microsoft.Communication.RecognizeCompleted event callback.

    Use ACS Call Automation recommended signature:
    start_recognizing_media(RecognizeInputType.SPEECH, target_participant, ...)
    """
    acs_client = get_acs_client()
    if not acs_client:
        logger.error("ACS client not available, cannot start speech recognition")
        return

    try:
        if RecognizeInputType is None or PhoneNumberIdentifier is None:
            logger.error("SDK missing RecognizeInputType/PhoneNumberIdentifier, cannot start recognition")
            await speak_error_message(call_connection_id, debug_tag="start-recognize-sdk-missing")
            return

        call_connection = acs_client.get_call_connection(call_connection_id)
        call_info = _active_acs_calls.get(call_connection_id, {})
        
        # Prefer using saved actual phone number
        caller_phone = call_info.get("caller_phone")
        
        # Fallback: if only rawId (like "4:+613..."), strip "4:" prefix
        if not caller_phone:
            caller_raw_id = call_info.get("caller_raw_id", "")
            if isinstance(caller_raw_id, str) and caller_raw_id.startswith("4:"):
                caller_phone = caller_raw_id[2:]  # Remove "4:" prefix to get "+613..."
                logger.warning("Using caller_phone extracted from rawId (stripped '4:'): %s", caller_phone)
            else:
                logger.error("Missing caller phone for call %s (caller_phone=%s, caller_raw_id=%s)", 
                           call_connection_id, caller_phone, caller_raw_id)
                await speak_error_message(call_connection_id, debug_tag="start-recognize-missing-caller")
                return

        # Use actual phone number to construct PhoneNumberIdentifier (cannot use rawId)
        caller_identifier = PhoneNumberIdentifier(caller_phone)  # type: ignore[call-arg]
        logger.info("Starting speech recognition for call %s, caller_phone=%s", call_connection_id, caller_phone)

        call_connection.start_recognizing_media(
            RecognizeInputType.SPEECH,  # type: ignore[name-defined]
            caller_identifier,
            speech_language="en-US",  # Changed to en-US to match TTS configuration
            initial_silence_timeout=10,  # Seconds to wait for user to speak
            end_silence_timeout=2,  # How long silence before considering sentence ended
            operation_context="user-speech",
        )
        logger.info("Speech recognition started (waiting for RecognizeCompleted event)")

    except Exception as e:
        logger.error("Error in start_speech_recognition: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())
        await speak_error_message(call_connection_id, debug_tag="start-recognize-exception")


async def play_answer_message(call_connection_id: str, answer_text: str) -> None:
    """
    Play GPT-generated answer text (the "speak back" step in phone Q&A)
    """
    acs_client = get_acs_client()
    if not acs_client:
        logger.error("ACS client not available, cannot play answer message")
        return

    try:
        call_connection = acs_client.get_call_connection(call_connection_id)

        logger.info("Playing answer message using TTS...")
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
                logger.error("TextSource not found in SDK (answer)")
                logger.error("   Please ensure azure-communication-callautomation is installed")
                return

        play_result = call_connection.play_media(
            text_source,
            operation_context="answer-tts",
        )

        logger.info("Answer message playback initiated")
        if hasattr(play_result, "operation_id"):
            logger.info("   Answer Operation ID: %s", play_result.operation_id)

        if call_connection_id in _active_acs_calls:
            _active_acs_calls[call_connection_id]["last_answer"] = answer_text

    except Exception as e:
        logger.error("Error in play_answer_message: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())


async def speak_error_message(call_connection_id: Optional[str], debug_tag: str = "") -> None:
    """
    Simply announce "system error, for debugging" message on the phone to help you detect error points.
    - To avoid recursive errors, make an independent TTS call here, only log on failure and do not retry.
    """
    if not call_connection_id:
        return

    acs_client = get_acs_client()
    if not acs_client:
        logger.error("ACS client not available, cannot speak_error_message (tag=%s)", debug_tag)
        return

    try:
        call_connection = acs_client.get_call_connection(call_connection_id)
        error_text = "Sorry, there was an internal error while handling your request. This call is for debugging."

        logger.info("Speaking error message (tag=%s) on call %s", debug_tag, call_connection_id)

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
                logger.error("TextSource not available when trying to speak error (tag=%s)", debug_tag)
                return

        try:
            call_connection.play_media(
                text_source,
                operation_context=f"error-tts-{debug_tag or 'generic'}",
            )
            logger.info("Error message playback started (tag=%s)", debug_tag)
        except Exception as play_err:
            logger.error("Failed to play error message (tag=%s): %s", debug_tag, str(play_err))
            import traceback
            logger.error("Traceback: %s", traceback.format_exc())

    except Exception as e:
        logger.error("speak_error_message failed (tag=%s): %s", debug_tag, str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())


async def handle_acs_webhook(request: web.Request) -> web.Response:
    """
    Handle ACS Call Automation webhook events
    
    This is the main webhook endpoint, ACS will send all events here.
    Note: ACS/Event Grid may POST one event or an event array, process them one by one here.
    """
    try:
        # Parse event data
        raw_data = await request.json()
        
        # Convert to event list uniformly for processing one by one
        if isinstance(raw_data, list):
            events = raw_data
            if not events:
                logger.warning("Received empty event array")
                return web.json_response({"status": "received", "message": "Empty event array"}, status=200)
            logger.info("Received ACS Event Array with %d event(s)", len(events))
        else:
            events = [raw_data]
        
        for event_data in events:
            # Log received event
            # Event Grid uses eventType, ACS Call Automation uses type or kind
            event_type = event_data.get("eventType") or event_data.get("type") or event_data.get("kind") or "Unknown"
            logger.info("=" * 60)
            logger.info("Received ACS Event: %s", event_type)
            logger.info("Event data: %s", json.dumps(event_data, indent=2, ensure_ascii=False))
            logger.info("=" * 60)
            
            # Handle Event Grid subscription validation event (important!)
            if event_type == "Microsoft.EventGrid.SubscriptionValidationEvent":
                # Event Grid validation event data structure
                event_data_obj = event_data.get("data", {})
                validation_code = event_data_obj.get("validationCode")
                
                if validation_code:
                    logger.info("Event Grid subscription validation received")
                    logger.info("   Validation Code: %s", validation_code)
                    # Return validation code to complete subscription validation
                    # Event Grid expects response format: {"validationResponse": "code"}
                    response_data = {
                        "validationResponse": validation_code
                    }
                    logger.info("   Sending validation response: %s", response_data)
                    # Validation events are sent alone, can return directly here
                    return web.json_response(response_data, status=200)
                else:
                    logger.warning("Validation event received but no validationCode found")
                    logger.warning("   Event data structure: %s", json.dumps(event_data, indent=2))
                    continue
            
            # Handle incoming call event
            if event_type == "Microsoft.Communication.IncomingCall":
                await handle_incoming_call_event(event_data)
            
            # Handle call connected event
            elif event_type == "Microsoft.Communication.CallConnected":
                await handle_call_connected_event(event_data)
            
            # Handle call disconnected event
            elif event_type == "Microsoft.Communication.CallDisconnected":
                await handle_call_disconnected_event(event_data)
            
            # Handle play completed event
            elif event_type == "Microsoft.Communication.PlayCompleted":
                await handle_play_completed_event(event_data)
            
            # Handle play failed event
            elif event_type == "Microsoft.Communication.PlayFailed":
                await handle_play_failed_event(event_data)
            
            # Handle speech recognition completed event (phone Q&A entry point)
            elif event_type == "Microsoft.Communication.RecognizeCompleted":
                await handle_recognize_completed(event_data)

            # Handle speech recognition failed event
            elif event_type == "Microsoft.Communication.RecognizeFailed":
                await handle_recognize_failed_event(event_data)
            
            # Other event types
            else:
                logger.info("Unhandled event type: %s", event_type)
        
        # Return 200 after all events are processed
        return web.json_response({"status": "received"}, status=200)
        
    except json.JSONDecodeError as e:
        logger.error("Failed to parse JSON: %s", str(e))
        return web.json_response({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.error("Error processing webhook: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())
        return web.json_response({"error": str(e)}, status=500)


async def handle_acs_ping(request: web.Request) -> web.Response:
    """Test route - verify ACS routes are registered"""
    return web.json_response({
        "status": "ok",
        "message": "ACS routes are registered",
        "timestamp": time.time()
    })


async def handle_get_active_calls(request: web.Request) -> web.Response:
    """Get current active ACS call list"""
    return web.json_response({
        "active_calls": list(_active_acs_calls.values()),
        "count": len(_active_acs_calls)
    })


async def handle_get_call_status(request: web.Request) -> web.Response:
    """Get status of specific call"""
    call_connection_id = request.match_info.get("call_connection_id")
    
    if not call_connection_id:
        return web.json_response({"error": "Missing call_connection_id"}, status=400)
    
    if call_connection_id in _active_acs_calls:
        return web.json_response(_active_acs_calls[call_connection_id])
    else:
        return web.json_response({"error": "Call not found"}, status=404)


async def handle_hangup_call(request: web.Request) -> web.Response:
    """Hang up specified call"""
    call_connection_id = request.match_info.get("call_connection_id")
    
    if not call_connection_id:
        return web.json_response({"error": "Missing call_connection_id"}, status=400)
    
    acs_client = get_acs_client()
    if not acs_client:
        return web.json_response({"error": "ACS client not configured"}, status=503)
    
    try:
        # Get CallConnectionClient
        call_connection_client = acs_client.get_call_connection(call_connection_id)
        
        # Hang up call
        call_connection_client.hang_up(is_for_everyone=True)
        
        # Clean up call record
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
    Register ACS-related routes to aiohttp application
    
    Usage example:
        from acs_call_handler import register_acs_routes
        register_acs_routes(app)
    """
    # Very prominent log to verify if it's being called
    logger.error("### ACS ROUTES REGISTER() CALLED ###")
    logger.info("Registering ACS call handler routes...")
    
    # Load environment variables
    if not os.environ.get("RUNNING_IN_PRODUCTION"):
        load_dotenv()
    
    # Initialize ACS client (if configured)
    get_acs_client()
    
    # Register routes
    try:
        app.router.add_get("/api/acs/ping", handle_acs_ping)  # Test route to verify route registration
        logger.info("Registered: GET /api/acs/ping")
    except Exception as e:
        logger.error("Failed to register GET /api/acs/ping: %s", str(e))
    
    try:
        app.router.add_post("/api/acs/calls/events", handle_acs_webhook)
        logger.info("Registered: POST /api/acs/calls/events")
    except Exception as e:
        logger.error("Failed to register POST /api/acs/calls/events: %s", str(e))
    
    try:
        app.router.add_get("/api/acs/calls", handle_get_active_calls)
        logger.info("Registered: GET /api/acs/calls")
    except Exception as e:
        logger.error("Failed to register GET /api/acs/calls: %s", str(e))
    
    try:
        app.router.add_get("/api/acs/calls/{call_connection_id}", handle_get_call_status)
        logger.info("Registered: GET /api/acs/calls/{call_connection_id}")
    except Exception as e:
        logger.error("Failed to register GET /api/acs/calls/{call_connection_id}: %s", str(e))
    
    try:
        app.router.add_delete("/api/acs/calls/{call_connection_id}", handle_hangup_call)
        logger.info("Registered: DELETE /api/acs/calls/{call_connection_id}")
    except Exception as e:
        logger.error("Failed to register DELETE /api/acs/calls/{call_connection_id}: %s", str(e))
    
    # Verify routes are actually added
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


# Test function
async def test_acs_connection() -> bool:
    """Test if ACS connection is normal"""
    acs_client = get_acs_client()
    if not acs_client:
        logger.error("ACS client not available")
        return False
    
    logger.info("ACS client is available and ready")
    return True


if __name__ == "__main__":
    # Standalone test mode
    import asyncio
    
    async def main():
        # Load environment variables
        load_dotenv()
        
        # Test connection
        logger.info("Testing ACS connection...")
        success = await test_acs_connection()
        
        if success:
            logger.info("ACS connection test passed")
        else:
            logger.error("ACS connection test failed")
            logger.info("Please check your ACS_CONNECTION_STRING environment variable")
    
    asyncio.run(main())
