"""
Salesforce integration service for creating quotes.
"""
import logging
import os
from typing import Optional, Dict, Any

try:
    from simple_salesforce import Salesforce
except ImportError:
    Salesforce = None

logger = logging.getLogger("voicerag")


class SalesforceService:
    """Service for interacting with Salesforce API."""

    def __init__(self):
        """Initialize Salesforce connection."""
        self.sf: Optional[Salesforce] = None
        self.instance_url: Optional[str] = None
        self._connect()

    def _connect(self) -> bool:
        """Connect to Salesforce using Username-Password OAuth flow."""
        if Salesforce is None:
            logger.warning("simple-salesforce not installed. Salesforce features disabled.")
            return False

        instance_url = os.environ.get("SALESFORCE_INSTANCE_URL")
        username = os.environ.get("SALESFORCE_USERNAME")
        password = os.environ.get("SALESFORCE_PASSWORD")
        security_token = os.environ.get("SALESFORCE_SECURITY_TOKEN")
        consumer_key = os.environ.get("SALESFORCE_CONSUMER_KEY")
        consumer_secret = os.environ.get("SALESFORCE_CONSUMER_SECRET")

        if not all([instance_url, username, password, security_token, consumer_key, consumer_secret]):
            logger.warning("Salesforce credentials not configured. Salesforce features disabled.")
            return False

        try:
            # Determine login domain based on instance URL
            # For Developer Edition with .develop.lightning.force.com, use "login" not "test"
            # Only use "test" for actual sandbox URLs
            if "test.salesforce.com" in instance_url.lower() or "sandbox" in instance_url.lower():
                domain = "test"
            elif "develop" in instance_url.lower() or "dev-ed" in instance_url.lower():
                # Developer Edition may use "login" domain
                domain = "login"
            else:
                domain = "login"
            
            # Username-Password OAuth flow using REST API
            # Force REST API by using session_id=None and setting instance_url after
            # simple-salesforce will use REST API OAuth when SOAP is disabled
            try:
                self.sf = Salesforce(
                    username=username,
                    password=password,
                    security_token=security_token,
                    consumer_key=consumer_key,
                    consumer_secret=consumer_secret,
                    domain=domain
                )
            except Exception as soap_error:
                # If SOAP fails, try using REST API directly via OAuth
                if "SOAP" in str(soap_error) or "soap" in str(soap_error).lower():
                    logger.info("SOAP API disabled, using REST API OAuth flow")
                    # Use REST API OAuth flow
                    import requests
                    login_url = "https://test.salesforce.com" if domain == "test" else "https://login.salesforce.com"
                    token_url = f"{login_url}/services/oauth2/token"
                    
                    oauth_data = {
                        "grant_type": "password",
                        "client_id": consumer_key,
                        "client_secret": consumer_secret,
                        "username": username,
                        "password": password + security_token
                    }
                    
                    response = requests.post(token_url, data=oauth_data, timeout=10)
                    if response.status_code == 200:
                        oauth_result = response.json()
                        access_token = oauth_result["access_token"]
                        instance_url_from_oauth = oauth_result["instance_url"]
                        
                        # Create Salesforce instance with access token
                        self.sf = Salesforce(
                            instance_url=instance_url_from_oauth,
                            session_id=access_token
                        )
                        self.instance_url = instance_url_from_oauth
                        logger.info("Successfully connected to Salesforce via REST API OAuth")
                        return True
                    else:
                        raise Exception(f"OAuth failed: {response.json().get('error_description', 'Unknown error')}")
                else:
                    raise
            self.instance_url = instance_url
            logger.info("Successfully connected to Salesforce")
            return True
        except Exception as e:
            logger.error("Failed to connect to Salesforce: %s", str(e))
            self.sf = None
            return False

    def is_available(self) -> bool:
        """Check if Salesforce is available and connected."""
        return self.sf is not None

    def create_or_get_account(self, customer_name: str, contact_info: str) -> Optional[str]:
        """
        Create or get an Account by email/phone (unique identifier).
        
        Strategy: Use email/phone as primary identifier to avoid duplicate name conflicts.
        - First, try to find existing Contact by email/phone
        - If found, use the Contact's Account
        - If not found, create new Account and Contact
        
        Args:
            customer_name: Name of the customer/account
            contact_info: Contact information (email or phone) - used as unique identifier
            
        Returns:
            Account ID if successful, None otherwise
        """
        if not self.is_available():
            return None

        try:
            # Step 1: Try to find existing Contact by email or phone (unique identifier)
            # This avoids the problem of duplicate names
            if "@" in contact_info:
                # Search by email (most reliable unique identifier)
                contact_query = f"SELECT Id, Name, Email, AccountId FROM Contact WHERE Email = '{contact_info.replace("'", "''")}' LIMIT 1"
            elif any(char.isdigit() for char in contact_info):
                # Search by phone as fallback
                contact_query = f"SELECT Id, Name, Email, AccountId, Phone FROM Contact WHERE Phone = '{contact_info.replace("'", "''")}' LIMIT 1"
            else:
                # No valid contact info, cannot use email/phone lookup
                contact_query = None
            
            if contact_query:
                contact_result = self.sf.query(contact_query)
                if contact_result["totalSize"] > 0:
                    existing_contact = contact_result["records"][0]
                    account_id = existing_contact.get("AccountId")
                    if account_id:
                        logger.info("Found existing Contact by email/phone: %s (Contact ID: %s, Account ID: %s)", 
                                   contact_info, existing_contact["Id"], account_id)
                        # Update contact name if it changed (optional, but good for data quality)
                        try:
                            if existing_contact.get("Name") != customer_name:
                                self.sf.Contact.update(existing_contact["Id"], {"LastName": customer_name})
                                logger.info("Updated Contact name from '%s' to '%s'", existing_contact.get("Name"), customer_name)
                        except Exception as e:
                            logger.warning("Could not update Contact name: %s", str(e))
                        return account_id
            
            # Step 2: If no Contact found by email/phone, try to find Account by name as fallback
            # (but only if we don't have email, since email is more reliable)
            if "@" not in contact_info:
                account_result = self.sf.query(
                    f"SELECT Id, Name FROM Account WHERE Name = '{customer_name.replace("'", "''")}' LIMIT 1"
                )
                if account_result["totalSize"] > 0:
                    account_id = account_result["records"][0]["Id"]
                    logger.info("Found existing Account by name: %s (ID: %s)", customer_name, account_id)
                    return account_id
            
            # Step 3: Create new account (no existing contact or account found)
            account_data = {
                "Name": customer_name,
                "Type": "Customer"
            }
            
            # Store contact info in Account fields if available
            if "@" in contact_info:
                account_data["Website"] = contact_info  # Store email in Website field temporarily
            elif any(char.isdigit() for char in contact_info):
                account_data["Phone"] = contact_info
            
            result = self.sf.Account.create(account_data)
            account_id = result["id"]
            logger.info("Created new Account: %s (ID: %s)", customer_name, account_id)
            return account_id
            
        except Exception as e:
            logger.error("Failed to create/get Account: %s", str(e))
            import traceback
            logger.error("Traceback: %s", traceback.format_exc())
            return None

    def create_or_get_contact(self, account_id: str, customer_name: str, contact_info: str) -> Optional[str]:
        """
        Create or get a Contact for the account.
        
        Uses email/phone as primary identifier to find existing contact,
        then updates AccountId if needed (in case contact exists but was linked to different account).
        
        Args:
            account_id: Account ID
            customer_name: Customer name
            contact_info: Contact information (email or phone) - used as unique identifier
            
        Returns:
            Contact ID if successful, None otherwise
        """
        if not self.is_available():
            return None

        try:
            # Step 1: Try to find existing contact by email/phone (unique identifier)
            # Don't restrict to AccountId first, to find contact even if it's linked to different account
            if "@" in contact_info:
                # Search by email (most reliable unique identifier)
                contact_query = f"SELECT Id, Name, Email, AccountId FROM Contact WHERE Email = '{contact_info.replace("'", "''")}' LIMIT 1"
            elif any(char.isdigit() for char in contact_info):
                # Search by phone as fallback
                contact_query = f"SELECT Id, Name, Email, Phone, AccountId FROM Contact WHERE Phone = '{contact_info.replace("'", "''")}' LIMIT 1"
            else:
                contact_query = None
            
            if contact_query:
                result = self.sf.query(contact_query)
                if result["totalSize"] > 0:
                    existing_contact = result["records"][0]
                    contact_id = existing_contact["Id"]
                    existing_account_id = existing_contact.get("AccountId")
                    
                    # If contact exists but is linked to different account, update it
                    if existing_account_id and existing_account_id != account_id:
                        try:
                            self.sf.Contact.update(contact_id, {"AccountId": account_id})
                            logger.info("Updated Contact %s from Account %s to Account %s", contact_id, existing_account_id, account_id)
                        except Exception as e:
                            logger.warning("Could not update Contact AccountId: %s", str(e))
                            # Continue anyway, contact exists even if we can't update AccountId
                    
                    # Update name if it changed
                    try:
                        if existing_contact.get("Name") != customer_name:
                            self.sf.Contact.update(contact_id, {"LastName": customer_name})
                            logger.info("Updated Contact name from '%s' to '%s'", existing_contact.get("Name"), customer_name)
                    except Exception as e:
                        logger.warning("Could not update Contact name: %s", str(e))
                    
                    logger.info("Found existing Contact: %s (ID: %s, Account: %s)", customer_name, contact_id, account_id)
                    return contact_id
            
            # Step 2: If no contact found by email/phone, try to find in this specific account
            # (fallback for cases where we only have phone and there might be duplicates)
            email_filter = f"Email = '{contact_info.replace("'", "''")}'" if "@" in contact_info else None
            phone_filter = f"Phone = '{contact_info.replace("'", "''")}'" if email_filter is None else None
            
            query_filter = email_filter or phone_filter or "1=0"
            if query_filter != "1=0":
                result = self.sf.query(
                    f"SELECT Id, Name, Email FROM Contact WHERE AccountId = '{account_id}' AND ({query_filter}) LIMIT 1"
                )
                
                if result["totalSize"] > 0:
                    contact_id = result["records"][0]["Id"]
                    logger.info("Found existing Contact in Account: %s (ID: %s)", customer_name, contact_id)
                    return contact_id
            
            # Step 3: Create new contact (no existing contact found)
            contact_data = {
                "AccountId": account_id,
                "LastName": customer_name,
            }
            
            if "@" in contact_info:
                contact_data["Email"] = contact_info
            elif any(char.isdigit() for char in contact_info):
                contact_data["Phone"] = contact_info
            
            result = self.sf.Contact.create(contact_data)
            contact_id = result["id"]
            logger.info("Created new Contact: %s (ID: %s)", customer_name, contact_id)
            return contact_id
            
        except Exception as e:
            logger.error("Failed to create/get Contact: %s", str(e))
            import traceback
            logger.error("Traceback: %s", traceback.format_exc())
            return None

    def create_opportunity(self, account_id: str, name: str, stage: Optional[str] = None) -> Optional[str]:
        """
        Create an Opportunity for the account.
        
        Args:
            account_id: Account ID
            name: Opportunity name
            stage: Opportunity stage (default: Prospecting)
            
        Returns:
            Opportunity ID if successful, None otherwise
        """
        if not self.is_available():
            return None

        try:
            stage = stage or os.environ.get("SALESFORCE_OPPORTUNITY_STAGE", "Prospecting")
            
            opportunity_data = {
                "AccountId": account_id,
                "Name": name,
                "StageName": stage,
                "CloseDate": "2024-12-31"  # Default close date
            }
            
            result = self.sf.Opportunity.create(opportunity_data)
            opportunity_id = result["id"]
            logger.info("Created Opportunity: %s (ID: %s)", name, opportunity_id)
            return opportunity_id
            
        except Exception as e:
            logger.error("Failed to create Opportunity: %s", str(e))
            return None

    def _get_all_products(self) -> str:
        """Get list of all active products for debugging."""
        if not self.is_available():
            return "Salesforce not available"
        try:
            result = self.sf.query("SELECT Name FROM Product2 WHERE IsActive = true LIMIT 10")
            if result["totalSize"] > 0:
                names = [record["Name"] for record in result["records"]]
                return ", ".join(names)
            return "No active products found"
        except Exception as e:
            return f"Error: {str(e)}"

    def create_quote(
        self,
        account_id: Optional[str],
        opportunity_id: Optional[str],
        customer_name: str,
        quote_items: list,  # Array of {"product_package": str, "quantity": int}
        expected_start_date: Optional[str] = None,
        notes: Optional[str] = None,
        # Legacy parameters for backward compatibility
        product_package: Optional[str] = None,
        quantity: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Create a Quote in Salesforce with multiple quote items.
        
        Args:
            account_id: Account ID
            opportunity_id: Opportunity ID (optional)
            customer_name: Customer name
            quote_items: List of quote items, each with {"product_package": str, "quantity": int}
            expected_start_date: Expected start date (YYYY-MM-DD)
            notes: Additional notes
            product_package: (Legacy) Single product name (for backward compatibility)
            quantity: (Legacy) Single quantity (for backward compatibility)
            
        Returns:
            Dictionary with quote_id and quote_url if successful, None otherwise
        """
        if not self.is_available():
            return None

        try:
            # Create Quote
            # Note: In some Salesforce orgs, AccountId might need to be set differently
            # Try using AccountId first, if it fails, try without it
            quote_data = {
                "Name": f"Quote for {customer_name}",
                "Status": "Draft"
            }
            
            # Set Pricebook2Id if configured (required for Quote Line Items)
            pricebook_id = os.environ.get("SALESFORCE_DEFAULT_PRICEBOOK_ID")
            if pricebook_id:
                try:
                    quote_data["Pricebook2Id"] = pricebook_id
                    logger.info("Setting Pricebook2Id on Quote: %s", pricebook_id)
                except Exception:
                    logger.warning("Cannot set Pricebook2Id on Quote, will try without it")
            
            # Try to set AccountId if provided, but handle permission errors gracefully
            if account_id:
                try:
                    quote_data["AccountId"] = account_id
                except Exception:
                    logger.warning("Cannot set AccountId on Quote, will create without Account association")
            else:
                logger.info("No AccountId provided, creating Quote without Account association")
            
            if opportunity_id:
                quote_data["OpportunityId"] = opportunity_id
            
            if expected_start_date:
                quote_data["ExpirationDate"] = expected_start_date
            
            # Add custom fields if they exist
            if notes:
                # Try to set Notes field (custom field API name might be different)
                try:
                    quote_data["Description"] = notes
                except:
                    pass
            
            # Try to create Quote with AccountId first
            quote_id = None
            try:
                result = self.sf.Quote.create(quote_data)
                quote_id = result["id"]
                logger.info("Created Quote with AccountId: %s", quote_id)
            except Exception as create_error:
                error_str = str(create_error)
                # If AccountId permission error, try without it
                if "AccountId" in error_str or "INVALID_FIELD_FOR_INSERT_UPDATE" in error_str:
                    logger.warning("Cannot set AccountId on Quote during creation due to error: %s. Trying without AccountId.", error_str)
                    quote_data_without_account = {k: v for k, v in quote_data.items() if k != "AccountId"}
                    try:
                        result = self.sf.Quote.create(quote_data_without_account)
                        quote_id = result["id"]
                        logger.info("Created Quote without AccountId: %s. Will try to update AccountId after creation.", quote_id)
                    except Exception as create_error2:
                        logger.error("Failed to create Quote even without AccountId: %s", str(create_error2))
                        raise
                else:
                    logger.error("Unexpected error creating Quote: %s", error_str)
                    raise
            
            # Ensure AccountId is set (try update if it wasn't set during creation)
            if account_id and quote_id:
                try:
                    # Check if AccountId is already set
                    quote_check = self.sf.Quote.get(quote_id)
                    if not quote_check.get("AccountId"):
                        logger.info("AccountId not set during creation, attempting to update Quote %s with AccountId %s", quote_id, account_id)
                        self.sf.Quote.update(quote_id, {"AccountId": account_id})
                        logger.info("Successfully set AccountId on Quote after creation")
                    else:
                        logger.info("AccountId already set on Quote: %s", quote_check.get("AccountId"))
                except Exception as update_error:
                    logger.warning("Could not set AccountId on Quote %s: %s. Quote created but without Account association.", quote_id, str(update_error))
            
            if not quote_id:
                raise Exception("Failed to create Quote - no quote_id returned")
            
            # Get Quote Number and verify AccountId
            quote = self.sf.Quote.get(quote_id)
            quote_number = quote.get("QuoteNumber", quote_id)
            final_account_id = quote.get("AccountId")
            if final_account_id:
                logger.info("Quote %s successfully associated with Account %s", quote_id, final_account_id)
            else:
                logger.warning("Quote %s created but AccountId is still not set. Quote Number: %s", quote_id, quote_number)
            
            # Convert legacy format to quote_items if needed
            if not quote_items or len(quote_items) == 0:
                if product_package and quantity:
                    quote_items = [{"product_package": product_package, "quantity": quantity}]
                else:
                    quote_items = []
            
            # Create Quote Line Items for each product (optional - only if Pricebook is configured)
            pricebook_id = os.environ.get("SALESFORCE_DEFAULT_PRICEBOOK_ID")
            items_created = 0
            failed_items = []  # Track items that couldn't be created as line items
            
            if pricebook_id and quote_items:
                try:
                    for item in quote_items:
                        if not isinstance(item, dict):
                            continue
                        product_package_item = item.get("product_package")
                        quantity_item = item.get("quantity", 1)
                        
                        if not product_package_item:
                            continue
                        
                        try:
                            # Find product by name (case-insensitive, partial match)
                            # First try exact match
                            escaped_name = str(product_package_item).replace("'", "''")
                            product_result = self.sf.query(
                                f"SELECT Id, Name FROM Product2 WHERE Name = '{escaped_name}' AND IsActive = true LIMIT 1"
                            )
                            
                            # If not found, try case-insensitive match
                            if product_result["totalSize"] == 0:
                                product_result = self.sf.query(
                                    f"SELECT Id, Name FROM Product2 WHERE LOWER(Name) = LOWER('{escaped_name}') AND IsActive = true LIMIT 1"
                                )
                            
                            # If still not found, try partial match (contains)
                            if product_result["totalSize"] == 0:
                                product_result = self.sf.query(
                                    f"SELECT Id, Name FROM Product2 WHERE Name LIKE '%{escaped_name}%' AND IsActive = true LIMIT 1"
                                )
                            
                            if product_result["totalSize"] > 0:
                                product_id = product_result["records"][0]["Id"]
                                actual_product_name = product_result["records"][0]["Name"]
                                logger.info("Found product: %s (searched for: %s)", actual_product_name, product_package_item)
                                
                                # Try to find pricebook entry
                                try:
                                    pricebook_entry_result = self.sf.query(
                                        f"SELECT Id, UnitPrice FROM PricebookEntry WHERE Product2Id = '{product_id}' AND Pricebook2Id = '{pricebook_id}' AND IsActive = true LIMIT 1"
                                    )
                                    
                                    if pricebook_entry_result["totalSize"] > 0:
                                        pricebook_entry_id = pricebook_entry_result["records"][0]["Id"]
                                        unit_price = pricebook_entry_result["records"][0]["UnitPrice"]
                                        
                                        # Create Quote Line Item
                                        line_item_data = {
                                            "QuoteId": quote_id,
                                            "PricebookEntryId": pricebook_entry_id,
                                            "Quantity": quantity_item,
                                            "UnitPrice": unit_price
                                        }
                                        self.sf.QuoteLineItem.create(line_item_data)
                                        items_created += 1
                                        logger.info("Created Quote Line Item for product: %s (quantity: %s)", actual_product_name, quantity_item)
                                    else:
                                        # PricebookEntry doesn't exist, try to create it
                                        logger.info("Product '%s' found but no PricebookEntry in pricebook. Attempting to create PricebookEntry.", actual_product_name)
                                        try:
                                            # Try to get Standard Price from Standard Pricebook (ID: 01s000000000000AAA for most orgs, or query for it)
                                            standard_pricebook_id = None
                                            try:
                                                standard_pricebook_result = self.sf.query(
                                                    "SELECT Id FROM Pricebook2 WHERE IsStandard = true LIMIT 1"
                                                )
                                                if standard_pricebook_result["totalSize"] > 0:
                                                    standard_pricebook_id = standard_pricebook_result["records"][0]["Id"]
                                            except:
                                                pass
                                            
                                            # Try to get price from Standard Pricebook
                                            standard_price = None
                                            if standard_pricebook_id:
                                                try:
                                                    std_entry_result = self.sf.query(
                                                        f"SELECT UnitPrice FROM PricebookEntry WHERE Product2Id = '{product_id}' AND Pricebook2Id = '{standard_pricebook_id}' AND IsActive = true LIMIT 1"
                                                    )
                                                    if std_entry_result["totalSize"] > 0:
                                                        standard_price = std_entry_result["records"][0]["UnitPrice"]
                                                except:
                                                    pass
                                            
                                            # If no standard price found, use 0 as default
                                            if standard_price is None:
                                                standard_price = 0
                                                logger.info("No standard price found for product '%s', using default price 0", actual_product_name)
                                            
                                            # Create PricebookEntry in the specified pricebook
                                            new_pricebook_entry_data = {
                                                "Product2Id": product_id,
                                                "Pricebook2Id": pricebook_id,
                                                "UnitPrice": standard_price,
                                                "IsActive": True
                                            }
                                            new_entry_result = self.sf.PricebookEntry.create(new_pricebook_entry_data)
                                            new_pricebook_entry_id = new_entry_result["id"]
                                            logger.info("Created new PricebookEntry for product '%s' in pricebook (Price: %s)", actual_product_name, standard_price)
                                            
                                            # Now create Quote Line Item with the new PricebookEntry
                                            line_item_data = {
                                                "QuoteId": quote_id,
                                                "PricebookEntryId": new_pricebook_entry_id,
                                                "Quantity": quantity_item,
                                                "UnitPrice": standard_price
                                            }
                                            self.sf.QuoteLineItem.create(line_item_data)
                                            items_created += 1
                                            logger.info("Created Quote Line Item for product: %s (quantity: %s, price: %s)", actual_product_name, quantity_item, standard_price)
                                        except Exception as create_error:
                                            logger.warning("Failed to create PricebookEntry for product '%s': %s. Will add to description.", actual_product_name, str(create_error))
                                            failed_items.append(f"{actual_product_name} (Quantity: {quantity_item})")
                                except Exception as e:
                                    # PricebookEntry query failed
                                    logger.warning("PricebookEntry query failed for product '%s': %s. Will add to description.", product_package_item, str(e))
                                    failed_items.append(f"{product_package_item} (Quantity: {quantity_item})")
                            else:
                                logger.warning("Product '%s' not found. Will add to description. Available products: %s", 
                                             product_package_item, 
                                             self._get_all_products())
                                failed_items.append(f"{product_package_item} (Quantity: {quantity_item})")
                        except Exception as e:
                            logger.warning("Failed to create Quote Line Item for product '%s': %s. Will add to description.", product_package_item, str(e))
                            failed_items.append(f"{product_package_item} (Quantity: {quantity_item})")
                    
                    if items_created > 0:
                        logger.info("Created %d Quote Line Item(s) for Quote %s", items_created, quote_id)
                    else:
                        logger.warning("No Quote Line Items were created for Quote %s. Adding products to description.", quote_id)
                except Exception as e:
                    logger.warning("Failed to create Quote Line Items: %s. Will add products to description.", str(e))
                    # If entire process failed, add all items to failed_items
                    for item in quote_items:
                        if isinstance(item, dict) and item.get("product_package"):
                            failed_items.append(f"{item.get('product_package')} (Quantity: {item.get('quantity', 1)})")
            elif not pricebook_id:
                logger.info("No Pricebook ID configured. Quote created without line items. Adding products to description.")
                # Add all items to description since we can't create line items
                for item in quote_items:
                    if isinstance(item, dict) and item.get("product_package"):
                        failed_items.append(f"{item.get('product_package')} (Quantity: {item.get('quantity', 1)})")
            elif not quote_items:
                logger.info("No quote items provided. Quote created without line items.")
            
            # If we have failed items or no line items were created, update Quote description with product information
            if failed_items or (items_created == 0 and quote_items):
                try:
                    # Get current Quote to check existing description
                    current_quote = self.sf.Quote.get(quote_id)
                    existing_description = current_quote.get("Description", "") or ""
                    
                    # Build description with product information
                    description_parts = []
                    
                    # Preserve existing description (notes) if it exists
                    if existing_description:
                        description_parts.append(existing_description)
                    
                    # Add requested products if we couldn't create line items for them
                    if failed_items:
                        if description_parts:
                            description_parts.append("")  # Add blank line separator
                        description_parts.append("Requested Products:")
                        for item in failed_items:
                            description_parts.append(f"  - {item}")
                    
                    # Add expected start date if not already in description
                    if expected_start_date and "Expected Start Date" not in existing_description:
                        if description_parts:
                            description_parts.append("")  # Add blank line separator
                        description_parts.append(f"Expected Start Date: {expected_start_date}")
                    
                    updated_description = "\n".join(description_parts)
                    
                    # Only update if we have something to add
                    if failed_items or (items_created == 0 and quote_items):
                        update_data = {"Description": updated_description}
                        self.sf.Quote.update(quote_id, update_data)
                        logger.info("Updated Quote %s description with product information: %s", quote_id, updated_description[:200])
                except Exception as e:
                    logger.warning("Failed to update Quote description with product information: %s", str(e))
            
            # Generate Quote URL
            quote_url = f"{self.instance_url}/lightning/r/Quote/{quote_id}/view"
            
            # Final verification - get Quote details to log
            final_quote = self.sf.Quote.get(quote_id)
            logger.info("Created Quote: %s (ID: %s, AccountId: %s, OpportunityId: %s)", 
                       quote_number, quote_id, 
                       final_quote.get("AccountId", "None"),
                       final_quote.get("OpportunityId", "None"))
            
            return {
                "quote_id": quote_id,
                "quote_number": quote_number,
                "quote_url": quote_url
            }
            
        except Exception as e:
            logger.error("Failed to create Quote in Salesforce: %s", str(e))
            import traceback
            logger.error("Traceback: %s", traceback.format_exc())
            return None


# Global instance
_salesforce_service: Optional[SalesforceService] = None


def get_salesforce_service() -> SalesforceService:
    """Get or create Salesforce service instance."""
    global _salesforce_service
    if _salesforce_service is None:
        _salesforce_service = SalesforceService()
    return _salesforce_service

