import logging
import os
from typing import Any, Optional
from uuid import uuid4

from quote_tools import _find_best_product_match, normalize_email

logger = logging.getLogger("voicerag")


def fetch_available_products(limit: int = 100) -> list[dict[str, str]]:
    from salesforce_service import get_salesforce_service

    sf_service = get_salesforce_service()
    products: list[dict[str, str]] = []
    if not sf_service.is_available():
        return products

    try:
        result = sf_service.sf.query(
            f"SELECT Id, Name FROM Product2 WHERE IsActive = true ORDER BY Name LIMIT {int(limit)}"
        )
        if result["totalSize"] > 0:
            products = [{"id": record["Id"], "name": record["Name"]} for record in result["records"]]
    except Exception as e:
        logger.error("Error fetching products: %s", str(e))

    return products


def merge_quote_items(
    existing_items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
    replace_items: bool = False,
) -> list[dict[str, Any]]:
    if replace_items:
        return [item for item in new_items if isinstance(item, dict)]

    merged_items = [item.copy() for item in existing_items if isinstance(item, dict)]
    for new_item in new_items:
        if not isinstance(new_item, dict):
            continue
        product_name = new_item.get("product_package")
        quantity = new_item.get("quantity")
        if not product_name:
            continue

        found = False
        for existing_item in merged_items:
            if existing_item.get("product_package") == product_name:
                existing_item["quantity"] = quantity
                found = True
                break
        if not found:
            merged_items.append(
                {
                    "product_package": product_name,
                    "quantity": quantity,
                }
            )

    return merged_items


def normalize_and_match_quote_extracted_data(
    current_extracted: dict[str, Any],
    new_extracted: dict[str, Any],
    products: list[dict[str, str]],
    replace_quote_items: bool = False,
) -> dict[str, Any]:
    extracted_data = dict(current_extracted or {})

    for key in ["customer_name", "contact_info", "expected_start_date", "notes"]:
        new_value = new_extracted.get(key)
        if new_value:
            extracted_data[key] = new_value

    existing_items = extracted_data.get("quote_items", []) or []
    new_items = new_extracted.get("quote_items", []) or []
    extracted_data["quote_items"] = merge_quote_items(existing_items, new_items, replace_quote_items)

    if extracted_data.get("contact_info"):
        normalized_email = normalize_email(str(extracted_data["contact_info"]))
        if normalized_email:
            extracted_data["contact_info"] = normalized_email

    if extracted_data.get("quote_items") and products:
        matched_items = []
        for item in extracted_data["quote_items"]:
            if not isinstance(item, dict):
                continue
            user_product = item.get("product_package")
            quantity = item.get("quantity")
            if not user_product:
                continue
            matched_product = _find_best_product_match(user_product, products)
            matched_items.append(
                {
                    "product_package": matched_product or user_product,
                    "quantity": quantity or 1,
                }
            )
        extracted_data["quote_items"] = matched_items

    extracted_data.setdefault("customer_name", None)
    extracted_data.setdefault("contact_info", None)
    extracted_data.setdefault("quote_items", [])
    extracted_data.setdefault("expected_start_date", None)
    extracted_data.setdefault("notes", None)

    return extracted_data


def build_quote_state(
    extracted_data: dict[str, Any],
    product_names: list[str],
) -> dict[str, Any]:
    valid_items = [
        item
        for item in extracted_data.get("quote_items", [])
        if isinstance(item, dict)
        and item.get("product_package")
        and item.get("quantity") is not None
        and item.get("quantity") > 0
    ]

    missing_fields = []
    if not extracted_data.get("customer_name"):
        missing_fields.append("customer_name")
    if not extracted_data.get("contact_info"):
        missing_fields.append("contact_info")
    if not valid_items:
        missing_fields.append("quote_items")

    return {
        "extracted": extracted_data,
        "missing_fields": missing_fields,
        "products_available": product_names,
        "is_complete": len(missing_fields) == 0,
    }


def generate_quote_collection_response(missing_fields: list[str], quote_state: dict[str, Any]) -> str:
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
            products_text = ", ".join(products_available[:5])
            return f"Which product would you like a quote for? Available products include: {products_text}. And how many would you need?"
        return "Which product would you like a quote for, and how many would you need?"

    return "I need a bit more information for your quote. Could you provide the missing details?"


def build_quote_confirmation_recap(quote_state: dict[str, Any]) -> str:
    extracted = quote_state.get("extracted", {}) if isinstance(quote_state, dict) else {}
    customer_name = extracted.get("customer_name") or "not provided"
    contact_info = extracted.get("contact_info") or "not provided"
    expected_start_date = extracted.get("expected_start_date") or "not provided"
    notes = extracted.get("notes") or "none"

    valid_items = [
        item
        for item in extracted.get("quote_items", []) or []
        if isinstance(item, dict) and item.get("product_package") and item.get("quantity")
    ]
    product_text = (
        ", ".join(f"{item.get('product_package')} x{item.get('quantity')}" for item in valid_items)
        if valid_items
        else "not provided"
    )

    return (
        f"Let me recap: name {customer_name}, contact {contact_info}, "
        f"products {product_text}, expected start date {expected_start_date}, notes {notes}."
    )


def build_quote_targeted_recap(quote_state: dict[str, Any], requested_fields: list[str]) -> str:
    if not requested_fields:
        return build_quote_confirmation_recap(quote_state)

    extracted = quote_state.get("extracted", {}) if isinstance(quote_state, dict) else {}
    parts: list[str] = []

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

    return "Let me recap: " + ", ".join(parts) + "." if parts else build_quote_confirmation_recap(quote_state)


async def create_quote_from_extracted(
    extracted: dict[str, Any],
    fallback_to_mock: bool = False,
) -> Optional[dict[str, Any]]:
    customer_name = extracted.get("customer_name")
    contact_info = extracted.get("contact_info")
    quote_items = extracted.get("quote_items", [])
    expected_start_date = extracted.get("expected_start_date")
    notes = extracted.get("notes")

    if not customer_name or not contact_info or not quote_items:
        logger.error(
            "Incomplete quote information: customer_name=%s, contact_info=%s, quote_items=%s",
            customer_name,
            contact_info,
            quote_items,
        )
        return None

    from email_service import send_quote_email
    from salesforce_service import get_salesforce_service

    sf_service = get_salesforce_service()
    quote_result = None

    if sf_service.is_available():
        try:
            account_id = sf_service.create_or_get_account(customer_name, contact_info)
            contact_id = None
            opportunity_id = None

            if account_id:
                contact_id = sf_service.create_or_get_contact(account_id, customer_name, contact_info)
                logger.info("Contact resolved for quote creation: %s", contact_id or "N/A")
                if os.environ.get("SALESFORCE_CREATE_OPPORTUNITY", "false").lower() == "true":
                    opportunity_id = sf_service.create_opportunity(account_id, f"Opportunity for {customer_name}")

            quote_result = sf_service.create_quote(
                account_id=account_id,
                opportunity_id=opportunity_id,
                customer_name=customer_name,
                quote_items=quote_items,
                expected_start_date=expected_start_date,
                notes=notes,
            )
        except Exception as e:
            logger.error("Error creating quote in Salesforce: %s", str(e))

    if not quote_result:
        if not fallback_to_mock:
            return None
        quote_id = str(uuid4())
        quote_result = {
            "quote_id": quote_id,
            "quote_number": quote_id[:8],
            "quote_url": f"https://example.com/quotes/{quote_id}",
        }

    email_sent = False
    email_error = None
    if "@" in str(contact_info):
        try:
            product_summary = ", ".join(
                [f"{item.get('product_package')} (x{item.get('quantity')})" for item in quote_items if isinstance(item, dict)]
            )
            total_quantity = sum([int(item.get("quantity", 0)) for item in quote_items if isinstance(item, dict)])
            email_sent = await send_quote_email(
                to_email=contact_info,
                customer_name=customer_name,
                quote_url=quote_result["quote_url"],
                product_package=product_summary,
                quantity=str(total_quantity),
                expected_start_date=expected_start_date,
                notes=notes,
            )
        except Exception as e:
            email_error = str(e)
            logger.error("Error sending email: %s", email_error)

    return {
        "quote_id": quote_result.get("quote_id"),
        "quote_number": quote_result.get("quote_number"),
        "quote_url": quote_result.get("quote_url"),
        "email_sent": email_sent,
        "email_error": email_error if not email_sent else None,
    }
