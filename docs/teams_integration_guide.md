# Teams Integration Guide for VoiceRAG

## Overview

This document describes how to integrate the VoiceRAG application with Microsoft Teams, enabling users to interact with the knowledge base through Teams channels or personal chats.

## Integration Architecture

The VoiceRAG application is currently designed as a web application with real-time voice interaction through WebSockets. To integrate with Teams, we need to extend the backend to support synchronous HTTP-based interaction patterns typical of Teams bots.

### Current Architecture

```
Web Frontend (React)
    ↓ WebSocket
VoiceRAG Backend (aiohttp)
    ↓ Tools API
Azure OpenAI Realtime API + Azure AI Search
```

### Teams Integration Architecture

```
Microsoft Teams
    ↓ Teams Bot Framework
Azure Bot Service (Optional)
    ↓ HTTP API
VoiceRAG Backend (Extended)
    ↓ Tools API
Azure OpenAI API + Azure AI Search
```

## Integration Options

### Option 1: Azure Bot Framework (Recommended)

**Pros:**
- Native Teams integration with full UI support
- Handles Teams-specific authentication and permissions
- Supports rich cards, adaptive cards, and interactive elements
- Scales automatically with Teams usage
- Provides analytics and monitoring

**Cons:**
- Requires additional Azure Bot Service resource
- More complex initial setup
- Requires Teams app deployment

**Architecture:**
```
Teams Client
    ↓ Bot Framework SDK
Azure Bot Service
    ↓ Teams Channel Connector
VoiceRAG Backend (HTTP API)
    ↓ Python Client
Azure OpenAI + AI Search
```

### Option 2: Incoming Webhook (Simple)

**Pros:**
- Easiest to implement (minutes to hours)
- No additional Azure resources required
- Direct integration with existing backend
- Can be deployed via SharePoint or Admin Panel

**Cons:**
- Limited to one-way communication (Teams → VoiceRAG)
- No rich UI elements in Teams
- Manual response posting required
- Lacks conversational context management
- Authentication needs custom handling

**Architecture:**
```
Teams Channel
    ↓ Incoming Webhook (HTTP POST)
VoiceRAG Backend (HTTP Endpoint)
    ↓ Process Query
Azure OpenAI + AI Search
    ↓ Return Response
Teams Webhook (Incoming) or Bot Reply
```

### Option 3: Messaging Extension

**Pros:**
- Native Teams UX with custom search interface
- Appears in Teams compose box
- Can return rich adaptive cards
- Users can share results in conversations

**Cons:**
- More frontend development required
- Requires Teams app manifest configuration
- Limited to search/query scenarios

**Use Case:** Users invoke knowledge base search from Teams compose box and share results as cards.

## Implementation Steps

### Phase 1: Backend API Extension

The current VoiceRAG backend uses WebSocket for real-time voice interaction. For Teams integration, we need to add synchronous HTTP endpoints.

#### Required Changes to `app/backend/app.py`

1. **Add HTTP Chat Endpoint**

```python
@app.post("/api/chat")
async def api_chat(request: Request):
    """Synchronous chat endpoint for Teams integration."""
    try:
        data = await request.json()
        user_message = data.get("message", "")
        session_id = data.get("session_id", "teams_default")
        
        # Initialize or retrieve conversation context
        context = get_or_create_context(session_id)
        
        # Create tools for search and grounding
        tools = [
            create_search_tool(),
            create_grounding_tool()
        ]
        
        # Call OpenAI Chat Completion API (synchronous)
        from openai import AzureOpenAI
        client = AzureOpenAI(
            api_key=os.environ.get("AZURE_OPENAI_API_KEY"),
            api_version="2024-02-15-preview",
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"]
        )
        
        messages = context["messages"] + [
            {"role": "user", "content": user_message}
        ]
        
        # Call with function calling
        response = client.chat.completions.create(
            model=os.environ["AZURE_OPENAI_REALTIME_DEPLOYMENT"],
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )
        
        # Process tool calls
        result = process_tool_calls(response, tools)
        
        # Update context
        context["messages"].append({"role": "assistant", "content": result["answer"]})
        
        return json_response({
            "answer": result["answer"],
            "sources": result.get("sources", []),
            "session_id": session_id
        })
        
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return json_response({"error": str(e)}, status=500)
```

2. **Add Session Context Management**

```python
# In-memory session storage (use Redis for production)
conversation_contexts = {}

def get_or_create_context(session_id: str) -> dict:
    """Retrieve or create conversation context for a session."""
    if session_id not in conversation_contexts:
        conversation_contexts[session_id] = {
            "messages": [{
                "role": "system",
                "content": rtmt.system_message
            }]
        }
    return conversation_contexts[session_id]
```

3. **Add Search Tool for HTTP Context**

```python
def create_search_tool():
    """Create search tool definition for OpenAI function calling."""
    return {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search the knowledge base for relevant information",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query"
                    }
                },
                "required": ["query"]
            }
        }
    }

async def execute_search_tool(query: str) -> dict:
    """Execute search against Azure AI Search."""
    # Use existing ragtools.SearchClient logic
    from backend.ragtools import SearchClient
    search_client = SearchClient()
    results = await search_client.search(query)
    return results
```

#### Required Dependencies

Add to `app/backend/requirements.txt`:

```
azure-ai-openai>=1.0.0  # For Chat Completions API
```

### Phase 2: Teams Bot Implementation

#### Option A: Azure Bot Service (Production)

1. **Create Azure Bot Resource**

```bash
az bot create \
  --resource-group <your-resource-group> \
  --name voice-rag-teams-bot \
  --appid <app-id> \
  --app-type MultiTenant \
  --location eastus
```

2. **Create Teams App Manifest**

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/teams/v1.16/MicrosoftTeams.schema.json",
  "manifestVersion": "1.16",
  "id": "<bot-id>",
  "version": "1.0.0",
  "packageName": "com.company.voicerag",
  "developer": {
    "name": "Your Company",
    "websiteUrl": "https://yourcompany.com",
    "privacyUrl": "https://yourcompany.com/privacy"
  },
  "name": {
    "short": "VoiceRAG Bot",
    "full": "VoiceRAG Knowledge Assistant"
  },
  "description": {
    "short": "Ask questions about your knowledge base",
    "full": "VoiceRAG Bot helps you find information from your knowledge base using natural language."
  },
  "icons": {
    "color": "icon-color.png",
    "outline": "icon-outline.png"
  },
  "accentColor": "#0078D4",
  "bots": [
    {
      "botId": "<bot-id>",
      "scopes": ["personal", "team", "groupchat"],
      "commandLists": [
        {
          "scopes": ["personal", "team", "groupchat"],
          "commands": [
            {
              "title": "Ask",
              "description": "Ask a question about the knowledge base"
            },
            {
              "title": "Search",
              "description": "Search for information"
            }
          ]
        }
      ],
      "supportsFiles": false,
      "isNotificationOnly": false
    }
  ],
  "validDomains": ["<your-backend-domain>"]
}
```

3. **Bot Code (Python)**

```python
from botbuilder.core import ActivityHandler, TurnContext
from botbuilder.schema import ChannelAccount, Activity

class VoiceRAGBot(ActivityHandler):
    def __init__(self):
        self.backend_url = "https://<your-backend-url>/api/chat"
    
    async def on_message_activity(self, turn_context: TurnContext):
        """Handle incoming messages."""
        user_message = turn_context.activity.text
        session_id = f"teams_{turn_context.activity.from_property.id}"
        
        # Call VoiceRAG backend
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.backend_url,
                json={"message": user_message, "session_id": session_id}
            )
            result = response.json()
        
        # Send response back to Teams
        await turn_context.send_activity(result["answer"])
        
        # If sources are provided, send them as cards
        if result.get("sources"):
            await self.send_source_cards(turn_context, result["sources"])
    
    async def send_source_cards(self, turn_context: TurnContext, sources: list):
        """Send grounding sources as adaptive cards."""
        for source in sources:
            card = self.create_source_card(source)
            await turn_context.send_activity(
                MessageFactory.attachment(card)
            )
```

4. **Deploy to Teams**

```bash
# Package Teams app
zip -r voice-rag-teams-app.zip manifest.json icons/

# Upload via Teams Admin Center or App Studio
# Or distribute via Teams App Catalog
```

#### Option B: Incoming Webhook (Quick Start)

1. **Create Webhook in Teams**

   - Go to Teams channel → Connectors → Incoming Webhook
   - Configure name and icon
   - Copy webhook URL

2. **Add Webhook Endpoint to Backend**

```python
@app.post("/api/teams/webhook")
async def teams_webhook(request: Request):
    """Handle Teams incoming webhook."""
    try:
        data = await request.json()
        user_message = data.get("text", "")
        user_name = data.get("username", "Unknown")
        
        # Process with existing chat logic
        result = await process_chat_message(
            message=user_message,
            session_id=f"teams_{user_name}"
        )
        
        # Post response back to Teams
        webhook_url = data.get("response_url")
        await post_to_teams(webhook_url, {
            "text": result["answer"],
            "response_type": "in_channel"
        })
        
        return json_response({"status": "ok"})
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return json_response({"error": str(e)}, status=500)
```

3. **Configure Teams Connector**

   - Paste VoiceRAG backend URL as webhook endpoint
   - Test by posting a message in the channel

### Phase 3: Authentication and Security

#### Entra ID Integration

The application already uses Azure Managed Identity. For Teams integration:

1. **Enable Teams Channel in Bot Service**

```bash
az bot teams create \
  --resource-group <resource-group> \
  --name <bot-name>
```

2. **Add Required Permissions**

Teams bot needs:
- `chat.readwrite` - Read and send messages
- `offline_access` - Maintain session context

3. **Configure Bot Authentication**

```python
# In bot configuration
from botframework.connector.auth import SimpleCredentialProvider

# Use existing VoiceRAG credentials
credential_provider = SimpleCredentialProvider(
    app_id=os.environ["MICROSOFT_APP_ID"],
    app_password=os.environ["MICROSOFT_APP_PASSWORD"]
)
```

## Deployment Considerations

### Cost Impact

Teams integration adds minimal cost:
- **Azure Bot Service**: Free tier available (10K messages/month)
- **No additional AI costs**: Reuses existing OpenAI and Search resources
- **Minimal compute**: HTTP endpoints are lightweight

### Performance

- **Latency**: HTTP-based interaction is slower than WebSocket (typically 1-3 seconds)
- **Concurrency**: Bot Service handles concurrent Teams users
- **Session Management**: Use Redis for production (not in-memory dict)

### Monitoring

Add logging for Teams interactions:

```python
logger.info("Teams message received", extra={
    "user": session_id,
    "message_length": len(user_message),
    "sources_count": len(result.get("sources", []))
})
```

## Testing

### Local Testing

1. **Start Backend**: `python app/backend/app.py`
2. **Use Teams Tunneling**: 
   ```bash
   ngrok http 8765
   # Configure bot with ngrok URL
   ```
3. **Test in Teams**: Install bot in your Teams app

### Production Testing

1. Deploy backend with new endpoints
2. Create Teams app package
3. Upload to Teams App Catalog or distribute to users
4. Monitor Azure Application Insights for errors

## Limitations and Future Work

### Current Limitations

1. **No Voice Support in Teams**: Teams bot only supports text (no real-time audio)
2. **Session Context**: Limited by Teams bot session lifetime
3. **Grounding Files Display**: Not directly viewable in Teams (links provided instead)


### Future Enhancements

1. **Adaptive Cards for Rich UI**: Display grounding sources as cards
2. **Message Extensions**: Add search bar in Teams compose
3. **Tab Integration**: Add knowledge base as Teams tab
4. **File Upload in Teams**: Allow users to upload files directly to knowledge base from Teams
5. **Voice Notes Support**: Process Teams voice notes using Azure Speech Service

## References

- [Azure Bot Service Documentation](https://learn.microsoft.com/en-us/azure/bot-service/)
- [Teams Bot Framework Documentation](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/what-are-bots)
- [Adaptive Cards](https://adaptivecards.io/)
- [VoiceRAG Project Repository](https://github.com/your-repo/voicerag)

## Support
For issues or questions:
- Backend issues: Check `app/backend/app.py` logs
- Teams integration: Check Azure Bot Service logs in Azure Portal
- Authentication: Verify App ID and App Password in Azure Entra ID



