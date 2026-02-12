"""
Quote extraction tools for RTMiddleTier.
Detects quote requests and extracts information from conversation history.
"""
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
from difflib import SequenceMatcher

from rtmt import RTMiddleTier, Tool, ToolResult, ToolResultDirection

logger = logging.getLogger("voicerag")

# Email normalization constants
_WORD_MAP = [
    # at
    (r"\b(at|@|艾特|小老鼠|at-sign|atsign)\b", "@"),
    # dot / point
    (r"\b(dot|point|period|句号|点|點)\b", "."),
    # underscore
    (r"\b(underscore|under\s*score|下划线|下劃線)\b", "_"),
    # hyphen/dash
    (r"\b(dash|hyphen|minus|横杠|横杆|短横)\b", "-"),
    # plus
    (r"\b(plus|加号|加號)\b", "+"),
]

_DOMAIN_FIX = {
    "gamil.com": "gmail.com",
    "gmial.com": "gmail.com",
    "gmail.con": "gmail.com",
    "hotmial.com": "hotmail.com",
    "outllok.com": "outlook.com",
}

_TRAILING_PUNCT = ".,;:!?)）]}'\"，。；：！？"

_EMAIL_REGEX = re.compile(r"^[a-z0-9][a-z0-9._%+\-]*@[a-z0-9.\-]+\.[a-z]{2,}$", re.I)

# 宽松抽取：允许 @ 左右有空格、dot 左右有空格
_EMAIL_FIND_REGEX = re.compile(
    r"([a-z0-9][a-z0-9._%+\-\s]*\s*@\s*[a-z0-9.\-\s]+\s*\.\s*[a-z]{2,})",
    re.I
)

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


def _apply_word_map(s: str) -> str:
    """Apply word mapping to convert spoken words to email symbols."""
    out = s
    for pattern, repl in _WORD_MAP:
        out = re.sub(pattern, repl, out, flags=re.I)
    return out


def _strip_trailing_punct(s: str) -> str:
    """Remove trailing punctuation from string."""
    while s and s[-1] in _TRAILING_PUNCT:
        s = s[:-1]
    return s


def _normalize_one(candidate: str) -> str:
    """Normalize a single email candidate."""
    s = candidate.strip().lower()
    s = _strip_trailing_punct(s)
    s = _apply_word_map(s)
    s = re.sub(r"\s+", "", s)  # remove all whitespace
    s = _strip_trailing_punct(s)
    
    if "@" not in s:
        return s
    
    local, domain = s.split("@", 1)
    
    # 1) 合并 local 中的拆字分隔：k-e-n-a-n / k_e_n_a_n / k.e.n.a.n
    # 只有当它看起来像"很多单字符被分隔"才做合并，避免误伤正常邮箱
    if re.fullmatch(r"[a-z0-9](?:[-_.][a-z0-9]){3,}", local):
        local = re.sub(r"[-_.]", "", local)
    
    # 2) 处理多段 "-单字符" 的情况：K-E-N-A-N-2-5-2-9-0.44604 => kenan25290.44604
    # 仅在出现多段 "-单字符" 的情况下移除连字符
    if re.search(r"(?:^|[^a-z0-9])[a-z0-9](?:-[a-z0-9]){2,}", local):
        local = local.replace("-", "")
    
    # domain: 清理重复点、去首尾点
    domain = re.sub(r"\.{2,}", ".", domain).strip(".")
    
    # 修复缺少 dot 的常见 TLD 粘连：gmailcom -> gmail.com
    domain = re.sub(r"([a-z0-9])com$", r"\1.com", domain)
    domain = re.sub(r"([a-z0-9])net$", r"\1.net", domain)
    domain = re.sub(r"([a-z0-9])org$", r"\1.org", domain)
    
    # 常见拼写纠错
    domain = _DOMAIN_FIX.get(domain, domain)
    
    return f"{local}@{domain}"


def normalize_email(raw: str) -> Optional[str]:
    """
    Return a cleaned/normalized email or None if cannot get a valid email.
    
    Handles various voice transcription issues:
    - Converts "at" to "@", "dot" to ".", etc.
    - Fixes common domain typos (gamil.com -> gmail.com)
    - Removes hyphens from character-separated names (K-E-N-A-N -> kenan)
    - Cleans whitespace and punctuation
    """
    if not raw or not raw.strip():
        return None
    
    text = raw.strip().lower()
    text = _apply_word_map(text)
    text = _strip_trailing_punct(text)
    
    # 先尝试从文本中抓取候选 email 段
    candidates = [m.group(1) for m in _EMAIL_FIND_REGEX.finditer(text)]
    if not candidates:
        candidates = [text]
    
    # 逐个清洗并验证，返回第一个合法的
    for cand in candidates:
        norm = _normalize_one(cand)
        norm = re.sub(r"\.{2,}", ".", norm)
        norm = _strip_trailing_punct(norm)
        
        if _EMAIL_REGEX.match(norm):
            return norm
    
    return None


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
    threshold = 0.6  # Minimum similarity threshold (increased from 0.3 for better accuracy)
    
    user_lower = user_input.lower().strip()
    
    for product in products:
        product_name = product.get("name", "")
        if not product_name:
            continue
            
        product_lower = product_name.lower()
        
        # Exact match (case-insensitive)
        if user_lower == product_lower:
            logger.info("Exact product match found: '%s'", product_name)
            return product_name
            
        # Calculate similarity
        score = _similarity(user_lower, product_lower)
        
        # Check if user input contains product name or vice versa
        # Only boost if it's a meaningful substring match (not just single character)
        if len(user_lower) >= 3 and len(product_lower) >= 3:
            if user_lower in product_lower or product_lower in user_lower:
                score = max(score, 0.75)  # Boost score for substring matches
        
        if score > best_score:
            best_score = score
            best_match = product_name
    
    if best_score >= threshold:
        logger.info("Matched product '%s' to '%s' with score %.2f", user_input, best_match, best_score)
        return best_match
    
    logger.info("No good product match found for '%s' (best score: %.2f, threshold: %.2f)", user_input, best_score, threshold)
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
        # Support multiple quote items: quote_items is an array of {product_package, quantity}
        extracted_data: Dict[str, Any] = {
            "customer_name": None,
            "contact_info": None,
            "quote_items": [],  # Array of {product_package, quantity}
            "expected_start_date": None,
            "notes": None,
        }
        
        # If no messages yet, return current state without error
        if not messages:
            required_fields = ["customer_name", "contact_info", "quote_items"]
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
            missing_fields = ["customer_name", "contact_info", "quote_items"]
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
        
        # Create extraction prompt - support multiple products
        extraction_prompt = f"""Extract quote information from the following conversation. 
Return a JSON object with the following fields:
- customer_name: Customer's name (if mentioned)
- contact_info: Email address or phone number (if mentioned)
- quote_items: Array of items, each with {{"product_package": "product name", "quantity": number}}. 
  Support multiple products - if user mentions multiple products, include all of them.
  Example: [{{"product_package": "Product A", "quantity": 10}}, {{"product_package": "Product B", "quantity": 5}}]
  If only one product is mentioned, return array with one item: [{{"product_package": "Product A", "quantity": 10}}]
- expected_start_date: Expected start date in format YYYY-MM-DD or dd/mm/yyyy (if mentioned)
- notes: Any additional notes or requirements mentioned

Available products: {product_list_text}

Conversation:
{conversation_text}

Return ONLY a valid JSON object, no other text. If a field is not found, use null for that field (use [] for quote_items if no products mentioned).
Example format (single product):
{{
  "customer_name": "John Doe",
  "contact_info": "john@example.com",
  "quote_items": [{{"product_package": "Product A", "quantity": 10}}],
  "expected_start_date": null,
  "notes": "Need fast delivery"
}}

Example format (multiple products):
{{
  "customer_name": "John Doe",
  "contact_info": "john@example.com",
  "quote_items": [{{"product_package": "Product A", "quantity": 10}}, {{"product_package": "Product B", "quantity": 5}}],
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
            extracted_data.setdefault("customer_name", None)
            extracted_data.setdefault("contact_info", None)
            extracted_data.setdefault("quote_items", [])
            extracted_data.setdefault("expected_start_date", None)
            extracted_data.setdefault("notes", None)
            
            # Convert legacy format (product_package + quantity) to quote_items format if needed
            if "product_package" in extracted_data and extracted_data["product_package"] and "quote_items" not in extracted_data:
                # Legacy format detected, convert to new format
                if extracted_data.get("quantity"):
                    extracted_data["quote_items"] = [{
                        "product_package": extracted_data["product_package"],
                        "quantity": extracted_data["quantity"]
                    }]
                extracted_data.pop("product_package", None)
                extracted_data.pop("quantity", None)
            elif "product_package" in extracted_data and extracted_data.get("product_package") and not extracted_data.get("quote_items"):
                # If quote_items is empty but product_package exists, convert it
                if extracted_data.get("quantity"):
                    extracted_data["quote_items"] = [{
                        "product_package": extracted_data["product_package"],
                        "quantity": extracted_data["quantity"]
                    }]
                extracted_data.pop("product_package", None)
                extracted_data.pop("quantity", None)
            
            # Ensure quote_items is a list
            if not isinstance(extracted_data.get("quote_items"), list):
                extracted_data["quote_items"] = []
            
            # Normalize email address if contact_info is provided
            if extracted_data.get("contact_info"):
                original_contact = extracted_data["contact_info"]
                normalized_email = normalize_email(str(original_contact))
                if normalized_email:
                    if normalized_email != original_contact:
                        logger.info("Normalized email: '%s' -> '%s'", original_contact, normalized_email)
                    extracted_data["contact_info"] = normalized_email
                else:
                    # If normalization fails but contact_info looks like it might be an email, log warning
                    if "@" in str(original_contact) or any(word in str(original_contact).lower() for word in ["at", "dot", "gmail", "hotmail", "outlook"]):
                        logger.warning("Could not normalize email from: '%s'", original_contact)
        except Exception as e:
            logger.error("Error calling OpenAI API: %s", str(e))
            import traceback
            logger.error("Traceback: %s", traceback.format_exc())
            missing_fields = ["customer_name", "contact_info", "quote_items"]
            result = {
                "extracted": extracted_data,
                "missing_fields": missing_fields,
                "products_available": product_names,
                "is_complete": False,
            }
            return ToolResult(json.dumps(result), ToolResultDirection.TO_SERVER)
        
        # Match product names for all quote items
        quote_items = extracted_data.get("quote_items", [])
        if quote_items and products:
            matched_items = []
            for item in quote_items:
                if not isinstance(item, dict):
                    continue
                user_product = item.get("product_package")
                quantity = item.get("quantity")
                if user_product and products:
                    matched_product = _find_best_product_match(user_product, products)
                    if matched_product:
                        matched_items.append({
                            "product_package": matched_product,
                            "quantity": quantity or 1
                        })
                        logger.info("Product matched: '%s' -> '%s'", user_product, matched_product)
                    else:
                        # If no good match found, keep original but mark it
                        logger.warning("Product '%s' does not match any available products. Available products: %s", 
                                     user_product, product_names)
                        matched_items.append({
                            "product_package": user_product,  # Keep original for now
                            "quantity": quantity or 1,
                            "matched": False
                        })
                elif user_product:
                    # Product without quantity
                    matched_items.append({
                        "product_package": user_product,
                        "quantity": 1
                    })
            extracted_data["quote_items"] = matched_items
        
        # Auto-register user to Salesforce when we have name and contact info
        # This ensures the user is already in Salesforce before creating quotes
        customer_name = extracted_data.get("customer_name")
        contact_info = extracted_data.get("contact_info")
        if customer_name and contact_info and sf_service.is_available():
            try:
                # Register user asynchronously (don't block the response)
                # This ensures user is in Salesforce for future quote operations
                account_id = sf_service.create_or_get_account(customer_name, contact_info)
                if account_id:
                    contact_id = sf_service.create_or_get_contact(account_id, customer_name, contact_info)
                    logger.info("Auto-registered user to Salesforce: %s (Account: %s, Contact: %s)", 
                               customer_name, account_id, contact_id or "N/A")
                else:
                    logger.warning("Failed to auto-register user to Salesforce: %s", customer_name)
            except Exception as e:
                # Don't fail the quote extraction if registration fails
                logger.warning("Error auto-registering user to Salesforce: %s", str(e))
        
        # Determine what information is missing
        missing_fields = []
        if not extracted_data.get("customer_name"):
            missing_fields.append("customer_name")
        if not extracted_data.get("contact_info"):
            missing_fields.append("contact_info")
        
        # Check quote_items: must have at least one item with both product_package and quantity
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
            logger.info("quote_items validation failed: no valid items found. quote_items=%s", quote_items)
        
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
                "quote_items": [],
                "expected_start_date": None,
                "notes": None,
            },
            "missing_fields": ["customer_name", "contact_info", "quote_items"],
            "products_available": [],
            "is_complete": False,
        }
        return ToolResult(
            json.dumps(fallback),
            ToolResultDirection.TO_SERVER
        )


_user_registration_tool_schema = {
    "type": "function",
    "name": "extract_user_info",
    "description": "Extract user registration information (name and email) from the conversation. Use this tool when the user provides their name and email address for registration.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False
    }
}


async def _extract_user_info_tool(
    rtmt: RTMiddleTier,
    session_id: str,
    args: Any
) -> ToolResult:
    """
    Extract user registration information (name and email) from conversation.
    
    Returns structured state: {"extracted": {"customer_name": ..., "contact_info": ...}, "is_complete": bool}
    """
    logger.info("extract_user_info tool called with session_id=%s", session_id)
    try:
        conversation = rtmt._conversation_logs.get(session_id, {"messages": []})
        messages = conversation.get("messages", [])
        
        if not messages:
            result = {
                "extracted": {
                    "customer_name": None,
                    "contact_info": None,
                },
                "is_complete": False,
            }
            return ToolResult(json.dumps(result), ToolResultDirection.TO_SERVER)
        
        conversation_text = "\n".join([
            f"{msg['role'].upper()}: {msg['content']}"
            for msg in messages[-10:]
        ])
        
        # Use LLM to extract user information
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
        
        if not openai_endpoint or not openai_deployment:
            result = {
                "extracted": {"customer_name": None, "contact_info": None},
                "is_complete": False,
            }
            return ToolResult(json.dumps(result), ToolResultDirection.TO_SERVER)
        
        if llm_key:
            credential = AzureKeyCredential(llm_key)
        else:
            credential = DefaultAzureCredential()
        
        extraction_prompt = f"""Extract user registration information from the following conversation.
Return a JSON object with:
- customer_name: User's name (if known from any prior user turn)
- contact_info: Email address (if known from any prior user turn)

Important rules:
- Use the full conversation context, not just the latest message.
- If the latest user message asks what they previously provided (for example: "what email did I give"), return the previously provided values from earlier turns.
- Prefer the most recently provided valid value when multiple values exist.

Conversation:
{conversation_text}

Return ONLY a valid JSON object, no other text. If a field is not found, use null.
Example format:
{{
  "customer_name": "John Doe",
  "contact_info": "john@example.com"
}}"""
        
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
        
        response = client.chat.completions.create(
            model=openai_deployment,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that extracts structured information from conversations. Always return valid JSON only."},
                {"role": "user", "content": extraction_prompt}
            ],
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        
        extracted_data = json.loads(response.choices[0].message.content)
        
        # Normalize email if provided
        if extracted_data.get("contact_info"):
            original_contact = extracted_data["contact_info"]
            normalized_email = normalize_email(str(original_contact))
            if normalized_email:
                extracted_data["contact_info"] = normalized_email
        
        is_complete = bool(extracted_data.get("customer_name") and extracted_data.get("contact_info"))
        
        result = {
            "extracted": {
                "customer_name": extracted_data.get("customer_name"),
                "contact_info": extracted_data.get("contact_info"),
            },
            "is_complete": is_complete,
        }
        
        logger.info("User info extraction result: is_complete=%s, name=%s, email=%s", 
                   is_complete, extracted_data.get("customer_name"), extracted_data.get("contact_info"))
        
        result_text = json.dumps(result)
        if is_complete:
            return ToolResult(result_text, ToolResultDirection.TO_CLIENT)
        else:
            return ToolResult(result_text, ToolResultDirection.TO_SERVER)
        
    except Exception as e:
        logger.exception("extract_user_info FAILED: %s", str(e))
        fallback = {
            "extracted": {"customer_name": None, "contact_info": None},
            "is_complete": False,
        }
        return ToolResult(json.dumps(fallback), ToolResultDirection.TO_SERVER)


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


def attach_user_registration_tool(rtmt: RTMiddleTier) -> None:
    """
    Attach user registration tool to RTMiddleTier.
    
    This tool allows the LLM to extract user name and email for registration.
    """
    async def tool_handler(args: Any, session_id: str = None) -> ToolResult:
        if not session_id:
            session_id = getattr(rtmt, '_current_session_id', None)
        if not session_id:
            return ToolResult(
                json.dumps({"error": "Session ID not available"}),
                ToolResultDirection.TO_SERVER
            )
        return await _extract_user_info_tool(rtmt, session_id, args)
    
    rtmt.tools["extract_user_info"] = Tool(
        schema=_user_registration_tool_schema,
        target=tool_handler
    )

