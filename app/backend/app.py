import json
import logging
import os
from pathlib import Path
from uuid import uuid4

import aiohttp
from aiohttp import web
from azure.core.credentials import AzureKeyCredential
from azure.identity import AzureDeveloperCliCredential, DefaultAzureCredential
from dotenv import load_dotenv

from intent_router import build_session_state, classify_intent_fallback, classify_intent_with_llm
from ragtools import attach_rag_tools
from rtmt import RTMiddleTier
from quote_tools import attach_quote_extraction_tool, attach_quote_management_tools, attach_user_registration_tool
from quote_workflow import create_quote_from_extracted, fetch_available_products
from teams_calling import TeamsCaller

from openai import AzureOpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voicerag")

# 延迟导入 ACS handler，避免导入失败导致应用无法启动
try:
    from acs_call_handler import handle_realtime_acs_phone_turn, register_acs_routes
    _acs_handler_available = True
except ImportError as e:
    logger.warning("ACS call handler not available: %s", str(e))
    _acs_handler_available = False
    handle_realtime_acs_phone_turn = None
    register_acs_routes = None

# Store active calls: call_id -> call_info
_active_calls: dict[str, dict] = {}

async def create_app():
    if not os.environ.get("RUNNING_IN_PRODUCTION"):
        logger.info("Running in development mode, loading from .env file")
        load_dotenv()

    # ── Voice entry mode ──────────────────────────────────────────────────────
    # Controls which voice entry point is active: 'web' (browser) or 'acs' (phone).
    # Only one mode can be active at a time. Both cannot run simultaneously as they
    # would compete for the same GPT Realtime voice pipeline.
    _voice_entry_mode = os.environ.get("VOICE_ENTRY_MODE", "web").strip().lower()
    if _voice_entry_mode not in {"web", "acs"}:
        raise RuntimeError(
            f"VOICE_ENTRY_MODE={_voice_entry_mode!r} is invalid. "
            "Accepted values: 'web' or 'acs'. "
            "Fix the configuration and restart the server."
        )
    logger.info("Voice entry mode: %s", _voice_entry_mode)
    # ─────────────────────────────────────────────────────────────────────────

    llm_key = os.environ.get("AZURE_OPENAI_API_KEY")
    search_key = os.environ.get("AZURE_SEARCH_API_KEY")

    credential = None
    if not llm_key or not search_key:
        if tenant_id := os.environ.get("AZURE_TENANT_ID"):
            logger.info("Using AzureDeveloperCliCredential with tenant_id %s", tenant_id)
            credential = AzureDeveloperCliCredential(tenant_id=tenant_id, process_timeout=60)
        else:
            logger.info("Using DefaultAzureCredential")
            credential = DefaultAzureCredential()
    llm_credential = AzureKeyCredential(llm_key) if llm_key else credential
    search_credential = AzureKeyCredential(search_key) if search_key else credential
    
    app = web.Application()
    
    # Initialize RTMiddleTier only if Azure OpenAI configuration is available
    openai_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    openai_deployment = os.environ.get("AZURE_OPENAI_REALTIME_DEPLOYMENT")
    
    rtmt = None

    if openai_endpoint and openai_deployment:
        rtmt = RTMiddleTier(
            credentials=llm_credential,
            endpoint=openai_endpoint,
            deployment=openai_deployment,
            voice_choice=os.environ.get("AZURE_OPENAI_REALTIME_VOICE_CHOICE") or "alloy",
            )
        rtmt.system_message = """
            You are a concise English-only voice assistant for a web app.
            A backend intent router classifies every user turn before you respond.
            Follow the route-specific instructions supplied in each response.create event.

            Global rules:
            - Keep spoken replies short and useful.
            - Never mention internal intent names, tool names, JSON, file names, source keys, or implementation details.
            - Use tools only when the route-specific instruction says to use them.
            - Registration is required before quote or knowledge-base tasks.
            - The initial quote send is handled by the web confirmation dialog; do not call send_quote_email for the first send.
        """.strip()

        def _classify_web_intent(rtmt_instance: RTMiddleTier, session_id: str | None, transcript: str) -> dict:
            session_state = build_session_state(rtmt_instance, session_id)
            llm_eval_deployment = (
                os.environ.get("AZURE_OPENAI_EXTRACTION_DEPLOYMENT")
                or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
                or "gpt-4o-mini"
            )

            if not openai_endpoint:
                return classify_intent_fallback(transcript, session_state)

            try:
                if llm_key:
                    classifier_client = AzureOpenAI(
                        api_key=llm_key,
                        api_version="2024-02-15-preview",
                        azure_endpoint=openai_endpoint,
                    )
                else:
                    token_credential = credential or DefaultAzureCredential()
                    token = token_credential.get_token("https://cognitiveservices.azure.com/.default").token
                    classifier_client = AzureOpenAI(
                        api_key=token,
                        api_version="2024-02-15-preview",
                        azure_endpoint=openai_endpoint,
                    )
                return classify_intent_with_llm(
                    classifier_client,
                    llm_eval_deployment,
                    transcript,
                    session_state,
                )
            except Exception as exc:
                logger.exception("LLM intent classifier failed; using fallback: %s", str(exc))
                return classify_intent_fallback(transcript, session_state)

        rtmt.intent_classifier = _classify_web_intent

        attach_rag_tools(rtmt,
            credentials=search_credential,
            search_endpoint=os.environ.get("AZURE_SEARCH_ENDPOINT"),
            search_index=os.environ.get("AZURE_SEARCH_INDEX"),
            semantic_configuration=os.environ.get("AZURE_SEARCH_SEMANTIC_CONFIGURATION") or None,
            identifier_field=os.environ.get("AZURE_SEARCH_IDENTIFIER_FIELD") or "chunk_id",
            content_field=os.environ.get("AZURE_SEARCH_CONTENT_FIELD") or "chunk",
            embedding_field=os.environ.get("AZURE_SEARCH_EMBEDDING_FIELD") or "text_vector",
            title_field=os.environ.get("AZURE_SEARCH_TITLE_FIELD") or "title",
            use_vector_query=(os.getenv("AZURE_SEARCH_USE_VECTOR_QUERY", "true") == "true")
            )
        
        # Attach quote extraction tool
        attach_quote_extraction_tool(rtmt)
        # Attach user registration tool
        attach_user_registration_tool(rtmt)
        attach_quote_management_tools(rtmt)
        logger.info("Quote extraction tool and user registration tool attached. Available tools: %s", list(rtmt.tools.keys()))

        if _voice_entry_mode == "acs" and handle_realtime_acs_phone_turn is not None:
            rtmt.acs_phone_turn_handler = handle_realtime_acs_phone_turn
            logger.info(
                "ACS Realtime phone chain configured to use ACS phone business logic "
                "(receptionist/routing/quote flow) instead of the Web VoiceRAG intent flow"
            )

        rtmt.attach_to_app(app, "/realtime", acs_path="/realtime/acs")
        logger.info("RTMiddleTier initialized with Azure OpenAI")
    else:
        logger.warning("Azure OpenAI configuration not found. Voice features will be disabled. Quote API is still available.")

    def _sync_realtime_session_state(
        session_id: str | None,
        user_data: dict | None = None,
        quote_data: dict | None = None,
        quote_delivery: dict | None = None,
    ) -> None:
        if not rtmt or not session_id:
            return

        conversation = rtmt._conversation_logs.setdefault(
            session_id,
            {"session_id": session_id, "start_time": "", "messages": []},
        )
        messages = conversation.setdefault("messages", [])

        if user_data:
            extracted = {
                "customer_name": user_data.get("customer_name"),
                "contact_info": user_data.get("contact_info"),
            }
            rtmt._user_states[session_id] = {
                "extracted": extracted,
                "is_complete": bool(extracted.get("customer_name") and extracted.get("contact_info")),
                "is_confirmed": True,
            }
            messages.append({
                "role": "user",
                "content": (
                    f"My confirmed name is {extracted.get('customer_name')}. "
                    f"My confirmed email is {extracted.get('contact_info')}."
                ),
            })

        if quote_data:
            extracted = {
                "customer_name": quote_data.get("customer_name"),
                "contact_info": quote_data.get("contact_info"),
                "quote_items": quote_data.get("quote_items", []),
                "expected_start_date": quote_data.get("expected_start_date"),
                "notes": quote_data.get("notes"),
            }
            valid_items = [
                item for item in extracted["quote_items"]
                if isinstance(item, dict) and item.get("product_package") and item.get("quantity")
            ]
            missing_fields = []
            if not extracted.get("customer_name"):
                missing_fields.append("customer_name")
            if not extracted.get("contact_info"):
                missing_fields.append("contact_info")
            if not valid_items:
                missing_fields.append("quote_items")
            rtmt._quote_states[session_id] = {
                "extracted": extracted,
                "missing_fields": missing_fields,
                "products_available": rtmt._quote_states.get(session_id, {}).get("products_available", []),
                "is_complete": len(missing_fields) == 0,
                "delivery": quote_delivery or rtmt._quote_states.get(session_id, {}).get("delivery", {}),
            }
            product_summary = ", ".join(
                [f"{item.get('product_package')} x{item.get('quantity')}" for item in extracted["quote_items"] if isinstance(item, dict)]
            )
            messages.append({
                "role": "user",
                "content": (
                    f"My confirmed quote details are: name {extracted.get('customer_name')}, "
                    f"email {extracted.get('contact_info')}, products {product_summary or 'none'}, "
                    f"expected start date {extracted.get('expected_start_date') or 'not provided'}, "
                    f"notes {extracted.get('notes') or 'none'}."
                ),
            })

    def _classify_utterance_state(transcript: str, pending_action: str | None = None) -> dict:
        """Use LLM to classify user utterance into state used for UI branching."""
        normalized_pending_action = pending_action if pending_action in {"quote", "user_registration"} else "none"

        if not transcript.strip():
            return {"state": "other", "pending_action": normalized_pending_action}

        llm_eval_deployment = (
            os.environ.get("AZURE_OPENAI_EXTRACTION_DEPLOYMENT")
            or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
            or "gpt-4o-mini"
        )

        if not openai_endpoint:
            logger.warning("Skipping utterance classification because AZURE_OPENAI_ENDPOINT is not configured")
            return {"state": "other", "pending_action": normalized_pending_action}

        if llm_key:
            client = AzureOpenAI(
                api_key=llm_key,
                api_version="2024-02-15-preview",
                azure_endpoint=openai_endpoint,
            )
        else:
            token_credential = credential or DefaultAzureCredential()
            token = token_credential.get_token("https://cognitiveservices.azure.com/.default").token
            client = AzureOpenAI(
                api_key=token,
                api_version="2024-02-15-preview",
                azure_endpoint=openai_endpoint,
            )

        response = client.chat.completions.create(
            model=llm_eval_deployment,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an intent-state classifier for voice interactions. "
                        "Classify the user utterance to one state only: confirm, cancel, or other. "
                        "Use semantic intent rather than keywords. "
                        "Only return cancel when the user truly wants to stop/reject current pending action."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Pending action: {normalized_pending_action}\n"
                        f"Utterance: {transcript}\n\n"
                        "Return JSON only with this shape: "
                        '{"state":"confirm|cancel|other","pending_action":"quote|user_registration|none"}'
                    ),
                },
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )

        parsed = json.loads(response.choices[0].message.content)
        state = parsed.get("state")
        if state not in {"confirm", "cancel", "other"}:
            state = "other"

        return {
            "state": state,
            "pending_action": normalized_pending_action,
        }

    async def handle_utterance_state(request: web.Request) -> web.Response:
        """Classify user utterance into behavior state for front-end branching."""
        try:
            payload = await request.json()
            transcript = str(payload.get("transcript") or "")
            pending_action = payload.get("pending_action")
            result = _classify_utterance_state(transcript, pending_action)
            return web.json_response(result)
        except Exception as exc:
            logger.exception("Failed to classify utterance state: %s", str(exc))
            return web.json_response(
                {"state": "other", "pending_action": "none", "error": "classification_failed"},
                status=200,
            )

    async def handle_quote_request(request: web.Request) -> web.Response:
        """Handle quote request - creates quote in Salesforce and sends email."""
        payload = await request.json()
        
        # Support both old format (product_package + quantity) and new format (quote_items)
        quote_items = payload.get("quote_items", [])
        if not quote_items:
            # Legacy format: convert product_package + quantity to quote_items
            if payload.get("product_package") and payload.get("quantity"):
                quote_items = [{
                    "product_package": payload["product_package"],
                    "quantity": int(payload["quantity"])
                }]
        
        required_fields = [
            "customer_name",
            "contact_info"
        ]
        missing_fields = [field for field in required_fields if not payload.get(field)]
        
        # Validate quote_items
        if not quote_items or not isinstance(quote_items, list) or len(quote_items) == 0:
            missing_fields.append("quote_items")
        
        # Validate each quote item
        for item in quote_items:
            if not isinstance(item, dict) or not item.get("product_package") or not item.get("quantity"):
                if "quote_items" not in missing_fields:
                    missing_fields.append("quote_items")
                break
        
        if missing_fields:
            return web.json_response(
                {"error": f"Missing required fields: {', '.join(missing_fields)}"},
                status=400
            )

        customer_name = payload["customer_name"]
        contact_info = payload["contact_info"]
        expected_start_date = payload.get("expected_start_date")
        notes = payload.get("notes")

        quote_result = await create_quote_from_extracted(
            {
                "customer_name": customer_name,
                "contact_info": contact_info,
                "quote_items": quote_items,
                "expected_start_date": expected_start_date,
                "notes": notes,
            },
            fallback_to_mock=True,
        )
        if not quote_result:
            return web.json_response({"error": "Failed to create quote"}, status=500)
        
        response_data = {
            "quote_id": quote_result.get("quote_id"),
            "quote_number": quote_result.get("quote_number"),
            "quote_url": quote_result["quote_url"],
            "email_sent": quote_result.get("email_sent", False),
            "email_error": quote_result.get("email_error"),
        }
        
        return web.json_response(response_data)

    async def handle_get_products(request: web.Request) -> web.Response:
        """Get list of products from Salesforce."""
        return web.json_response({"products": fetch_available_products()})
    
    async def handle_register_user(request: web.Request) -> web.Response:
        """Register user to Salesforce - creates or gets Account and Contact."""
        payload = await request.json()
        customer_name = payload.get("customer_name")
        contact_info = payload.get("contact_info")
        session_id = payload.get("session_id")
        
        if not customer_name or not contact_info:
            return web.json_response(
                {"error": "customer_name and contact_info are required"},
                status=400
            )
        
        from salesforce_service import get_salesforce_service
        sf_service = get_salesforce_service()
        
        if not sf_service.is_available():
            return web.json_response(
                {"error": "Salesforce is not available"},
                status=503
            )
        
        try:
            # Create or get Account
            account_id = sf_service.create_or_get_account(customer_name, contact_info)
            if not account_id:
                return web.json_response(
                    {"error": "Failed to create/get Account"},
                    status=500
                )
            
            # Create or get Contact
            contact_id = sf_service.create_or_get_contact(account_id, customer_name, contact_info)
            
            result = {
                "success": True,
                "account_id": account_id,
                "contact_id": contact_id,
                "message": "User registered successfully"
            }
            
            logger.info("User registered to Salesforce: %s (Account: %s, Contact: %s)", 
                       customer_name, account_id, contact_id)
            _sync_realtime_session_state(
                session_id,
                user_data={
                    "customer_name": customer_name,
                    "contact_info": contact_info,
                },
            )
            
            return web.json_response(result)
            
        except Exception as e:
            logger.error("Error registering user to Salesforce: %s", str(e))
            import traceback
            logger.error("Traceback: %s", traceback.format_exc())
            return web.json_response(
                {"error": f"Failed to register user: {str(e)}"},
                status=500
            )
    
    async def handle_confirm_quote(request: web.Request) -> web.Response:
        """Handle quote confirmation - creates quote and sends email."""
        payload = await request.json()
        quote_data = payload.get("quote_data", {})
        session_id = payload.get("session_id")
        
        if not quote_data:
            return web.json_response(
                {"error": "Quote data is required"},
                status=400
            )
        
        # Use the existing handle_quote_request logic
        # Create a mock request with the quote data
        customer_name = quote_data.get("customer_name", "")
        contact_info = quote_data.get("contact_info", "")
        
        # Support both old format and new format
        quote_items = quote_data.get("quote_items", [])
        if not quote_items:
            # Legacy format: convert product_package + quantity to quote_items
            if quote_data.get("product_package") and quote_data.get("quantity"):
                quote_items = [{
                    "product_package": quote_data["product_package"],
                    "quantity": int(quote_data["quantity"])
                }]
        
        expected_start_date = quote_data.get("expected_start_date")
        notes = quote_data.get("notes")
        
        # Validate required fields
        if not customer_name or not contact_info:
            return web.json_response(
                {"error": "Missing required quote information: customer_name and contact_info"},
                status=400
            )
        
        if not quote_items or not isinstance(quote_items, list) or len(quote_items) == 0:
            return web.json_response(
                {"error": "Missing required quote information: quote_items"},
                status=400
            )
        
        # Validate each quote item
        for item in quote_items:
            if not isinstance(item, dict) or not item.get("product_package") or not item.get("quantity"):
                return web.json_response(
                    {"error": "Invalid quote_items: each item must have product_package and quantity"},
                    status=400
                )
        
        quote_result = await create_quote_from_extracted(
            {
                "customer_name": customer_name,
                "contact_info": contact_info,
                "quote_items": quote_items,
                "expected_start_date": expected_start_date,
                "notes": notes,
            },
            fallback_to_mock=True,
        )
        if not quote_result:
            return web.json_response({"error": "Failed to create quote"}, status=500)
        
        _sync_realtime_session_state(
            session_id,
            quote_data=quote_data,
            quote_delivery={
                "quote_id": quote_result.get("quote_id"),
                "quote_number": quote_result.get("quote_number"),
                "quote_url": quote_result.get("quote_url"),
                "email_sent": quote_result.get("email_sent", False),
                "email_error": quote_result.get("email_error"),
            },
        )

        return web.json_response({
            "success": True,
            "quote_id": quote_result.get("quote_id"),
            "quote_number": quote_result.get("quote_number"),
            "quote_url": quote_result["quote_url"],
            "email_sent": quote_result.get("email_sent", False),
            "email_error": quote_result.get("email_error"),
        })

    # Initialize Teams Caller if configuration is available
    teams_caller = None
    tenant_id = os.environ.get("TEAMS_TENANT_ID") or os.environ.get("AZURE_TENANT_ID")
    teams_client_id = os.environ.get("TEAMS_CLIENT_ID")
    teams_client_secret = os.environ.get("TEAMS_CLIENT_SECRET")
    teams_bot_app_id = os.environ.get("TEAMS_BOT_APP_ID")
    teams_bot_display_name = os.environ.get("TEAMS_BOT_DISPLAY_NAME", "VoiceRAG Bot")

    if tenant_id and teams_client_id and teams_client_secret:
        try:
            teams_caller = TeamsCaller(
                tenant_id=tenant_id,
                client_id=teams_client_id,
                client_secret=teams_client_secret,
                bot_app_id=teams_bot_app_id,
                bot_display_name=teams_bot_display_name
            )
            logger.info("Teams Caller initialized successfully")
        except Exception as e:
            logger.warning("Failed to initialize Teams Caller: %s", str(e))
    else:
        logger.info("Teams configuration not found. Teams calling features will be disabled.")

    async def handle_teams_call(request: web.Request) -> web.Response:
        """Handle request to make a Teams call"""
        if not teams_caller:
            return web.json_response(
                {"error": "Teams calling is not configured"},
                status=503
            )

        payload = await request.json()
        call_type = payload.get("type")  # "phone" or "teams_user"
        target = payload.get("target")  # phone number or user UPN/objectId
        callback_uri = payload.get("callback_uri")

        if not call_type or not target:
            return web.json_response(
                {"error": "Missing required fields: type and target"},
                status=400
            )

        try:
            async with aiohttp.ClientSession() as session:
                if call_type == "phone":
                    result = await teams_caller.make_call(target, callback_uri, session)
                elif call_type == "teams_user":
                    result = await teams_caller.make_call_to_teams_user(target, callback_uri, session)
                else:
                    return web.json_response(
                        {"error": f"Invalid call type: {call_type}. Must be 'phone' or 'teams_user'"},
                        status=400
                    )

                call_id = result.get("id")
                if call_id:
                    _active_calls[call_id] = {
                        "call_id": call_id,
                        "call_type": call_type,
                        "target": target,
                        "state": result.get("state", "unknown"),
                        "created_at": result.get("createdDateTime")
                    }
                    logger.info("Call created: call_id=%s, type=%s, target=%s", call_id, call_type, target)

                return web.json_response(result)
        except Exception as e:
            logger.error("Error making Teams call: %s", str(e))
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def handle_teams_call_status(request: web.Request) -> web.Response:
        """Handle request to get Teams call status"""
        if not teams_caller:
            return web.json_response(
                {"error": "Teams calling is not configured"},
                status=503
            )

        call_id = request.match_info.get("call_id")
        if not call_id:
            return web.json_response(
                {"error": "Missing call_id"},
                status=400
            )

        try:
            async with aiohttp.ClientSession() as session:
                status = await teams_caller.get_call_status(call_id, session)
                # Update active calls cache
                if call_id in _active_calls:
                    _active_calls[call_id]["state"] = status.get("state", "unknown")
                return web.json_response(status)
        except Exception as e:
            logger.error("Error getting call status: %s", str(e))
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def handle_teams_end_call(request: web.Request) -> web.Response:
        """Handle request to end a Teams call"""
        if not teams_caller:
            return web.json_response(
                {"error": "Teams calling is not configured"},
                status=503
            )

        call_id = request.match_info.get("call_id")
        if not call_id:
            return web.json_response(
                {"error": "Missing call_id"},
                status=400
            )

        try:
            async with aiohttp.ClientSession() as session:
                await teams_caller.end_call(call_id, session)
                # Remove from active calls
                if call_id in _active_calls:
                    del _active_calls[call_id]
                logger.info("Call ended: call_id=%s", call_id)
                return web.json_response({"success": True, "call_id": call_id})
        except Exception as e:
            logger.error("Error ending call: %s", str(e))
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def handle_teams_callbacks(request: web.Request) -> web.Response:
        """Handle Teams callbacks from Microsoft Graph API"""
        try:
            payload = await request.json()
            logger.info("Received Teams callback: %s", json.dumps(payload, indent=2))

            # Extract call ID from callback
            call_id = payload.get("resourceData", {}).get("id") or payload.get("id")
            if not call_id:
                # Try to find it in other fields
                call_id = payload.get("resource") or payload.get("callId")

            if call_id:
                # Update active call state
                if call_id in _active_calls:
                    state = payload.get("resourceData", {}).get("state") or payload.get("state")
                    if state:
                        _active_calls[call_id]["state"] = state
                        logger.info("Updated call state: call_id=%s, state=%s", call_id, state)

            # Microsoft Graph expects 202 Accepted for callbacks
            return web.json_response({"received": True}, status=202)
        except Exception as e:
            logger.error("Error processing Teams callback: %s", str(e))
            # Still return 202 to acknowledge receipt
            return web.json_response({"error": str(e)}, status=202)

    async def handle_get_active_calls(request: web.Request) -> web.Response:
        """Get list of active calls"""
        return web.json_response({
            "active_calls": list(_active_calls.values()),
            "count": len(_active_calls)
        })
    
    current_directory = Path(__file__).parent
    routes = [
        web.get('/', lambda _: web.FileResponse(current_directory / 'static/index.html')),
        web.get('/api/products', handle_get_products),
        web.post('/api/quotes', handle_quote_request),
        web.post('/api/quotes/confirm', handle_confirm_quote),
        web.post('/api/utterance-state', handle_utterance_state),
        web.post('/api/salesforce/register-user', handle_register_user),
    ]

    # Add Teams calling routes if configured
    if teams_caller:
        routes.extend([
            web.post('/api/teams/calls', handle_teams_call),
            web.get('/api/teams/calls', handle_get_active_calls),
            web.get('/api/teams/calls/{call_id}', handle_teams_call_status),
            web.delete('/api/teams/calls/{call_id}', handle_teams_end_call),
            web.post('/api/teams/callbacks', handle_teams_callbacks),  # Microsoft Graph callback endpoint
        ])
        logger.info("Teams calling API endpoints registered")

    app.add_routes(routes)
    
    # 注册 ACS Call Automation 路由（仅在 VOICE_ENTRY_MODE=acs 时启用）
    if _voice_entry_mode == "acs":
        if _acs_handler_available and register_acs_routes:
            try:
                logger.info("About to call register_acs_routes(app)...")
                register_acs_routes(app)
                logger.info("ACS call handler routes registered successfully")

                # 验证路由是否真的被添加
                acs_routes_found = []
                for route in app.router.routes():
                    route_str = str(route)
                    if '/api/acs' in route_str:
                        acs_routes_found.append(route_str)
                logger.info("Verified ACS routes in app.router: %s", acs_routes_found)
            except Exception as e:
                logger.error("Failed to register ACS routes: %s", str(e))
                import traceback
                logger.error("Traceback: %s", traceback.format_exc())
        else:
            logger.warning(
                "VOICE_ENTRY_MODE=acs but ACS handler is not available — "
                "phone calls will not be handled. "
                "_acs_handler_available=%s",
                _acs_handler_available,
            )
    else:
        logger.info(
            "VOICE_ENTRY_MODE=web: ACS call routes not registered "
            "(set VOICE_ENTRY_MODE=acs to enable phone call handling)."
        )
    
    # 静态文件路由放在最后（避免覆盖 API 路由）
    app.router.add_static('/', path=current_directory / 'static', name='static')
    
    return app

if __name__ == "__main__":
    host = "localhost"
    port = 8765
    logger.info("=" * 50)
    logger.info("Starting VoiceRAG server...")
    logger.info("Server URL: http://%s:%s", host, port)
    logger.info("Quote API: http://%s:%s/api/quotes", host, port)

    # Print voice entry mode before starting so it's visible in the console
    _startup_voice_mode = os.environ.get("VOICE_ENTRY_MODE", "web").strip().lower()
    logger.info("Voice entry mode: %s", _startup_voice_mode)
    if _startup_voice_mode == "acs":
        _legacy = os.environ.get("ACS_USE_LEGACY_RECOGNIZE", "false").strip().lower()
        _legacy_on = _legacy in {"1", "true", "yes", "on"}
        logger.info(
            "ACS legacy recognize fallback: %s",
            "enabled (legacy recognize+TTS)" if _legacy_on else "disabled (GPT Realtime bridge)",
        )
        logger.info("ACS events endpoint: http://%s:%s/api/acs/calls/events", host, port)

    # Check Teams configuration status
    if os.environ.get("TEAMS_CLIENT_ID") or (os.environ.get("TEAMS_TENANT_ID") and os.environ.get("TEAMS_CLIENT_SECRET")):
        logger.info("Teams Calling API: http://%s:%s/api/teams/calls", host, port)
        logger.info("Teams Callbacks: http://%s:%s/api/teams/callbacks", host, port)
    else:
        logger.info("Teams Calling: Not configured (set TEAMS_* env vars to enable)")

    logger.info("=" * 50)
    web.run_app(create_app(), host=host, port=port)
