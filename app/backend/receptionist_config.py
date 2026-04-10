import json
import logging
import re
from pathlib import Path
from typing import Optional


logger = logging.getLogger("voicerag")

_DEFAULT_CONFIG = {
    "company_name": "George Fethers",
    "welcome_message": "Welcome to George Fethers, how can I assist you today?",
    "company_summary": (
        "For over 150 years, George Fethers & Co. has helped shape Australia's built environment "
        "through a commitment to material excellence and design integrity. We supply premium "
        "architectural surfaces including natural timber pre-finished veneers, high-performance "
        "laminates, engineered timber flooring, and facade solutions."
    ),
    "office_address": "6/331 Ingles Street, Port Melbourne VIC 3207",
    "office_hours": "Monday to Friday, 8:30 AM to 4:30 PM",
    "warehouse_address": "6/212 Turner Street, Port Melbourne VIC 3207",
    "warehouse_hours": "Monday to Friday, 7:00 AM to 3:30 PM",
    "website_url": "https://gfethers.com.au/",
    "general_email": "gfethers@gfethers.com.au",
    "office_phone": "03 8652 8000",
    "sales_rep": {"name": "", "phone": ""},
    "routing_contacts": {
        "flooring": {"label": "flooring", "name": "Julia Nardella", "phone": "03 8652 8007"},
        "panels": {"label": "panels and veneers", "name": "Biljana Dimoska", "phone": "03 8652 8023"},
        "glass": {"label": "glass", "name": "Shelley Reynolds", "phone": "03 8652 8018"},
        "fibreglass": {"label": "fibreglass", "name": "Kerry Abdula", "phone": "03 8652 8012"},
    },
}

_CONFIG_PATH = Path(__file__).resolve().parent / "receptionist_config.json"


def _load_config() -> dict:
    config = dict(_DEFAULT_CONFIG)
    try:
        if not _CONFIG_PATH.exists():
            logger.warning("Receptionist config JSON not found at %s; using defaults.", _CONFIG_PATH)
            return config

        loaded = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            config.update({k: v for k, v in loaded.items() if k not in {"routing_contacts", "sales_rep"}})
            sales_rep = loaded.get("sales_rep")
            if isinstance(sales_rep, dict):
                config["sales_rep"] = {
                    **dict(_DEFAULT_CONFIG["sales_rep"]),
                    **sales_rep,
                }
            routing_contacts = loaded.get("routing_contacts")
            if isinstance(routing_contacts, dict):
                merged_contacts = {
                    key: dict(value)
                    for key, value in _DEFAULT_CONFIG["routing_contacts"].items()
                }
                for key, value in routing_contacts.items():
                    if isinstance(value, dict):
                        merged_contacts[key] = {
                            **merged_contacts.get(key, {}),
                            **value,
                        }
                config["routing_contacts"] = merged_contacts
    except Exception as e:
        logger.warning("Failed to load receptionist config from %s: %s", _CONFIG_PATH, str(e))
    return config


_CONFIG = _load_config()

COMPANY_NAME = str(_CONFIG["company_name"])
WELCOME_MESSAGE = str(_CONFIG["welcome_message"])
COMPANY_SUMMARY = str(_CONFIG["company_summary"])
OFFICE_ADDRESS = str(_CONFIG["office_address"])
OFFICE_HOURS = str(_CONFIG["office_hours"])
WAREHOUSE_ADDRESS = str(_CONFIG["warehouse_address"])
WAREHOUSE_HOURS = str(_CONFIG["warehouse_hours"])
WEBSITE_URL = str(_CONFIG["website_url"])
GENERAL_EMAIL = str(_CONFIG["general_email"])
OFFICE_PHONE = str(_CONFIG["office_phone"])
SALES_REP = {
    "name": str(dict(_CONFIG.get("sales_rep", {})).get("name", "")),
    "phone": str(dict(_CONFIG.get("sales_rep", {})).get("phone", "")),
}
ROUTING_CONTACTS: dict[str, dict[str, str]] = {
    key: {
        "label": str(value.get("label", key)),
        "name": str(value.get("name", "")),
        "phone": str(value.get("phone", "")),
    }
    for key, value in dict(_CONFIG.get("routing_contacts", {})).items()
    if isinstance(value, dict)
}

INFO_TOPICS = {
    "company_name",
    "company_summary",
    "office_address",
    "office_hours",
    "warehouse_address",
    "warehouse_hours",
    "website",
    "email",
    "office_phone",
}

DEPARTMENTS = set(ROUTING_CONTACTS.keys())

RECEPTIONIST_INTENTS = {
    "caller_intro",
    "company_info",
    "routing_request",
    "general_qa",
}


def format_department_label(department: str) -> str:
    contact = ROUTING_CONTACTS.get(department, {})
    return str(contact.get("label") or department)


def to_e164_au(phone_number: str) -> Optional[str]:
    digits = re.sub(r"\D", "", phone_number or "")
    if not digits:
        return None
    if digits.startswith("61"):
        return f"+{digits}"
    if digits.startswith("0"):
        return f"+61{digits[1:]}"
    if phone_number.startswith("+"):
        return phone_number
    return None


def build_transfer_message(department: str, architectural_firm: bool = False) -> str:
    if architectural_firm and SALES_REP.get("phone"):
        sales_rep_name = SALES_REP.get("name") or "our sales representative"
        return (
            f"I'll connect you with {sales_rep_name}. "
            f"The direct number is {SALES_REP['phone']}."
        )

    contact = ROUTING_CONTACTS.get(department)
    if not contact:
        return f"I can connect you with our main office on {OFFICE_PHONE}."

    department_label = format_department_label(department)
    if architectural_firm:
        return (
            f"I'll connect you with {contact['name']} for {department_label}. "
            f"If you need it, the direct number is {contact['phone']}."
        )
    return (
        f"I'll connect you with {contact['name']} for {department_label}. "
        f"The direct number is {contact['phone']}."
    )


def get_contact_for_department(department: str) -> Optional[dict[str, str]]:
    return ROUTING_CONTACTS.get(department)


def get_sales_rep_contact() -> Optional[dict[str, str]]:
    if not SALES_REP.get("phone"):
        return None
    return {
        "name": SALES_REP.get("name") or "Sales Representative",
        "phone": SALES_REP["phone"],
    }


def build_receptionist_prompt() -> str:
    routing_lines = []
    for key, contact in ROUTING_CONTACTS.items():
        routing_lines.append(
            f"{format_department_label(key)} to {contact['name']} on {contact['phone']}"
        )
    routing_text = ", ".join(routing_lines)

    return (
        f"You are the AI receptionist for {COMPANY_NAME}. "
        "Speak only in warm, concise English suitable for a live phone call. "
        "Do not reply in Chinese or any other language. "
        "If the caller speaks another language, still answer in English. "
        f"Always open the call with this exact sentence: '{WELCOME_MESSAGE}' "
        "When a caller introduces themselves, briefly acknowledge their name and continue helping. "
        "You help with company information and call routing. "
        f"Company summary: {COMPANY_SUMMARY} "
        f"Office and showroom address: {OFFICE_ADDRESS}. Office hours: {OFFICE_HOURS}. "
        f"Warehouse address: {WAREHOUSE_ADDRESS}. Warehouse hours: {WAREHOUSE_HOURS}. "
        f"Website: {WEBSITE_URL}. General email: {GENERAL_EMAIL}. Office phone: {OFFICE_PHONE}. "
        "For routing, identify whether the caller needs flooring, panels and veneers, glass, or fibreglass. "
        "Ask what company they are from when routing is needed. "
        "Architectural firms should be routed to a sales representative. "
        f"For all other callers, route by department: {routing_text}. "
        "Keep each answer under 3 short sentences."
    )


def build_company_info_answer(info_topic: str) -> Optional[str]:
    if info_topic == "company_name":
        return f"The company name is {COMPANY_NAME}."
    if info_topic == "company_summary":
        return COMPANY_SUMMARY
    if info_topic == "office_address":
        return f"Our office and showroom are at {OFFICE_ADDRESS}."
    if info_topic == "office_hours":
        return f"Our office and showroom hours are {OFFICE_HOURS}."
    if info_topic == "warehouse_address":
        return f"Our warehouse is at {WAREHOUSE_ADDRESS}."
    if info_topic == "warehouse_hours":
        return f"Our warehouse hours are {WAREHOUSE_HOURS}."
    if info_topic == "website":
        return f"Our website is {WEBSITE_URL}."
    if info_topic == "email":
        return f"Our general office email is {GENERAL_EMAIL}."
    if info_topic == "office_phone":
        return f"Our office phone number is {OFFICE_PHONE}."
    return None


def build_routing_prompt(
    pending_department: Optional[str] = None,
    missing_company_name: bool = False,
) -> str:
    if pending_department and missing_company_name:
        return (
            f"Which company are you calling from? "
            f"This will help me route your {format_department_label(pending_department)} enquiry."
        )
    if pending_department:
        return (
            f"Are you calling about {format_department_label(pending_department)}? "
            "And what company are you calling from?"
        )

    available_departments = ", ".join(format_department_label(dept) for dept in ROUTING_CONTACTS)
    return (
        f"Which area can I help with: {available_departments}? "
        "And what company are you calling from?"
    )


def build_name_acknowledgement(name: str) -> str:
    return f"Thank you, {name}. How can I help?"
