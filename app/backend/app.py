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

from ragtools import attach_rag_tools
from rtmt import RTMiddleTier
from quote_tools import attach_quote_extraction_tool, attach_user_registration_tool
from teams_calling import TeamsCaller

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voicerag")

# 延迟导入 ACS handler，避免导入失败导致应用无法启动
try:
    from acs_call_handler import register_acs_routes
    _acs_handler_available = True
except ImportError as e:
    logger.warning("ACS call handler not available: %s", str(e))
    _acs_handler_available = False
    register_acs_routes = None

# Store active calls: call_id -> call_info
_active_calls: dict[str, dict] = {}

async def create_app():
    if not os.environ.get("RUNNING_IN_PRODUCTION"):
        logger.info("Running in development mode, loading from .env file")
        load_dotenv()

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
    
    if openai_endpoint and openai_deployment:
        rtmt = RTMiddleTier(
            credentials=llm_credential,
            endpoint=openai_endpoint,
            deployment=openai_deployment,
            voice_choice=os.environ.get("AZURE_OPENAI_REALTIME_VOICE_CHOICE") or "alloy"
            )
        rtmt.system_message = """
            You are a helpful assistant with access to a knowledge base through the 'search' tool, and you help users request quotes.
            IMPORTANT: Always respond in English only, keep replies short (ideally one sentence), and never read file names or keys aloud.
            
            FIRST INTERACTION - USER REGISTRATION (CRITICAL - ALWAYS DO THIS FIRST):
            - You MUST check conversation history to see if there are ANY previous user messages.
            - If there are NO previous user messages (this is the FIRST interaction), you MUST:
              1. Immediately introduce yourself: "Hi, I'm your voice assistant. To provide better service, could I have your name and email address?"
              2. Do NOT answer any questions the user might have asked.
              3. Do NOT use search tool or any other tools except extract_user_info.
              4. Do NOT proceed with any other tasks.
            - After the user provides their information, call 'extract_user_info' tool to extract their name and email.
            - If information is incomplete (is_complete = false), ask for the missing piece (name OR email, one at a time).
            - Keep calling 'extract_user_info' after each response until is_complete = true.
            - Once complete (is_complete = true), ALWAYS restate all collected registration details (name and email) before asking for confirmation.
            - Then tell the user: "I've collected your information. Please review and confirm in the dialog on your screen, or you can say 'confirm' or 'yes' to proceed."
            - This registration MUST happen BEFORE any other questions, searches, or quote requests. Do not help with other tasks until registration is complete.
            
            ROLE OF 'extract_user_info' (user registration):
            - Call this tool after the user provides their name and/or email.
            - Returns: {"extracted": {"customer_name": ..., "contact_info": ...}, "is_complete": bool}
            - If is_complete = false: Ask for the missing information (either name or email, whichever is missing).
            - If is_complete = true: The system will show a confirmation dialog. BEFORE asking for confirmation, repeat all collected registration details (name + email), then say: "I've collected your information. Please review and confirm in the dialog on your screen, or you can say 'confirm' or 'yes' to proceed."
            - If user asks what they previously provided (for example: "what name did I give?" or "what email did I enter?"), call 'extract_user_info' again and answer using the extracted values. Do NOT reply with "I don't know" if the values exist in conversation history.
            - IMPORTANT: Only proceed with other tasks (search, quote requests) AFTER user registration is complete (is_complete = true and user has confirmed).
            
            ROLE OF 'extract_quote_info' (state evaluator):
            - Called multiple times; may return empty/partial info.
            - Returns structured state only: {"extracted": {...}, "missing_fields": [...], "is_complete": bool, "products_available": [...]}.
            - It never asks the user anything; you decide what to ask based on missing_fields.
            
            WHEN TO CALL THE TOOL:
            - If the user mentions anything about quotes/pricing (quote, quotation, price estimate, price, cost, pricing, estimate, get/need/want a quote), immediately call 'extract_quote_info'. Do not ask questions before the first call.
            - After each user reply, call the tool again to re-evaluate state until is_complete = true.
            - If the user says they want to change/update a specific field (e.g., email/contact info/product/quantity/start date/notes), gather that field only, then call the tool again. Do NOT re-ask already known fields unless the user says they also need to change them.
            
            HOW TO USE THE RESULT:
            - If is_complete = false: Ask only for the missing_fields, one at a time, very concise.
            - IMPORTANT: Check ALL missing_fields carefully. Do NOT say collection is complete until ALL required fields are filled:
              * customer_name: Customer's name
              * contact_info: Email address
              * quote_items: At least one product with both product_package (product name) AND quantity (number > 0)
            - SPECIAL HANDLING FOR PRODUCTS: 
              * If "quote_items" is in missing_fields and products_available is not empty, list the available products when asking.
              * If user mentions a product but no quantity, ask for the quantity: "What quantity do you need for [product name]?"
              * If user mentions quantity but no product, ask for the product: "Which product would you like? Available: [list products]"
              * Both product_package AND quantity are required for each item. Do NOT mark as complete if either is missing.
            - If is_complete = true: First restate all collected quote details (customer_name, contact_info, each quote_items product + quantity, expected_start_date, notes), then tell the user "I have all the information. Please review the details on your screen and confirm to send the quote. You can say 'confirm' or click the confirm button."
            
            CONFIRMATION:
            - If the user says "confirm", "yes", "send", "ok", "okay", "proceed", or "go ahead" after details are shown, proceed with sending the quote (the system will handle the send).
            
            KNOWLEDGE BASE:
            - For non-quote questions, first use the 'search' tool. If info is found, cite with 'report_grounding'. If not found, politely say it’s not in the knowledge base. Do not invent information.
            
            LANGUAGE:
            - Always respond in English, even if the user speaks another language or accent.
        """.strip()

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
        logger.info("Quote extraction tool and user registration tool attached. Available tools: %s", list(rtmt.tools.keys()))

        rtmt.attach_to_app(app, "/realtime")
        logger.info("RTMiddleTier initialized with Azure OpenAI")
    else:
        logger.warning("Azure OpenAI configuration not found. Voice features will be disabled. Quote API is still available.")

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

        # Try to create quote in Salesforce
        from salesforce_service import get_salesforce_service
        from email_service import send_quote_email
        
        sf_service = get_salesforce_service()
        quote_result = None
        
        if sf_service.is_available():
            try:
                # Create or get Account
                account_id = sf_service.create_or_get_account(customer_name, contact_info)
                if not account_id:
                    logger.warning("Failed to create/get Account, will create Quote without Account association")
                else:
                    logger.info("Account ID obtained: %s for customer: %s", account_id, customer_name)
                    # Create or get Contact
                    contact_id = sf_service.create_or_get_contact(account_id, customer_name, contact_info)
                    
                    # Create Opportunity (optional)
                    opportunity_id = None
                    if os.environ.get("SALESFORCE_CREATE_OPPORTUNITY", "false").lower() == "true":
                        opportunity_id = sf_service.create_opportunity(
                            account_id,
                            f"Opportunity for {customer_name}"
                        )
                
                # Try to create Quote even if account_id is None
                # The create_quote method can handle None account_id
                logger.info("Creating Quote - Account ID: %s, Quote Items: %s", account_id, len(quote_items))
                quote_result = sf_service.create_quote(
                    account_id=account_id,  # Can be None
                    opportunity_id=opportunity_id,
                    customer_name=customer_name,
                    quote_items=quote_items,  # Pass quote_items array
                    expected_start_date=expected_start_date,
                    notes=notes
                )
                if quote_result:
                    logger.info("Quote created successfully: ID=%s, Number=%s", quote_result.get("quote_id"), quote_result.get("quote_number"))
            except Exception as e:
                logger.error("Error creating quote in Salesforce: %s", str(e))
                import traceback
                logger.error("Traceback: %s", traceback.format_exc())
        
        # Fallback to mock if Salesforce is not available or failed
        if not quote_result:
            product_summary = ", ".join([f"{item.get('product_package')} (x{item.get('quantity')})" for item in quote_items])
            logger.warning("Quote creation failed or Salesforce unavailable. Using mock quote URL. Customer: %s, Products: %s", customer_name, product_summary)
            quote_id = str(uuid4())
            quote_url = f"https://example.com/quotes/{quote_id}"
            logger.info("Mock quote created: id=%s, customer=%s", quote_id, customer_name)
            quote_result = {
                "quote_id": quote_id,
                "quote_number": quote_id[:8],
                "quote_url": quote_url
            }
        
        # Send email notification
        email_sent = False
        email_error = None
        if "@" in contact_info:  # Only send if contact_info looks like an email
            try:
                logger.info("Attempting to send email to: %s", contact_info)
                # For email, use first product or create summary
                product_summary = ", ".join([f"{item.get('product_package')} (x{item.get('quantity')})" for item in quote_items])
                total_quantity = sum([int(item.get("quantity", 0)) for item in quote_items])
                email_sent = await send_quote_email(
                    to_email=contact_info,
                    customer_name=customer_name,
                    quote_url=quote_result["quote_url"],
                    product_package=product_summary,  # Use summary for email
                    quantity=str(total_quantity),  # Use total quantity
                    expected_start_date=expected_start_date,
                    notes=notes
                )
                if email_sent:
                    logger.info("Email sent successfully to %s", contact_info)
                else:
                    logger.warning("Email sending returned False for %s", contact_info)
            except Exception as e:
                logger.error("Error sending email: %s", str(e))
                email_error = str(e)
        
        response_data = {
            "quote_id": quote_result.get("quote_id"),
            "quote_number": quote_result.get("quote_number"),
            "quote_url": quote_result["quote_url"],
            "email_sent": email_sent,
            "email_error": email_error if not email_sent else None
        }
        
        return web.json_response(response_data)

    async def handle_get_products(request: web.Request) -> web.Response:
        """Get list of products from Salesforce."""
        from salesforce_service import get_salesforce_service
        
        sf_service = get_salesforce_service()
        products = []
        
        if sf_service.is_available():
            try:
                # Query active products
                result = sf_service.sf.query(
                    "SELECT Id, Name FROM Product2 WHERE IsActive = true ORDER BY Name LIMIT 100"
                )
                
                if result["totalSize"] > 0:
                    products = [
                        {"id": record["Id"], "name": record["Name"]}
                        for record in result["records"]
                    ]
            except Exception as e:
                logger.error("Error fetching products from Salesforce: %s", str(e))
        
        return web.json_response({"products": products})
    
    async def handle_register_user(request: web.Request) -> web.Response:
        """Register user to Salesforce - creates or gets Account and Contact."""
        payload = await request.json()
        customer_name = payload.get("customer_name")
        contact_info = payload.get("contact_info")
        
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
        
        # Reuse the quote creation logic
        from salesforce_service import get_salesforce_service
        from email_service import send_quote_email
        
        sf_service = get_salesforce_service()
        quote_result = None
        
        if sf_service.is_available():
            try:
                account_id = sf_service.create_or_get_account(customer_name, contact_info)
                if not account_id:
                    logger.warning("Failed to create/get Account, will create Quote without Account association")
                else:
                    contact_id = sf_service.create_or_get_contact(account_id, customer_name, contact_info)
                    
                    opportunity_id = None
                    if os.environ.get("SALESFORCE_CREATE_OPPORTUNITY", "false").lower() == "true":
                        opportunity_id = sf_service.create_opportunity(
                            account_id,
                            f"Opportunity for {customer_name}"
                        )
                
                # Try to create Quote even if account_id is None
                # The create_quote method can handle None account_id
                quote_result = sf_service.create_quote(
                    account_id=account_id,  # Can be None
                    opportunity_id=opportunity_id,
                    customer_name=customer_name,
                    quote_items=quote_items,  # Pass quote_items array
                    expected_start_date=expected_start_date,
                    notes=notes
                )
            except Exception as e:
                logger.error("Error creating quote in Salesforce: %s", str(e))
                import traceback
                logger.error("Traceback: %s", traceback.format_exc())
        
        if not quote_result:
            product_summary = ", ".join([f"{item.get('product_package')} (x{item.get('quantity')})" for item in quote_items])
            logger.warning("Quote creation failed or Salesforce unavailable. Using mock quote URL. Customer: %s, Products: %s", customer_name, product_summary)
            quote_id = str(uuid4())
            quote_url = f"https://example.com/quotes/{quote_id}"
            logger.info("Mock quote created: id=%s, customer=%s", quote_id, customer_name)
            quote_result = {
                "quote_id": quote_id,
                "quote_number": quote_id[:8],
                "quote_url": quote_url
            }
        
        # Send email
        email_sent = False
        email_error = None
        if "@" in contact_info:
            try:
                # For email, use first product or create summary
                product_summary = ", ".join([f"{item.get('product_package')} (x{item.get('quantity')})" for item in quote_items])
                total_quantity = sum([int(item.get("quantity", 0)) for item in quote_items])
                email_sent = await send_quote_email(
                    to_email=contact_info,
                    customer_name=customer_name,
                    quote_url=quote_result["quote_url"],
                    product_package=product_summary,  # Use summary for email
                    quantity=str(total_quantity),  # Use total quantity
                    expected_start_date=expected_start_date,
                    notes=notes
                )
            except Exception as e:
                logger.error("Error sending email: %s", str(e))
                email_error = str(e)
        
        return web.json_response({
            "success": True,
            "quote_id": quote_result.get("quote_id"),
            "quote_number": quote_result.get("quote_number"),
            "quote_url": quote_result["quote_url"],
            "email_sent": email_sent,
            "email_error": email_error if not email_sent else None
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
    
    # 注册 ACS Call Automation 路由（在 add_static 之前，避免路由冲突）
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
        logger.info("ACS call handler not available, skipping route registration")
        logger.info("  _acs_handler_available: %s", _acs_handler_available)
        logger.info("  register_acs_routes is None: %s", register_acs_routes is None)
    
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
    
    # Check Teams configuration status
    if os.environ.get("TEAMS_CLIENT_ID") or (os.environ.get("TEAMS_TENANT_ID") and os.environ.get("TEAMS_CLIENT_SECRET")):
        logger.info("Teams Calling API: http://%s:%s/api/teams/calls", host, port)
        logger.info("Teams Callbacks: http://%s:%s/api/teams/callbacks", host, port)
    else:
        logger.info("Teams Calling: Not configured (set TEAMS_* env vars to enable)")
    
    logger.info("=" * 50)
    web.run_app(create_app(), host=host, port=port)
