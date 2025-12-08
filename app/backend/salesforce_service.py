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
        Create or get an Account by name.
        
        Args:
            customer_name: Name of the customer/account
            contact_info: Contact information (email or phone)
            
        Returns:
            Account ID if successful, None otherwise
        """
        if not self.is_available():
            return None

        try:
            # Try to find existing account
            result = self.sf.query(
                f"SELECT Id, Name FROM Account WHERE Name = '{customer_name.replace("'", "''")}' LIMIT 1"
            )
            
            if result["totalSize"] > 0:
                account_id = result["records"][0]["Id"]
                logger.info("Found existing Account: %s (ID: %s)", customer_name, account_id)
                return account_id
            
            # Create new account
            account_data = {
                "Name": customer_name,
                "Type": "Customer"
            }
            
            # Try to extract email or phone from contact_info
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
            return None

    def create_or_get_contact(self, account_id: str, customer_name: str, contact_info: str) -> Optional[str]:
        """
        Create or get a Contact for the account.
        
        Args:
            account_id: Account ID
            customer_name: Customer name
            contact_info: Contact information (email or phone)
            
        Returns:
            Contact ID if successful, None otherwise
        """
        if not self.is_available():
            return None

        try:
            # Try to find existing contact
            email_filter = f"Email = '{contact_info.replace("'", "''")}'" if "@" in contact_info else None
            phone_filter = f"Phone = '{contact_info.replace("'", "''")}'" if email_filter is None else None
            
            query_filter = email_filter or phone_filter or "1=0"
            result = self.sf.query(
                f"SELECT Id, Name, Email FROM Contact WHERE AccountId = '{account_id}' AND ({query_filter}) LIMIT 1"
            )
            
            if result["totalSize"] > 0:
                contact_id = result["records"][0]["Id"]
                logger.info("Found existing Contact: %s (ID: %s)", customer_name, contact_id)
                return contact_id
            
            # Create new contact
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
        account_id: str,
        opportunity_id: Optional[str],
        customer_name: str,
        product_package: str,
        quantity: int,
        expected_start_date: Optional[str] = None,
        notes: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Create a Quote in Salesforce.
        
        Args:
            account_id: Account ID
            opportunity_id: Opportunity ID (optional)
            customer_name: Customer name
            product_package: Product or package name
            quantity: Quantity
            expected_start_date: Expected start date (YYYY-MM-DD)
            notes: Additional notes
            
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
            
            # Try to set AccountId, but handle permission errors gracefully
            try:
                quote_data["AccountId"] = account_id
            except Exception:
                logger.warning("Cannot set AccountId on Quote, will create without Account association")
            
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
            try:
                result = self.sf.Quote.create(quote_data)
                quote_id = result["id"]
            except Exception as create_error:
                # If AccountId permission error, try without it
                if "AccountId" in str(create_error) or "INVALID_FIELD_FOR_INSERT_UPDATE" in str(create_error):
                    logger.warning("Cannot set AccountId on Quote due to permissions, creating without Account association")
                    quote_data_without_account = {k: v for k, v in quote_data.items() if k != "AccountId"}
                    result = self.sf.Quote.create(quote_data_without_account)
                    quote_id = result["id"]
                    # Try to update AccountId after creation (sometimes update works when create doesn't)
                    try:
                        self.sf.Quote.update(quote_id, {"AccountId": account_id})
                        logger.info("Successfully set AccountId on Quote after creation")
                    except Exception:
                        logger.warning("Could not set AccountId on Quote even after creation. Quote created without Account association.")
                else:
                    raise
            
            quote_id = result["id"]
            
            # Get Quote Number
            quote = self.sf.Quote.get(quote_id)
            quote_number = quote.get("QuoteNumber", quote_id)
            
            # Create Quote Line Item (optional - only if Pricebook is configured)
            pricebook_id = os.environ.get("SALESFORCE_DEFAULT_PRICEBOOK_ID")
            if pricebook_id:
                try:
                    # Find product by name (case-insensitive, partial match)
                    # First try exact match
                    escaped_name = product_package.replace("'", "''")
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
                        logger.info("Found product: %s (searched for: %s)", actual_product_name, product_package)
                        
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
                                    "Quantity": quantity,
                                    "UnitPrice": unit_price
                                }
                                self.sf.QuoteLineItem.create(line_item_data)
                                logger.info("Created Quote Line Item for product: %s", actual_product_name)
                            else:
                                logger.warning("Product '%s' found but no PricebookEntry in pricebook. Make sure product is added to the pricebook. Quote created without line items.", actual_product_name)
                        except Exception as e:
                            # PricebookEntry object might not be available
                            logger.warning("PricebookEntry not available or query failed: %s. Quote created without line items.", str(e))
                    else:
                        logger.warning("Product '%s' not found. Available products: %s. Quote created without line items.", 
                                     product_package, 
                                     self._get_all_products())
                except Exception as e:
                    logger.warning("Failed to create Quote Line Item: %s. Quote created without line items.", str(e))
            else:
                logger.info("No Pricebook ID configured. Quote created without line items. This is OK - you can add line items manually in Salesforce.")
            
            # Generate Quote URL
            quote_url = f"{self.instance_url}/lightning/r/Quote/{quote_id}/view"
            
            logger.info("Created Quote: %s (ID: %s)", quote_number, quote_id)
            
            return {
                "quote_id": quote_id,
                "quote_number": quote_number,
                "quote_url": quote_url
            }
            
        except Exception as e:
            logger.error("Failed to create Quote: %s", str(e))
            return None


# Global instance
_salesforce_service: Optional[SalesforceService] = None


def get_salesforce_service() -> SalesforceService:
    """Get or create Salesforce service instance."""
    global _salesforce_service
    if _salesforce_service is None:
        _salesforce_service = SalesforceService()
    return _salesforce_service

