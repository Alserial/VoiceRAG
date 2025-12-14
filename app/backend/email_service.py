"""
Email service for sending quote notifications.
Supports multiple email providers: Azure Communication Services, SMTP, and Salesforce.
"""
import logging
import os
from pathlib import Path
from typing import Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

import aiohttp
import requests

logger = logging.getLogger("voicerag")


async def send_quote_email(
    to_email: str,
    customer_name: str,
    quote_url: str,
    product_package: str,
    quantity: str,
    expected_start_date: Optional[str] = None,
    notes: Optional[str] = None
) -> bool:
    """
    Send quote notification email.
    
    Args:
        to_email: Recipient email address
        customer_name: Customer name
        quote_url: Quote URL
        product_package: Product or package name
        quantity: Quantity
        expected_start_date: Expected start date
        notes: Additional notes
        
    Returns:
        True if email sent successfully, False otherwise
    """
    email_service = os.environ.get("EMAIL_SERVICE", "smtp").lower()
    
    if email_service == "azure":
        return await _send_azure_email(
            to_email, customer_name, quote_url, product_package, quantity, expected_start_date, notes
        )
    elif email_service == "smtp":
        return await _send_smtp_email(
            to_email, customer_name, quote_url, product_package, quantity, expected_start_date, notes
        )
    elif email_service == "salesforce":
        return await _send_salesforce_email(
            to_email, customer_name, quote_url, product_package, quantity, expected_start_date, notes
        )
    else:
        logger.warning("Unknown email service: %s. Email not sent.", email_service)
        return False


async def _send_azure_email(
    to_email: str,
    customer_name: str,
    quote_url: str,
    product_package: str,
    quantity: str,
    expected_start_date: Optional[str],
    notes: Optional[str]
) -> bool:
    """Send email using Azure Communication Services."""
    connection_string = os.environ.get("AZURE_COMMUNICATION_CONNECTION_STRING")
    from_email = os.environ.get("AZURE_COMMUNICATION_EMAIL_FROM")
    
    if not connection_string or not from_email:
        logger.warning("Azure Communication Services not configured. Email not sent.")
        return False
    
    try:
        # Extract endpoint and access key from connection string
        # Format: endpoint=https://...;accesskey=...
        parts = connection_string.split(";")
        endpoint = None
        access_key = None
        
        for part in parts:
            if part.startswith("endpoint="):
                endpoint = part.split("=", 1)[1]
            elif part.startswith("accesskey="):
                access_key = part.split("=", 1)[1]
        
        if not endpoint or not access_key:
            logger.error("Invalid Azure Communication Services connection string format")
            return False
        
        # Prepare email content
        subject = f"{os.environ.get('EMAIL_SUBJECT_PREFIX', '')}Quote Request - {product_package}".strip()
        html_content = _get_email_html_template(
            customer_name, quote_url, product_package, quantity, expected_start_date, notes
        )
        text_content = _get_email_text_template(
            customer_name, quote_url, product_package, quantity, expected_start_date, notes
        )
        
        # Azure Communication Services Email API
        url = f"{endpoint}/emails:send"
        headers = {
            "Content-Type": "application/json",
            "Ocp-Apim-Subscription-Key": access_key
        }
        
        payload = {
            "senderAddress": from_email,
            "content": {
                "subject": subject,
                "html": html_content,
                "plainText": text_content
            },
            "recipients": {
                "to": [{"address": to_email}]
            }
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status in [200, 202]:
                    logger.info("Email sent successfully via Azure Communication Services to %s", to_email)
                    return True
                else:
                    error_text = await response.text()
                    logger.error("Failed to send email via Azure: %s - %s", response.status, error_text)
                    return False
                    
    except Exception as e:
        logger.error("Error sending email via Azure Communication Services: %s", str(e))
        return False


async def _send_smtp_email(
    to_email: str,
    customer_name: str,
    quote_url: str,
    product_package: str,
    quantity: str,
    expected_start_date: Optional[str],
    notes: Optional[str]
) -> bool:
    """Send email using SMTP."""
    import smtplib
    import asyncio
    
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    smtp_use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
    from_email = os.environ.get("EMAIL_FROM", smtp_user)
    from_name = os.environ.get("EMAIL_FROM_NAME", "VoiceRAG System")
    
    if not all([smtp_host, smtp_user, smtp_password]):
        logger.warning("SMTP not configured. Email not sent.")
        return False
    
    try:
        # Prepare email
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{from_name} <{from_email}>"
        msg["To"] = to_email
        msg["Subject"] = f"{os.environ.get('EMAIL_SUBJECT_PREFIX', '')}Quote Request - {product_package}".strip()
        
        # Add text and HTML parts
        text_content = _get_email_text_template(
            customer_name, quote_url, product_package, quantity, expected_start_date, notes
        )
        html_content = _get_email_html_template(
            customer_name, quote_url, product_package, quantity, expected_start_date, notes
        )
        
        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))
        
        # Send email in thread pool to avoid blocking
        def send_sync():
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                if smtp_use_tls:
                    server.starttls()
                server.login(smtp_user, smtp_password)
                server.send_message(msg)
        
        await asyncio.get_event_loop().run_in_executor(None, send_sync)
        logger.info("Email sent successfully via SMTP to %s", to_email)
        return True
        
    except Exception as e:
        logger.error("Error sending email via SMTP: %s", str(e))
        return False


async def _send_salesforce_email(
    to_email: str,
    customer_name: str,
    quote_url: str,
    product_package: str,
    quantity: str,
    expected_start_date: Optional[str],
    notes: Optional[str]
) -> bool:
    """Send email using Salesforce API."""
    try:
        from salesforce_service import get_salesforce_service
        import asyncio
        import requests
        
        sf_service = get_salesforce_service()
        if not sf_service.is_available() or not sf_service.sf:
            logger.warning("Salesforce not available. Email not sent.")
            return False
        
        # Add customizable subject prefix to improve email deliverability
        subject_prefix = os.environ.get("EMAIL_SUBJECT_PREFIX", "").strip()
        if subject_prefix:
            subject = f"{subject_prefix} Quote Request - {product_package}"
        else:
            subject = f"Quote Request - {product_package}"
        
        # Use plain text email body instead of HTML
        text_body = _get_email_text_template(
            customer_name, quote_url, product_package, quantity, expected_start_date, notes
        )
        
        # Fix 1: Get instance_url from base_url to ensure it has https:// protocol
        # simple-salesforce's base_url is like:
        # https://orgfarm-xxx-dev-ed.develop.my.salesforce.com/services/data/v61.0/
        base_url = sf_service.sf.base_url
        instance_url = base_url.split("/services")[0]
        
        # Fallback: if base_url is not available, construct from sf_instance
        if not instance_url or not instance_url.startswith("http"):
            sf_instance = sf_service.sf.sf_instance
            if sf_instance:
                instance_url = f"https://{sf_instance}"
            else:
                logger.error("Cannot determine Salesforce instance URL")
                return False
        
        email_endpoint = f"{instance_url}/services/data/v58.0/actions/standard/emailSimple"
        
        # Use plain text body instead of HTML
        payload = {
            "inputs": [{
                "emailBody": text_body,
                "emailAddresses": to_email,
                "emailSubject": subject,
                "senderType": "CurrentUser"
            }]
        }
        
        headers = {
            "Authorization": f"Bearer {sf_service.sf.session_id}",
            "Content-Type": "application/json"
        }
        
        logger.info("Sending email via Salesforce REST API...")
        logger.info("Endpoint: %s", email_endpoint)
        logger.info("To: %s", to_email)
        logger.info("Subject: %s", subject)
        
        # Fix 2: Use asyncio.run_in_executor to avoid blocking the event loop
        def _post_email():
            return requests.post(email_endpoint, json=payload, headers=headers, timeout=10)
        
        response = await asyncio.get_event_loop().run_in_executor(None, _post_email)
        
        logger.info("Salesforce email API response: Status %s", response.status_code)
        
        if response.status_code in [200, 201]:
            response_data = response.json() if response.text else {}
            logger.info("Email sent successfully via Salesforce REST API to %s", to_email)
            logger.info("Response data: %s", response_data)
            
            # Check if there are any errors in the response
            if isinstance(response_data, dict):
                if response_data.get("hasErrors"):
                    logger.error("Email API returned errors: %s", response_data)
                    return False
                if "results" in response_data:
                    for result in response_data["results"]:
                        if "errors" in result and result["errors"]:
                            logger.error("Email API returned errors: %s", result["errors"])
                            return False
            
            return True
        else:
            error_text = response.text[:500] if response.text else "Unknown error"
            logger.error("Failed to send email via Salesforce REST API: %s - %s", 
                        response.status_code, error_text)
            try:
                error_json = response.json()
                logger.error("Error details: %s", error_json)
            except:
                pass
            return False
            
    except Exception as e:
        logger.error("Error sending email via Salesforce: %s", str(e))
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())
        return False


def _send_salesforce_email_soap_fallback(
    sf_service, to_email: str, subject: str, text_body: str, html_body: str
) -> bool:
    """Fallback method using Salesforce SOAP API for sending email."""
    try:
        # Use Salesforce SOAP API endpoint
        # This requires the SOAP API to be enabled
        instance_url = sf_service.instance_url or sf_service.sf.sf_instance
        
        # Use REST API to call the email sending endpoint
        # Salesforce provides a REST endpoint for sending emails
        email_endpoint = f"{instance_url}/services/data/v58.0/actions/standard/emailSimple"
        
        payload = {
            "inputs": [{
                "emailBody": html_body,
                "emailAddresses": to_email,
                "emailSubject": subject,
                "senderType": "CurrentUser"
            }]
        }
        
        # Use simple-salesforce's session to make REST call
        headers = {
            "Authorization": f"Bearer {sf_service.sf.session_id}",
            "Content-Type": "application/json"
        }
        
        import requests
        response = requests.post(email_endpoint, json=payload, headers=headers, timeout=10)
        
        if response.status_code in [200, 201]:
            logger.info("Email sent successfully via Salesforce REST API to %s", to_email)
            return True
        else:
            logger.error("Failed to send email via Salesforce REST API: %s - %s", 
                        response.status_code, response.text[:200])
            return False
            
    except Exception as e:
        logger.error("SOAP fallback also failed: %s", str(e))
        return False


def _get_email_html_template(
    customer_name: str,
    quote_url: str,
    product_package: str,
    quantity: str,
    expected_start_date: Optional[str],
    notes: Optional[str]
) -> str:
    """Generate HTML email template."""
    # Remove extra indentation and newlines to ensure clean HTML
    expected_start_html = f'<p><strong>Expected Start Date:</strong> {expected_start_date}</p>' if expected_start_date else ''
    notes_html = f'<p><strong>Notes:</strong> {notes}</p>' if notes else ''
    
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
.container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
.header {{ background-color: #6366f1; color: white; padding: 20px; text-align: center; }}
.content {{ background-color: #f9fafb; padding: 20px; }}
.button {{ display: inline-block; padding: 12px 24px; background-color: #6366f1; color: white; text-decoration: none; border-radius: 5px; margin: 20px 0; }}
.details {{ background-color: white; padding: 15px; margin: 15px 0; border-left: 4px solid #6366f1; }}
.footer {{ text-align: center; color: #666; font-size: 12px; margin-top: 20px; }}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>Quote Request</h1>
</div>
<div class="content">
<p>Dear {customer_name},</p>
<p>Thank you for your interest in our products. We have prepared a quote for you.</p>
<div class="details">
<h3>Quote Details:</h3>
<p><strong>Product/Package:</strong> {product_package}</p>
<p><strong>Quantity:</strong> {quantity}</p>
{expected_start_html}
{notes_html}
</div>
<p><a href="{quote_url}" class="button">View Quote</a></p>
<p>If the button doesn't work, copy and paste this link into your browser:</p>
<p><a href="{quote_url}">{quote_url}</a></p>
</div>
<div class="footer">
<p>This is an automated message from VoiceRAG System.</p>
</div>
</div>
</body>
</html>"""
    return html


def _get_email_text_template(
    customer_name: str,
    quote_url: str,
    product_package: str,
    quantity: str,
    expected_start_date: Optional[str],
    notes: Optional[str]
) -> str:
    """Generate plain text email template."""
    return f"""
Quote Request

Dear {customer_name},

Thank you for your interest in our products. We have prepared a quote for you.

Quote Details:
- Product/Package: {product_package}
- Quantity: {quantity}
{f'- Expected Start Date: {expected_start_date}' if expected_start_date else ''}
{f'- Notes: {notes}' if notes else ''}

View your quote at: {quote_url}

This is an automated message from VoiceRAG System.
    """.strip()


async def send_conversation_email(
    to_email: str,
    conversation_file: str,
    session_id: str
) -> bool:
    """
    Send conversation log file via email.
    
    Args:
        to_email: Recipient email address
        conversation_file: Path to the conversation file
        session_id: Session ID for the conversation
        
    Returns:
        True if email sent successfully, False otherwise
    """
    email_service = os.environ.get("EMAIL_SERVICE", "smtp").lower()
    
    if email_service == "azure":
        return await _send_conversation_azure_email(to_email, conversation_file, session_id)
    elif email_service == "smtp":
        return await _send_conversation_smtp_email(to_email, conversation_file, session_id)
    elif email_service == "salesforce":
        return await _send_conversation_salesforce_email(to_email, conversation_file, session_id)
    else:
        logger.warning("Unknown email service: %s. Email not sent.", email_service)
        return False


async def _send_conversation_smtp_email(
    to_email: str,
    conversation_file: str,
    session_id: str
) -> bool:
    """Send conversation email with attachment using SMTP."""
    import smtplib
    import asyncio
    
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    smtp_use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
    from_email = os.environ.get("EMAIL_FROM", smtp_user)
    from_name = os.environ.get("EMAIL_FROM_NAME", "VoiceRAG System")
    
    if not all([smtp_host, smtp_user, smtp_password]):
        logger.warning("SMTP not configured. Email not sent.")
        return False
    
    try:
        filepath = Path(conversation_file)
        if not filepath.exists():
            logger.error("Conversation file not found: %s", conversation_file)
            return False
        
        # Prepare email
        msg = MIMEMultipart()
        msg["From"] = f"{from_name} <{from_email}>"
        msg["To"] = to_email
        msg["Subject"] = f"VoiceRAG Conversation Log - Session {session_id[:8]}"
        
        # Add text body
        body = f"""
This email contains the conversation log from VoiceRAG session {session_id[:8]}.

The conversation file is attached to this email.

This is an automated message from VoiceRAG System.
        """.strip()
        msg.attach(MIMEText(body, "plain"))
        
        # Add attachment
        with open(filepath, "rb") as f:
            attachment = MIMEApplication(f.read(), _subtype="txt")
            attachment.add_header(
                "Content-Disposition",
                "attachment",
                filename=filepath.name
            )
            msg.attach(attachment)
        
        # Send email in thread pool to avoid blocking
        def send_sync():
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                if smtp_use_tls:
                    server.starttls()
                server.login(smtp_user, smtp_password)
                server.send_message(msg)
        
        await asyncio.get_event_loop().run_in_executor(None, send_sync)
        logger.info("Conversation email sent successfully via SMTP to %s", to_email)
        return True
        
    except Exception as e:
        logger.error("Error sending conversation email via SMTP: %s", str(e))
        return False


async def _send_conversation_azure_email(
    to_email: str,
    conversation_file: str,
    session_id: str
) -> bool:
    """Send conversation email with attachment using Azure Communication Services."""
    connection_string = os.environ.get("AZURE_COMMUNICATION_CONNECTION_STRING")
    from_email = os.environ.get("AZURE_COMMUNICATION_EMAIL_FROM")
    
    if not connection_string or not from_email:
        logger.warning("Azure Communication Services not configured. Email not sent.")
        return False
    
    try:
        filepath = Path(conversation_file)
        if not filepath.exists():
            logger.error("Conversation file not found: %s", conversation_file)
            return False
        
        # Read file content
        file_content = filepath.read_bytes()
        file_name = filepath.name
        
        # Extract endpoint and access key from connection string
        parts = connection_string.split(";")
        endpoint = None
        access_key = None
        
        for part in parts:
            if part.startswith("endpoint="):
                endpoint = part.split("=", 1)[1]
            elif part.startswith("accesskey="):
                access_key = part.split("=", 1)[1]
        
        if not endpoint or not access_key:
            logger.error("Invalid Azure Communication Services connection string format")
            return False
        
        # Prepare email content
        subject = f"VoiceRAG Conversation Log - Session {session_id[:8]}"
        text_content = f"""
This email contains the conversation log from VoiceRAG session {session_id[:8]}.

The conversation file is attached to this email.

This is an automated message from VoiceRAG System.
        """.strip()
        
        # Azure Communication Services Email API with attachment
        url = f"{endpoint}/emails:send"
        headers = {
            "Content-Type": "application/json",
            "Ocp-Apim-Subscription-Key": access_key
        }
        
        import base64
        file_base64 = base64.b64encode(file_content).decode("utf-8")
        
        payload = {
            "senderAddress": from_email,
            "content": {
                "subject": subject,
                "plainText": text_content
            },
            "recipients": {
                "to": [{"address": to_email}]
            },
            "attachments": [
                {
                    "name": file_name,
                    "contentType": "text/plain",
                    "contentInBase64": file_base64
                }
            ]
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status in [200, 202]:
                    logger.info("Conversation email sent successfully via Azure Communication Services to %s", to_email)
                    return True
                else:
                    error_text = await response.text()
                    logger.error("Failed to send conversation email via Azure: %s - %s", response.status, error_text)
                    return False
                    
    except Exception as e:
        logger.error("Error sending conversation email via Azure Communication Services: %s", str(e))
        return False


async def _send_conversation_salesforce_email(
    to_email: str,
    conversation_file: str,
    session_id: str
) -> bool:
    """Send conversation email using Salesforce API."""
    try:
        from salesforce_service import get_salesforce_service
        import asyncio
        import requests
        import base64
        
        sf_service = get_salesforce_service()
        if not sf_service.is_available() or not sf_service.sf:
            logger.warning("Salesforce not available. Email not sent.")
            return False
        
        filepath = Path(conversation_file)
        if not filepath.exists():
            logger.error("Conversation file not found: %s", conversation_file)
            return False
        
        # Read file content
        file_content = filepath.read_bytes()
        file_base64 = base64.b64encode(file_content).decode("utf-8")
        
        subject = f"VoiceRAG Conversation Log - Session {session_id[:8]}"
        text_body = f"""
This email contains the conversation log from VoiceRAG session {session_id[:8]}.

The conversation file is attached to this email.

This is an automated message from VoiceRAG System.
        """.strip()
        
        # Get instance_url
        base_url = sf_service.sf.base_url
        instance_url = base_url.split("/services")[0]
        
        if not instance_url or not instance_url.startswith("http"):
            sf_instance = sf_service.sf.sf_instance
            if sf_instance:
                instance_url = f"https://{sf_instance}"
            else:
                logger.error("Cannot determine Salesforce instance URL")
                return False
        
        # Note: Salesforce emailSimple API doesn't support attachments directly
        # We'll include the file content in the email body instead
        email_body = f"{text_body}\n\n--- Conversation File Content ---\n\n{filepath.read_text(encoding='utf-8')}"
        
        email_endpoint = f"{instance_url}/services/data/v58.0/actions/standard/emailSimple"
        
        payload = {
            "inputs": [{
                "emailBody": email_body,
                "emailAddresses": to_email,
                "emailSubject": subject,
                "senderType": "CurrentUser"
            }]
        }
        
        headers = {
            "Authorization": f"Bearer {sf_service.sf.session_id}",
            "Content-Type": "application/json"
        }
        
        def _post_email():
            return requests.post(email_endpoint, json=payload, headers=headers, timeout=10)
        
        response = await asyncio.get_event_loop().run_in_executor(None, _post_email)
        
        if response.status_code in [200, 201]:
            logger.info("Conversation email sent successfully via Salesforce REST API to %s", to_email)
            return True
        else:
            error_text = response.text[:500] if response.text else "Unknown error"
            logger.error("Failed to send conversation email via Salesforce REST API: %s - %s", 
                        response.status_code, error_text)
            return False
            
    except Exception as e:
        logger.error("Error sending conversation email via Salesforce: %s", str(e))
        return False

