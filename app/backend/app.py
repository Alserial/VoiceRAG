import logging
import os
from pathlib import Path
from uuid import uuid4

from aiohttp import web
from azure.core.credentials import AzureKeyCredential
from azure.identity import AzureDeveloperCliCredential, DefaultAzureCredential
from dotenv import load_dotenv

from ragtools import attach_rag_tools
from rtmt import RTMiddleTier

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voicerag")

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
            You are a helpful assistant with access to a knowledge base through the 'search' tool. 
            IMPORTANT: Always respond in English only, regardless of the user's language or accent. Never switch to other languages.
            The user is listening to answers with audio, so it's *super* important that answers are as short as possible, a single sentence if at all possible. 
            Never read file names or source names or keys out loud. 
            
            Always use the following step-by-step instructions to respond: 
            1. First, use the 'search' tool to check the knowledge base for relevant information.
            2. If you find relevant information in the knowledge base:
               - Use that information to answer the question
               - Use the 'report_grounding' tool to report the source of information
               - Keep your answer short and focused on the found information
            3. If you don't find relevant information in the knowledge base:
               - Politely inform the user that the information is not available in the knowledge base
               - Say something like "I don't have information about that in my knowledge base"
               - Do NOT use your general knowledge to answer questions outside the knowledge base
               - Do NOT make up or infer information
            4. Always respond in English, even if the user speaks in another language or has an accent.
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

        rtmt.attach_to_app(app, "/realtime")
        logger.info("RTMiddleTier initialized with Azure OpenAI")
    else:
        logger.warning("Azure OpenAI configuration not found. Voice features will be disabled. Quote API is still available.")

    async def handle_quote_request(request: web.Request) -> web.Response:
        """Handle quote request - creates quote in Salesforce and sends email."""
        payload = await request.json()
        required_fields = [
            "customer_name",
            "contact_info",
            "product_package",
            "quantity",
            "expected_start_date"
        ]
        missing_fields = [field for field in required_fields if not payload.get(field)]
        if missing_fields:
            return web.json_response(
                {"error": f"Missing required fields: {', '.join(missing_fields)}"},
                status=400
            )

        customer_name = payload["customer_name"]
        contact_info = payload["contact_info"]
        product_package = payload["product_package"]
        quantity = int(payload["quantity"])
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
                    logger.warning("Failed to create/get Account, falling back to mock")
                else:
                    # Create or get Contact
                    contact_id = sf_service.create_or_get_contact(account_id, customer_name, contact_info)
                    
                    # Create Opportunity (optional)
                    opportunity_id = None
                    if os.environ.get("SALESFORCE_CREATE_OPPORTUNITY", "false").lower() == "true":
                        opportunity_id = sf_service.create_opportunity(
                            account_id,
                            f"Opportunity for {customer_name}"
                        )
                    
                    # Create Quote
                    quote_result = sf_service.create_quote(
                        account_id=account_id,
                        opportunity_id=opportunity_id,
                        customer_name=customer_name,
                        product_package=product_package,
                        quantity=quantity,
                        expected_start_date=expected_start_date,
                        notes=notes
                    )
            except Exception as e:
                logger.error("Error creating quote in Salesforce: %s", str(e))
        
        # Fallback to mock if Salesforce is not available or failed
        if not quote_result:
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
                email_sent = await send_quote_email(
                    to_email=contact_info,
                    customer_name=customer_name,
                    quote_url=quote_result["quote_url"],
                    product_package=product_package,
                    quantity=str(quantity),
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
    
    current_directory = Path(__file__).parent
    app.add_routes([
        web.get('/', lambda _: web.FileResponse(current_directory / 'static/index.html')),
        web.get('/api/products', handle_get_products),
        web.post('/api/quotes', handle_quote_request)
    ])
    app.router.add_static('/', path=current_directory / 'static', name='static')
    
    return app

if __name__ == "__main__":
    host = "localhost"
    port = 8765
    logger.info("=" * 50)
    logger.info("Starting VoiceRAG server...")
    logger.info("Server URL: http://%s:%s", host, port)
    logger.info("Quote API: http://%s:%s/api/quotes", host, port)
    logger.info("=" * 50)
    web.run_app(create_app(), host=host, port=port)
