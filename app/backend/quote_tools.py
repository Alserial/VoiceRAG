"""
Quote extraction tools for RTMiddleTier.
Detects quote requests and extracts information from conversation history.
"""
import json
import logging
import os
from typing import Any, Dict, List, Optional
from difflib import SequenceMatcher

from rtmt import RTMiddleTier, Tool, ToolResult, ToolResultDirection

logger = logging.getLogger("voicerag")

_quote_extraction_tool_schema = {
    "type": "function",
    "name": "extract_quote_info",
    "description": "Extract quote information from the conversation when user requests a quote. Use this tool when the user mentions needing a quote, quotation, or price estimate.",
    "parameters": {
        # No parameters needed; the server will read recent conversation history directly.
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False
    }
}


def _similarity(a: str, b: str) -> float:
    """Calculate similarity between two strings (0.0 to 1.0)."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _find_best_product_match(user_input: str, products: List[Dict[str, str]]) -> Optional[str]:
    """
    Find the best matching product from the list based on user input.
    
    Args:
        user_input: The product name or description mentioned by the user
        products: List of products with 'id' and 'name' keys
        
    Returns:
        The best matching product name, or None if no good match found
    """
    if not products or not user_input:
        return None
    
    best_match = None
    best_score = 0.0
    threshold = 0.3  # Minimum similarity threshold
    
    user_lower = user_input.lower().strip()
    
    for product in products:
        product_name = product.get("name", "")
        if not product_name:
            continue
            
        # Calculate similarity
        score = _similarity(user_lower, product_name.lower())
        
        # Check if user input contains product name or vice versa
        if user_lower in product_name.lower() or product_name.lower() in user_lower:
            score = max(score, 0.7)  # Boost score for substring matches
        
        if score > best_score:
            best_score = score
            best_match = product_name
    
    if best_score >= threshold:
        logger.info("Matched product '%s' to '%s' with score %.2f", user_input, best_match, best_score)
        return best_match
    
    logger.info("No good product match found for '%s' (best score: %.2f)", user_input, best_score)
    return None


async def _extract_quote_info_tool(
    rtmt: RTMiddleTier,
    session_id: str,
    args: Any
) -> ToolResult:
    """
    Quote state evaluator:
    - Can be called multiple times.
    - Accepts empty / partial context.
    - Returns structured state only (no user-facing text).
    """
    logger.info("extract_quote_info tool called with session_id=%s, args=%s", session_id, args)
    try:
        # Get conversation history (allow empty)
        logger.info("Checking conversation logs. Available sessions: %s", list(rtmt._conversation_logs.keys()))
        conversation = rtmt._conversation_logs.get(session_id, {"messages": []})
        messages = conversation.get("messages", [])
        logger.info("Found %d messages in conversation for session %s", len(messages), session_id)
        
        # Default extracted structure (allow empty)
        extracted_data: Dict[str, Any] = {
            "customer_name": None,
            "contact_info": None,
            "product_package": None,
            "quantity": None,
            "expected_start_date": None,
            "notes": None,
        }
        
        # If no messages yet, return current state without error
        if not messages:
            required_fields = ["customer_name", "contact_info", "product_package", "quantity"]
            missing_fields = required_fields.copy()
            result = {
                "extracted": extracted_data,
                "missing_fields": missing_fields,
                "products_available": [],
                "is_complete": False,
            }
            logger.info("No conversation yet; returning initial state: %s", json.dumps(result))
            return ToolResult(json.dumps(result), ToolResultDirection.TO_SERVER)
        
        # Build conversation text for LLM (last 10 messages)
        conversation_text = "\n".join([
            f"{msg['role'].upper()}: {msg['content']}"
            for msg in messages[-10:]  # Last 10 messages
        ])
        
        # Get available products
        from salesforce_service import get_salesforce_service
        sf_service = get_salesforce_service()
        products = []
        
        if sf_service.is_available():
            try:
                result = sf_service.sf.query(
                    "SELECT Id, Name FROM Product2 WHERE IsActive = true ORDER BY Name LIMIT 100"
                )
                if result["totalSize"] > 0:
                    products = [
                        {"id": record["Id"], "name": record["Name"]}
                        for record in result["records"]
                    ]
            except Exception as e:
                logger.error("Error fetching products: %s", str(e))
        
        # Use LLM to extract information from conversation
        from openai import AzureOpenAI
        from azure.core.credentials import AzureKeyCredential
        from azure.identity import DefaultAzureCredential
        
        openai_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        openai_deployment = (
            os.environ.get("AZURE_OPENAI_EXTRACTION_DEPLOYMENT")
            or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
            or "gpt-4o-mini"
        )
        llm_key = os.environ.get("AZURE_OPENAI_API_KEY")
        
        logger.info("Using deployment for extraction: %s, endpoint: %s", openai_deployment, openai_endpoint)
        
        if not openai_endpoint or not openai_deployment:
            logger.error("OpenAI configuration not available: endpoint=%s, deployment=%s", openai_endpoint, openai_deployment)
            required_fields = ["customer_name", "contact_info", "product_package", "quantity"]
            missing_fields = required_fields.copy()
            result = {
                "extracted": extracted_data,
                "missing_fields": missing_fields,
                "products_available": [],
                "is_complete": False,
            }
            return ToolResult(json.dumps(result), ToolResultDirection.TO_SERVER)
        
        # Use the same credential approach as the main app
        if llm_key:
            credential = AzureKeyCredential(llm_key)
        else:
            credential = DefaultAzureCredential()
        
        # Prepare product list for LLM
        product_names = [p["name"] for p in products] if products else []
        product_list_text = ", ".join(product_names) if product_names else "No products available"
        
        # Create extraction prompt
        extraction_prompt = f"""Extract quote information from the following conversation. 
Return a JSON object with the following fields:
- customer_name: Customer's name (if mentioned)
- contact_info: Email address or phone number (if mentioned)
- product_package: Product name mentioned by user (if any)
- quantity: Quantity or number of items (if mentioned, as a number)
- expected_start_date: Expected start date in format YYYY-MM-DD or dd/mm/yyyy (if mentioned)
- notes: Any additional notes or requirements mentioned

Available products: {product_list_text}

Conversation:
{conversation_text}

Return ONLY a valid JSON object, no other text. If a field is not found, use null for that field.
Example format:
{{
  "customer_name": "John Doe",
  "contact_info": "john@example.com",
  "product_package": null,
  "quantity": 10,
  "expected_start_date": null,
  "notes": "Need fast delivery"
}}"""

        # Call OpenAI to extract information (non-realtime text model)
        logger.info("Creating OpenAI client for extraction with deployment: %s", openai_deployment)
        if isinstance(credential, AzureKeyCredential):
            client = AzureOpenAI(
                api_key=credential.key,
                api_version="2024-02-15-preview",
                azure_endpoint=openai_endpoint
            )
        else:
            # Use token-based authentication
            token = credential.get_token("https://cognitiveservices.azure.com/.default").token
            client = AzureOpenAI(
                api_key=token,
                api_version="2024-02-15-preview",
                azure_endpoint=openai_endpoint
            )
        
        logger.info("Calling chat.completions API with deployment: %s (endpoint: %s/deployments/%s/chat/completions)", 
                   openai_deployment, openai_endpoint, openai_deployment)
        try:
            response = client.chat.completions.create(
                model=openai_deployment,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that extracts structured information from conversations. Always return valid JSON only."},
                    {"role": "user", "content": extraction_prompt}
                ],
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            logger.info("OpenAI API call successful, response received. Model used: %s", response.model if hasattr(response, 'model') else openai_deployment)
            extracted_data = json.loads(response.choices[0].message.content)
            logger.info("Extracted data parsed successfully: %s", json.dumps(extracted_data)[:300])
            # Ensure keys exist even if model omits them
            for key in ["customer_name", "contact_info", "product_package", "quantity", "expected_start_date", "notes"]:
                extracted_data.setdefault(key, None)
        except Exception as e:
            logger.error("Error calling OpenAI API: %s", str(e))
            import traceback
            logger.error("Traceback: %s", traceback.format_exc())
            required_fields = ["customer_name", "contact_info", "product_package", "quantity"]
            missing_fields = required_fields.copy()
            result = {
                "extracted": extracted_data,
                "missing_fields": missing_fields,
                "products_available": product_names,
                "is_complete": False,
            }
            return ToolResult(json.dumps(result), ToolResultDirection.TO_SERVER)
        
        # Match product name if provided
        if extracted_data.get("product_package") and products:
            user_product = extracted_data["product_package"]
            matched_product = _find_best_product_match(user_product, products)
            if matched_product:
                extracted_data["product_package"] = matched_product
                extracted_data["product_matched"] = True
            else:
                extracted_data["product_matched"] = False
        
        # Determine what information is missing
        required_fields = ["customer_name", "contact_info", "product_package", "quantity"]
        missing_fields = [
            field for field in required_fields
            if not extracted_data.get(field)
        ]
        
        # Prepare response (state only)
        result = {
            "extracted": extracted_data,
            "missing_fields": missing_fields,
            "products_available": product_names,
            "is_complete": len(missing_fields) == 0,
        }
        
        logger.info("Quote extraction result: missing_fields=%s, is_complete=%s, extracted_data=%s", missing_fields, result["is_complete"], json.dumps(extracted_data)[:200])
        
        result_text = json.dumps(result)
        if result["is_complete"]:
            logger.info("extract_quote_info SUCCESS - complete. Result length: %d", len(result_text))
            logger.info("extract_quote_info returning: %s", result_text[:500])
            return ToolResult(result_text, ToolResultDirection.TO_CLIENT)
        else:
            logger.info("extract_quote_info SUCCESS - incomplete. Result length: %d", len(result_text))
            logger.info("extract_quote_info returning: %s", result_text[:500])
            return ToolResult(result_text, ToolResultDirection.TO_SERVER)
        
    except Exception as e:
        logger.exception("extract_quote_info FAILED: %s", str(e))
        import traceback
        logger.error("Full traceback: %s", traceback.format_exc())
        fallback = {
            "extracted": {
                "customer_name": None,
                "contact_info": None,
                "product_package": None,
                "quantity": None,
                "expected_start_date": None,
                "notes": None,
            },
            "missing_fields": ["customer_name", "contact_info", "product_package", "quantity"],
            "products_available": [],
            "is_complete": False,
        }
        return ToolResult(
            json.dumps(fallback),
            ToolResultDirection.TO_SERVER
        )


def attach_quote_extraction_tool(rtmt: RTMiddleTier) -> None:
    """
    Attach quote extraction tool to RTMiddleTier.
    
    This tool allows the LLM to detect quote requests and extract information
    from the conversation history.
    """
    async def tool_handler(args: Any, session_id: str = None) -> ToolResult:
        # Get session_id from rtmt if not provided
        if not session_id:
            session_id = getattr(rtmt, '_current_session_id', None)
        if not session_id:
            return ToolResult(
                json.dumps({"error": "Session ID not available"}),
                ToolResultDirection.TO_SERVER
            )
        return await _extract_quote_info_tool(rtmt, session_id, args)
    
    rtmt.tools["extract_quote_info"] = Tool(
        schema=_quote_extraction_tool_schema,
        target=tool_handler
    )

