"""
Microsoft Teams Calling Bot integration for VoiceRAG
Adapted from MSAgent for async aiohttp usage
"""
import base64
import json
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

import aiohttp
from msal import ConfidentialClientApplication

logger = logging.getLogger("voicerag")

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)


def decode_tid(token: str) -> str:
    """Decode tenant ID from access token."""
    payload = token.split(".")[1]
    padding = "=" * (-len(payload) % 4)
    decoded = base64.urlsafe_b64decode(payload + padding)
    data = json.loads(decoded)
    return data.get("tid")


class TeamsCaller:
    """Microsoft Teams Calling Bot integration for VoiceRAG"""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        bot_app_id: Optional[str] = None,
        bot_display_name: str = "VoiceRAG Bot",
    ):
        """
        Initialize Teams Caller

        Args:
            tenant_id: Azure AD tenant ID
            client_id: Azure AD application client ID
            client_secret: Azure AD application client secret
            bot_app_id: Calling Bot app ID (defaults to client_id if not provided)
            bot_display_name: Display name for the bot in calls
        """
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.bot_app_id = bot_app_id or client_id
        self.bot_display_name = bot_display_name

        self.authority = f"https://login.microsoftonline.com/{tenant_id}"
        self.scope = ["https://graph.microsoft.com/.default"]
        self.graph_endpoint = "https://graph.microsoft.com/v1.0"
        self.access_token: Optional[str] = None
        self._token_provider = None

        # Initialize MSAL app
        self._msal_app = ConfidentialClientApplication(
            client_id=self.client_id,
            client_credential=self.client_secret,
            authority=self.authority,
        )

    def get_access_token(self) -> str:
        """Get access token (client credential / app-only)"""
        result = self._msal_app.acquire_token_for_client(scopes=self.scope)

        if "access_token" in result:
            self.access_token = result["access_token"]
            logger.debug("Access token acquired, tid = %s", decode_tid(self.access_token))
            return self.access_token

        error_msg = result.get("error_description", result.get("error", "Unknown error"))
        raise Exception(f"Failed to acquire access token: {error_msg}")

    def _headers(self) -> Dict[str, str]:
        """Get HTTP headers with authorization"""
        if not self.access_token:
            self.get_access_token()
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _try_parse_graph_error(resp: aiohttp.ClientResponse) -> str:
        """Extract error information from Graph API error response"""
        try:
            # This will be called after response is read, so we need the response text
            # The caller should pass response_data instead
            return "Graph API error"
        except Exception:
            return "Unknown error"

    def _get_callback_uri(self, callback_uri: Optional[str]) -> str:
        """Validate callback URI"""
        cb = (callback_uri or os.getenv("TEAMS_CALLBACK_URL") or "").strip()
        if not cb:
            raise Exception(
                "Please set TEAMS_CALLBACK_URL environment variable, "
                "e.g., https://xxxx.ngrok-free.app/api/teams/calls"
            )
        if not cb.lower().startswith("https://"):
            raise Exception(f"TEAMS_CALLBACK_URL must start with https://, got: {cb}")
        return cb

    async def resolve_user_to_object_id(
        self, user_input: str, session: Optional[aiohttp.ClientSession] = None
    ) -> Tuple[str, Optional[str]]:
        """
        Resolve user input (GUID or UPN) to Azure AD user object ID
        Returns (objectId, displayName)
        """
        user_input = user_input.strip()
        if UUID_RE.match(user_input):
            return user_input, None

        # Treat as UPN/email, query Graph API
        url = f"{self.graph_endpoint}/users/{user_input}?$select=id,displayName,userPrincipalName"

        if session:
            async with session.get(url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status >= 400:
                    error_text = await resp.text()
                    raise Exception(f"Failed to resolve user (UPN to objectId): HTTP {resp.status} - {error_text}")

                data = await resp.json()
                return data["id"], data.get("displayName")
        else:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status >= 400:
                        error_text = await resp.text()
                        raise Exception(f"Failed to resolve user (UPN to objectId): HTTP {resp.status} - {error_text}")

                    data = await resp.json()
                    return data["id"], data.get("displayName")

    def _build_source_application(self) -> Dict[str, Any]:
        """Build source application identity for app-only scenario"""
        return {
            "@odata.type": "#microsoft.graph.participantInfo",
            "identity": {
                "@odata.type": "#microsoft.graph.identitySet",
                "application": {
                    "@odata.type": "#microsoft.graph.identity",
                    "id": self.bot_app_id,
                    "displayName": self.bot_display_name,
                },
            },
        }

    async def make_call(
        self, to_phone_number: str, callback_uri: Optional[str] = None, session: Optional[aiohttp.ClientSession] = None
    ) -> Dict[str, Any]:
        """Make a call to a phone number (PSTN)"""
        cb = self._get_callback_uri(callback_uri)

        call_data = {
            "@odata.type": "#microsoft.graph.call",
            "direction": "outgoing",
            "callbackUri": cb,
            "tenantId": self.tenant_id,
            "source": self._build_source_application(),
            "targets": [
                {
                    "@odata.type": "#microsoft.graph.invitationParticipantInfo",
                    "identity": {
                        "@odata.type": "#microsoft.graph.identitySet",
                        "phone": {
                            "@odata.type": "#microsoft.graph.identity",
                            "id": to_phone_number,
                        },
                    },
                }
            ],
            "requestedModalities": ["audio"],
            "mediaConfig": {"@odata.type": "#microsoft.graph.serviceHostedMediaConfig"},
        }

        url = f"{self.graph_endpoint}/communications/calls"
        if session:
            async with session.post(url, headers=self._headers(), json=call_data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status >= 400:
                    error_text = await resp.text()
                    raise Exception(f"Failed to make call: HTTP {resp.status} - {error_text}")
                return await resp.json()
        else:
            async with aiohttp.ClientSession() as sess:
                async with sess.post(url, headers=self._headers(), json=call_data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status >= 400:
                        error_text = await resp.text()
                        raise Exception(f"Failed to make call: HTTP {resp.status} - {error_text}")
                    return await resp.json()

    async def make_call_to_teams_user(
        self, to_user_input: str, callback_uri: Optional[str] = None, session: Optional[aiohttp.ClientSession] = None
    ) -> Dict[str, Any]:
        """Make a call to a Teams user (input can be UPN or objectId)"""
        cb = self._get_callback_uri(callback_uri)
        target_object_id, _target_name = await self.resolve_user_to_object_id(to_user_input, session)

        call_data = {
            "@odata.type": "#microsoft.graph.call",
            "direction": "outgoing",
            "callbackUri": cb,
            "tenantId": self.tenant_id,
            "source": self._build_source_application(),
            "targets": [
                {
                    "@odata.type": "#microsoft.graph.invitationParticipantInfo",
                    "identity": {
                        "@odata.type": "#microsoft.graph.identitySet",
                        "user": {
                            "@odata.type": "#microsoft.graph.identity",
                            "id": target_object_id,
                        },
                    },
                }
            ],
            "requestedModalities": ["audio"],
            "mediaConfig": {"@odata.type": "#microsoft.graph.serviceHostedMediaConfig"},
        }

        url = f"{self.graph_endpoint}/communications/calls"
        if session:
            async with session.post(url, headers=self._headers(), json=call_data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status >= 400:
                    error_text = await resp.text()
                    raise Exception(f"Failed to call Teams user: HTTP {resp.status} - {error_text}")
                return await resp.json()
        else:
            async with aiohttp.ClientSession() as sess:
                async with sess.post(url, headers=self._headers(), json=call_data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status >= 400:
                        error_text = await resp.text()
                        raise Exception(f"Failed to call Teams user: HTTP {resp.status} - {error_text}")
                    return await resp.json()

    async def get_call_status(self, call_id: str, session: Optional[aiohttp.ClientSession] = None) -> Dict[str, Any]:
        """Get call status"""
        url = f"{self.graph_endpoint}/communications/calls/{call_id}"
        if session:
            async with session.get(url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status >= 400:
                    error_text = await resp.text()
                    raise Exception(f"Failed to get call status: HTTP {resp.status} - {error_text}")
                return await resp.json()
        else:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status >= 400:
                        error_text = await resp.text()
                        raise Exception(f"Failed to get call status: HTTP {resp.status} - {error_text}")
                    return await resp.json()

    async def end_call(self, call_id: str, session: Optional[aiohttp.ClientSession] = None) -> bool:
        """End a call"""
        url = f"{self.graph_endpoint}/communications/calls/{call_id}"
        if session:
            async with session.delete(url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status >= 400:
                    error_text = await resp.text()
                    raise Exception(f"Failed to end call: HTTP {resp.status} - {error_text}")
                return True
        else:
            async with aiohttp.ClientSession() as sess:
                async with sess.delete(url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status >= 400:
                        error_text = await resp.text()
                        raise Exception(f"Failed to end call: HTTP {resp.status} - {error_text}")
                    return True

