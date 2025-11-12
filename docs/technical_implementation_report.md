# 技术实施报告：Voice Agent 功能扩展方案

**项目名称**: Voice-Agent 企业级功能增强  
**日期**: 2025年11月10日  
**版本**: 1.0  
**受众**: George Fethers & Co. 技术团队

---

## 执行摘要

本报告针对Tim Perkins提出的三项核心需求，提供详细的技术实施方案。基于现有的Voice-Agent架构（Azure OpenAI GPT-4o Realtime API + RAG），我们将扩展以下功能：

1. **转录摘要与CRM集成**：自动生成对话摘要并同步至CRM系统
2. **人工客服转接**：智能判断并实现实时通话转接
3. **报价单生成与发送**：自动化报价流程与邮件发送

所有方案基于现有架构，采用模块化设计，确保可维护性和可扩展性。

---

## 1. 需求分析

### 1.1 需求一：转录摘要与CRM集成

**业务目标**：
- 自动记录客户对话内容
- 生成结构化摘要
- 将关键信息更新至CRM系统
- 支持通过邮件转发摘要

**技术要求**：
- 实时转录存储
- GPT-4o智能摘要生成
- CRM API集成（支持Salesforce、HubSpot、Dynamics 365等）
- SMTP邮件发送
- 数据格式化与字段映射

### 1.2 需求二：人工客服转接

**业务目标**：
- AI识别需要人工介入的场景
- 无缝转接至人工客服
- 保持对话上下文

**技术要求**：
- 转接触发逻辑（AI判断或用户请求）
- 电话系统集成（Twilio、Azure Communication Services等）
- 会话状态管理
- 转接后的上下文传递

### 1.3 需求三：报价单生成与发送

**业务目标**：
- AI收集报价所需信息
- 自动生成专业报价单
- 通过邮件发送给客户

**技术要求**：
- 信息采集与验证
- 报价单模板引擎
- PDF生成
- 邮件发送与追踪

---

## 2. 系统架构设计

### 2.1 现有架构分析

当前系统采用以下技术栈：

```
Frontend (React + TypeScript)
    ↕ WebSocket
Backend (Python aiohttp)
    ├─ RTMiddleTier (实时中间层)
    ├─ RAGTools (Azure AI Search)
    └─ Azure OpenAI GPT-4o Realtime API
```

**核心组件**：
- `app.py`: 主应用入口
- `rtmt.py`: 实时中间层，处理WebSocket通信
- `ragtools.py`: RAG工具，提供知识库搜索
- **Tool System**: 可扩展的工具调用框架

### 2.2 扩展架构设计

我们将新增以下模块：

```
app/backend/
├── integrations/
│   ├── __init__.py
│   ├── crm_integration.py      # CRM集成
│   ├── telephony.py             # 电话系统集成
│   └── email_service.py         # 邮件服务
├── services/
│   ├── __init__.py
│   ├── transcript_service.py    # 转录管理
│   ├── summary_service.py       # 摘要生成
│   ├── quote_service.py         # 报价单生成
│   └── handoff_service.py       # 转接管理
└── tools/
    ├── crm_tools.py             # CRM工具
    ├── handoff_tools.py         # 转接工具
    └── quote_tools.py           # 报价工具
```

---

## 3. 详细实施方案

## 3.1 功能一：转录摘要与CRM集成

### 3.1.1 技术方案

**组件架构**：

```
用户对话 → WebSocket → 转录存储 → 摘要生成 → CRM同步 + 邮件发送
```

**实施步骤**：

#### Step 1: 转录存储服务

创建 `app/backend/services/transcript_service.py`：

```python
from datetime import datetime
from typing import List, Dict
import json

class TranscriptService:
    def __init__(self):
        self.sessions = {}  # 内存存储，生产环境建议用Redis
    
    def start_session(self, session_id: str) -> None:
        """开始新的对话会话"""
        self.sessions[session_id] = {
            "id": session_id,
            "start_time": datetime.utcnow().isoformat(),
            "messages": [],
            "metadata": {}
        }
    
    def add_message(self, session_id: str, role: str, content: str) -> None:
        """添加对话消息"""
        if session_id in self.sessions:
            self.sessions[session_id]["messages"].append({
                "role": role,  # 'user' or 'assistant'
                "content": content,
                "timestamp": datetime.utcnow().isoformat()
            })
    
    def get_transcript(self, session_id: str) -> Dict:
        """获取完整转录"""
        return self.sessions.get(session_id, {})
    
    def end_session(self, session_id: str) -> None:
        """结束会话"""
        if session_id in self.sessions:
            self.sessions[session_id]["end_time"] = datetime.utcnow().isoformat()
```

#### Step 2: 智能摘要生成

创建 `app/backend/services/summary_service.py`：

```python
from azure.ai.inference import ChatCompletionsClient
from azure.core.credentials import AzureKeyCredential

class SummaryService:
    def __init__(self, endpoint: str, credential):
        self.client = ChatCompletionsClient(endpoint, credential)
    
    async def generate_summary(self, transcript: Dict) -> Dict:
        """
        使用GPT-4生成结构化摘要
        返回: {
            "summary": "简短摘要",
            "key_points": ["要点1", "要点2"],
            "customer_info": {...},
            "action_items": [...],
            "sentiment": "positive/neutral/negative"
        }
        """
        messages = transcript.get("messages", [])
        conversation_text = "\n".join([
            f"{msg['role']}: {msg['content']}" 
            for msg in messages
        ])
        
        prompt = f"""
        分析以下客户对话并生成结构化摘要。返回JSON格式：
        
        对话内容：
        {conversation_text}
        
        请提取：
        1. 对话简短摘要（1-2句话）
        2. 关键要点（列表）
        3. 客户信息（姓名、联系方式、公司等）
        4. 待办事项
        5. 情绪分析
        
        JSON格式输出。
        """
        
        response = await self.client.complete(
            messages=[{"role": "user", "content": prompt}],
            model="gpt-4",
            temperature=0.3
        )
        
        return json.loads(response.choices[0].message.content)
```

#### Step 3: CRM集成

创建 `app/backend/integrations/crm_integration.py`：

```python
from abc import ABC, abstractmethod
from typing import Dict, Optional
import aiohttp

class CRMIntegration(ABC):
    @abstractmethod
    async def update_contact(self, contact_id: str, data: Dict) -> bool:
        pass
    
    @abstractmethod
    async def create_activity(self, contact_id: str, activity_data: Dict) -> bool:
        pass

class SalesforceCRM(CRMIntegration):
    def __init__(self, instance_url: str, access_token: str):
        self.instance_url = instance_url
        self.access_token = access_token
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
    
    async def update_contact(self, contact_id: str, data: Dict) -> bool:
        """更新Salesforce联系人"""
        async with aiohttp.ClientSession() as session:
            url = f"{self.instance_url}/services/data/v58.0/sobjects/Contact/{contact_id}"
            async with session.patch(url, json=data, headers=self.headers) as resp:
                return resp.status == 204
    
    async def create_activity(self, contact_id: str, activity_data: Dict) -> bool:
        """创建活动记录"""
        async with aiohttp.ClientSession() as session:
            url = f"{self.instance_url}/services/data/v58.0/sobjects/Task"
            task = {
                "WhoId": contact_id,
                "Subject": activity_data.get("subject", "AI Voice Call"),
                "Description": activity_data.get("description"),
                "Status": "Completed",
                "ActivityDate": activity_data.get("date")
            }
            async with session.post(url, json=task, headers=self.headers) as resp:
                return resp.status == 201

class HubSpotCRM(CRMIntegration):
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.hubapi.com"
    
    async def update_contact(self, contact_id: str, data: Dict) -> bool:
        """更新HubSpot联系人"""
        async with aiohttp.ClientSession() as session:
            url = f"{self.base_url}/crm/v3/objects/contacts/{contact_id}"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            payload = {"properties": data}
            async with session.patch(url, json=payload, headers=headers) as resp:
                return resp.status == 200
    
    async def create_activity(self, contact_id: str, activity_data: Dict) -> bool:
        """创建活动记录"""
        async with aiohttp.ClientSession() as session:
            url = f"{self.base_url}/crm/v3/objects/notes"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            payload = {
                "properties": {
                    "hs_note_body": activity_data.get("description"),
                    "hs_timestamp": activity_data.get("timestamp")
                },
                "associations": [{
                    "to": {"id": contact_id},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", 
                              "associationTypeId": 202}]
                }]
            }
            async with session.post(url, json=payload, headers=headers) as resp:
                return resp.status == 201

class CRMFactory:
    @staticmethod
    def create(crm_type: str, **kwargs) -> CRMIntegration:
        if crm_type.lower() == "salesforce":
            return SalesforceCRM(kwargs["instance_url"], kwargs["access_token"])
        elif crm_type.lower() == "hubspot":
            return HubSpotCRM(kwargs["api_key"])
        else:
            raise ValueError(f"Unsupported CRM type: {crm_type}")
```

#### Step 4: 邮件服务

创建 `app/backend/integrations/email_service.py`：

```python
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional

class EmailService:
    def __init__(self, smtp_host: str, smtp_port: int, username: str, password: str):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
    
    async def send_summary(
        self, 
        to_email: str, 
        subject: str, 
        summary: Dict,
        cc: Optional[List[str]] = None
    ) -> bool:
        """发送对话摘要邮件"""
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = self.username
        message["To"] = to_email
        if cc:
            message["Cc"] = ", ".join(cc)
        
        # 生成HTML邮件内容
        html_content = self._generate_summary_html(summary)
        html_part = MIMEText(html_content, "html")
        message.attach(html_part)
        
        try:
            await aiosmtplib.send(
                message,
                hostname=self.smtp_host,
                port=self.smtp_port,
                username=self.username,
                password=self.password,
                use_tls=True
            )
            return True
        except Exception as e:
            print(f"Email sending failed: {e}")
            return False
    
    def _generate_summary_html(self, summary: Dict) -> str:
        """生成摘要邮件HTML"""
        return f"""
        <html>
        <body style="font-family: Arial, sans-serif;">
            <h2>对话摘要</h2>
            <p><strong>摘要:</strong> {summary.get('summary', 'N/A')}</p>
            
            <h3>关键要点:</h3>
            <ul>
                {''.join([f'<li>{point}</li>' for point in summary.get('key_points', [])])}
            </ul>
            
            <h3>客户信息:</h3>
            <pre>{json.dumps(summary.get('customer_info', {}), indent=2)}</pre>
            
            <h3>待办事项:</h3>
            <ul>
                {''.join([f'<li>{item}</li>' for item in summary.get('action_items', [])])}
            </ul>
            
            <p><strong>情绪:</strong> {summary.get('sentiment', 'N/A')}</p>
        </body>
        </html>
        """
```

#### Step 5: 整合到Tool System

创建 `app/backend/tools/crm_tools.py`：

```python
from rtmt import Tool, ToolResult, ToolResultDirection

# 工具Schema定义
_crm_update_tool_schema = {
    "type": "function",
    "name": "update_crm",
    "description": "Update customer information in CRM after conversation ends",
    "parameters": {
        "type": "object",
        "properties": {
            "contact_id": {
                "type": "string",
                "description": "CRM contact ID"
            },
            "updates": {
                "type": "object",
                "description": "Fields to update in CRM"
            }
        },
        "required": ["contact_id", "updates"]
    }
}

async def _crm_update_tool(
    transcript_service,
    summary_service,
    crm_integration,
    email_service,
    args: Dict
) -> ToolResult:
    """执行CRM更新和邮件发送"""
    session_id = args.get("session_id")
    contact_id = args.get("contact_id")
    
    # 1. 获取转录
    transcript = transcript_service.get_transcript(session_id)
    
    # 2. 生成摘要
    summary = await summary_service.generate_summary(transcript)
    
    # 3. 更新CRM
    await crm_integration.update_contact(contact_id, args.get("updates", {}))
    await crm_integration.create_activity(contact_id, {
        "subject": "AI Voice Call",
        "description": summary["summary"],
        "date": transcript["end_time"]
    })
    
    # 4. 发送邮件
    database_email = os.environ.get("CRM_DATABASE_EMAIL")
    await email_service.send_summary(
        to_email=database_email,
        subject=f"Call Summary - {contact_id}",
        summary=summary
    )
    
    return ToolResult(
        {"status": "success", "summary": summary},
        ToolResultDirection.TO_SERVER
    )

def attach_crm_tools(rtmt, transcript_service, summary_service, crm, email):
    """注册CRM工具到RTMiddleTier"""
    rtmt.tools["update_crm"] = Tool(
        schema=_crm_update_tool_schema,
        target=lambda args: _crm_update_tool(
            transcript_service, summary_service, crm, email, args
        )
    )
```

### 3.1.2 配置要求

在 `.env` 文件中添加：

```bash
# CRM配置
CRM_TYPE=salesforce  # 或 hubspot
CRM_INSTANCE_URL=https://your-instance.salesforce.com
CRM_ACCESS_TOKEN=your_access_token
CRM_DATABASE_EMAIL=database@company.com

# 邮件配置
SMTP_HOST=smtp.office365.com
SMTP_PORT=587
SMTP_USERNAME=noreply@company.com
SMTP_PASSWORD=your_password
```

### 3.1.3 集成到主应用

修改 `app/backend/app.py`：

```python
from services.transcript_service import TranscriptService
from services.summary_service import SummaryService
from integrations.crm_integration import CRMFactory
from integrations.email_service import EmailService
from tools.crm_tools import attach_crm_tools

async def create_app():
    # ... 现有代码 ...
    
    # 初始化新服务
    transcript_service = TranscriptService()
    summary_service = SummaryService(
        endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        credential=llm_credential
    )
    crm = CRMFactory.create(
        crm_type=os.environ.get("CRM_TYPE", "salesforce"),
        instance_url=os.environ.get("CRM_INSTANCE_URL"),
        access_token=os.environ.get("CRM_ACCESS_TOKEN")
    )
    email_service = EmailService(
        smtp_host=os.environ["SMTP_HOST"],
        smtp_port=int(os.environ.get("SMTP_PORT", 587)),
        username=os.environ["SMTP_USERNAME"],
        password=os.environ["SMTP_PASSWORD"]
    )
    
    # 注册CRM工具
    attach_crm_tools(rtmt, transcript_service, summary_service, crm, email_service)
    
    # ... 其余代码 ...
```

---

## 3.2 功能二：人工客服转接

### 3.2.1 技术方案

**架构设计**：

```
AI检测转接需求 → 触发转接工具 → 呼叫系统API → 转接人工客服 → 传递上下文
```

#### Step 1: 电话系统集成

创建 `app/backend/integrations/telephony.py`：

```python
import aiohttp
from typing import Dict, Optional, List
from abc import ABC, abstractmethod

class TelephonyProvider(ABC):
    @abstractmethod
    async def transfer_call(
        self, 
        call_sid: str, 
        to_number: str, 
        context: Optional[Dict] = None
    ) -> bool:
        pass
    
    @abstractmethod
    async def get_available_agents(self) -> List[Dict]:
        pass

class TwilioProvider(TelephonyProvider):
    """Twilio电话系统集成"""
    
    def __init__(self, account_sid: str, auth_token: str):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.base_url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}"
    
    async def transfer_call(
        self, 
        call_sid: str, 
        to_number: str, 
        context: Optional[Dict] = None
    ) -> bool:
        """转接电话到指定号码"""
        url = f"{self.base_url}/Calls/{call_sid}.json"
        
        # 生成TwiML指令进行转接
        twiml = f"""
        <Response>
            <Say>Transferring you to a human agent. Please wait.</Say>
            <Dial>
                <Number>{to_number}</Number>
            </Dial>
        </Response>
        """
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                auth=aiohttp.BasicAuth(self.account_sid, self.auth_token),
                data={"Twiml": twiml}
            ) as response:
                return response.status == 200
    
    async def get_available_agents(self) -> List[Dict]:
        """获取可用客服列表（简化示例）"""
        # 实际应集成工作人员管理系统
        return [
            {"id": "agent001", "name": "John Doe", "phone": "+1234567890"},
            {"id": "agent002", "name": "Jane Smith", "phone": "+0987654321"}
        ]

class AzureCommunicationServices(TelephonyProvider):
    """Azure Communication Services集成"""
    
    def __init__(self, endpoint: str, access_key: str):
        self.endpoint = endpoint
        self.access_key = access_key
    
    async def transfer_call(
        self, 
        call_sid: str, 
        to_number: str, 
        context: Optional[Dict] = None
    ) -> bool:
        """使用ACS转接电话"""
        url = f"{self.endpoint}/calling/calls/{call_sid}/:transfer"
        headers = {
            "Authorization": f"Bearer {self.access_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "targetParticipant": {
                "phoneNumber": to_number
            },
            "transferType": "blind"  # 或 "consultative" 用于咨询转接
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                return resp.status in [200, 202]
    
    async def get_available_agents(self) -> List[Dict]:
        """获取可用客服"""
        # 实现从队列管理系统获取
        return []

class TelephonyFactory:
    @staticmethod
    def create(provider: str, **kwargs) -> TelephonyProvider:
        if provider.lower() == "twilio":
            return TwilioProvider(kwargs["account_sid"], kwargs["auth_token"])
        elif provider.lower() == "azure":
            return AzureCommunicationServices(kwargs["endpoint"], kwargs["access_key"])
        else:
            raise ValueError(f"Unsupported telephony provider: {provider}")
```

#### Step 2: 转接管理服务

创建 `app/backend/services/handoff_service.py`：

```python
from typing import Dict, Optional
import logging

logger = logging.getLogger("voicerag")

class HandoffService:
    def __init__(self, telephony_provider, transcript_service):
        self.telephony = telephony_provider
        self.transcript_service = transcript_service
    
    async def initiate_handoff(
        self, 
        session_id: str, 
        call_sid: str, 
        reason: Optional[str] = None
    ) -> Dict:
        """
        启动人工转接流程
        
        返回: {
            "success": bool,
            "agent_id": str,
            "context": dict
        }
        """
        logger.info(f"Initiating handoff for session {session_id}, reason: {reason}")
        
        # 1. 获取对话上下文
        transcript = self.transcript_service.get_transcript(session_id)
        context = {
            "session_id": session_id,
            "conversation_summary": self._summarize_for_agent(transcript),
            "customer_intent": reason or "general_inquiry",
            "timestamp": transcript.get("start_time")
        }
        
        # 2. 选择可用客服
        agents = await self.telephony.get_available_agents()
        if not agents:
            logger.error("No agents available for handoff")
            return {"success": False, "error": "No agents available"}
        
        selected_agent = agents[0]  # 简化：选择第一个，实际应实现路由逻辑
        
        # 3. 执行转接
        success = await self.telephony.transfer_call(
            call_sid=call_sid,
            to_number=selected_agent["phone"],
            context=context
        )
        
        if success:
            logger.info(f"Call transferred to agent {selected_agent['id']}")
            # 4. 通知客服系统（可选：WebSocket、数据库等）
            await self._notify_agent(selected_agent["id"], context)
        
        return {
            "success": success,
            "agent_id": selected_agent["id"] if success else None,
            "context": context
        }
    
    def _summarize_for_agent(self, transcript: Dict) -> str:
        """为人工客服生成简要摘要"""
        messages = transcript.get("messages", [])
        if len(messages) <= 3:
            return "New conversation, minimal context."
        
        recent_messages = messages[-3:]
        summary = "Recent conversation:\n"
        for msg in recent_messages:
            summary += f"{msg['role']}: {msg['content'][:100]}...\n"
        
        return summary
    
    async def _notify_agent(self, agent_id: str, context: Dict):
        """通知客服有新的转接（可通过WebSocket、消息队列等）"""
        # 实现客服控制台通知逻辑
        pass
```

#### Step 3: 创建转接工具

创建 `app/backend/tools/handoff_tools.py`：

```python
from rtmt import Tool, ToolResult, ToolResultDirection
from typing import Dict

_handoff_tool_schema = {
    "type": "function",
    "name": "transfer_to_human",
    "description": (
        "Transfer the call to a human agent when the AI cannot handle the request, "
        "or when the user explicitly asks to speak with a human. "
        "Use this when: customer is frustrated, request is too complex, "
        "user asks for a human, or issue requires human judgment."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Reason for handoff (e.g., 'complex_inquiry', 'customer_request', 'escalation')",
                "enum": ["complex_inquiry", "customer_request", "technical_issue", 
                        "escalation", "sales_inquiry", "other"]
            },
            "urgency": {
                "type": "string",
                "description": "Urgency level",
                "enum": ["low", "medium", "high"],
                "default": "medium"
            }
        },
        "required": ["reason"]
    }
}

async def _handoff_tool(
    handoff_service,
    session_id: str,
    call_sid: str,
    args: Dict
) -> ToolResult:
    """执行人工转接"""
    
    reason = args.get("reason", "other")
    urgency = args.get("urgency", "medium")
    
    # 执行转接
    result = await handoff_service.initiate_handoff(
        session_id=session_id,
        call_sid=call_sid,
        reason=reason
    )
    
    if result["success"]:
        message = {
            "status": "transferred",
            "message": "Call is being transferred to a human agent.",
            "agent_id": result["agent_id"]
        }
    else:
        message = {
            "status": "failed",
            "message": "Unable to transfer at this time. Please try again.",
            "error": result.get("error")
        }
    
    return ToolResult(message, ToolResultDirection.TO_CLIENT)

def attach_handoff_tools(rtmt, handoff_service):
    """注册转接工具"""
    rtmt.tools["transfer_to_human"] = Tool(
        schema=_handoff_tool_schema,
        target=lambda args: _handoff_tool(
            handoff_service,
            args.get("session_id"),
            args.get("call_sid"),
            args
        )
    )
```

### 3.2.2 系统消息更新

修改 `app.py` 中的 `system_message`，让AI知道何时使用转接工具：

```python
rtmt.system_message = """
You are a helpful assistant with access to a knowledge base through the 'search' tool.

IMPORTANT: Always respond in English only.

You can transfer calls to human agents using the 'transfer_to_human' tool when:
1. The customer explicitly asks to speak with a human
2. The query is too complex and beyond your capabilities
3. The customer seems frustrated or unsatisfied
4. The request requires human judgment or authorization
5. Technical issues that require specialized support

Always use the following step-by-step instructions to respond: 
1. First, try to help the customer yourself using the 'search' tool
2. If you cannot provide a satisfactory answer, offer to transfer to a human
3. If customer agrees or explicitly requests human, use 'transfer_to_human' tool
4. Keep responses short and clear for audio listening

Always respond in English.
""".strip()
```

### 3.2.3 配置

在 `.env` 添加：

```bash
# 电话系统配置
TELEPHONY_PROVIDER=twilio  # 或 azure
TWILIO_ACCOUNT_SID=your_account_sid
TWILIO_AUTH_TOKEN=your_auth_token
AGENT_PHONE_NUMBER=+1234567890  # 默认客服号码
```

---

## 3.3 功能三：报价单生成与发送

### 3.3.1 技术方案

**流程设计**：

```
AI收集信息 → 验证完整性 → 生成报价单PDF → 发送邮件 → 通知用户
```

#### Step 1: 报价单生成服务

创建 `app/backend/services/quote_service.py`：

```python
from datetime import datetime, timedelta
from typing import Dict, List
import io
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors

class QuoteService:
    def __init__(self, company_info: Dict):
        self.company_info = company_info
    
    def validate_quote_data(self, data: Dict) -> tuple[bool, List[str]]:
        """
        验证报价数据完整性
        
        返回: (is_valid, missing_fields)
        """
        required_fields = [
            "customer_name",
            "customer_email",
            "items"  # [{name, description, quantity, unit_price}]
        ]
        
        missing = []
        for field in required_fields:
            if field not in data or not data[field]:
                missing.append(field)
        
        # 验证items
        if "items" in data:
            for idx, item in enumerate(data["items"]):
                item_required = ["name", "quantity", "unit_price"]
                for field in item_required:
                    if field not in item:
                        missing.append(f"items[{idx}].{field}")
        
        return (len(missing) == 0, missing)
    
    async def generate_quote_pdf(self, data: Dict) -> bytes:
        """
        生成报价单PDF
        
        data结构: {
            "customer_name": str,
            "customer_email": str,
            "customer_company": str (optional),
            "items": [
                {
                    "name": str,
                    "description": str,
                    "quantity": int,
                    "unit_price": float
                }
            ],
            "notes": str (optional),
            "valid_until": str (optional, ISO date)
        }
        """
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        story = []
        styles = getSampleStyleSheet()
        
        # 标题
        title = Paragraph(
            f"<b>QUOTATION</b><br/>Quote #{self._generate_quote_number()}",
            styles['Title']
        )
        story.append(title)
        story.append(Spacer(1, 0.3*inch))
        
        # 公司信息
        company_text = f"""
        <b>{self.company_info.get('name', 'Company Name')}</b><br/>
        {self.company_info.get('address', '')}<br/>
        {self.company_info.get('phone', '')}<br/>
        {self.company_info.get('email', '')}
        """
        story.append(Paragraph(company_text, styles['Normal']))
        story.append(Spacer(1, 0.3*inch))
        
        # 客户信息
        customer_text = f"""
        <b>Quote For:</b><br/>
        {data.get('customer_name', 'N/A')}<br/>
        {data.get('customer_company', '')}<br/>
        {data.get('customer_email', '')}
        """
        story.append(Paragraph(customer_text, styles['Normal']))
        story.append(Spacer(1, 0.3*inch))
        
        # 日期信息
        today = datetime.now().strftime("%Y-%m-%d")
        valid_until = data.get('valid_until') or (
            datetime.now() + timedelta(days=30)
        ).strftime("%Y-%m-%d")
        
        date_text = f"""
        <b>Date:</b> {today}<br/>
        <b>Valid Until:</b> {valid_until}
        """
        story.append(Paragraph(date_text, styles['Normal']))
        story.append(Spacer(1, 0.3*inch))
        
        # 项目表格
        table_data = [["Item", "Description", "Qty", "Unit Price", "Total"]]
        subtotal = 0
        
        for item in data.get("items", []):
            quantity = item["quantity"]
            unit_price = item["unit_price"]
            total = quantity * unit_price
            subtotal += total
            
            table_data.append([
                item["name"],
                item.get("description", "")[:50],  # 限制长度
                str(quantity),
                f"${unit_price:.2f}",
                f"${total:.2f}"
            ])
        
        # 计算税费和总计
        tax_rate = data.get("tax_rate", 0.10)  # 默认10%
        tax = subtotal * tax_rate
        total = subtotal + tax
        
        table_data.append(["", "", "", "Subtotal:", f"${subtotal:.2f}"])
        table_data.append(["", "", "", f"Tax ({tax_rate*100}%):", f"${tax:.2f}"])
        table_data.append(["", "", "", "Total:", f"${total:.2f}"])
        
        table = Table(table_data, colWidths=[2*inch, 2.5*inch, 0.7*inch, 1*inch, 1*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -4), colors.beige),
            ('GRID', (0, 0), (-1, -4), 1, colors.black),
            ('LINEBELOW', (0, -3), (-1, -3), 2, colors.black),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ]))
        
        story.append(table)
        story.append(Spacer(1, 0.3*inch))
        
        # 备注
        if notes := data.get("notes"):
            story.append(Paragraph(f"<b>Notes:</b>", styles['Normal']))
            story.append(Paragraph(notes, styles['Normal']))
        
        # 条款
        terms = """
        <b>Terms & Conditions:</b><br/>
        1. This quote is valid for 30 days from the date of issue.<br/>
        2. Prices are subject to change without notice.<br/>
        3. Payment terms: Net 30 days.<br/>
        4. Delivery timeframe will be confirmed upon order.
        """
        story.append(Spacer(1, 0.3*inch))
        story.append(Paragraph(terms, styles['Normal']))
        
        # 生成PDF
        doc.build(story)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        
        return pdf_bytes
    
    def _generate_quote_number(self) -> str:
        """生成报价单编号"""
        return f"Q-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
```

#### Step 2: 报价单工具

创建 `app/backend/tools/quote_tools.py`：

```python
from rtmt import Tool, ToolResult, ToolResultDirection
from typing import Dict
import base64

_quote_tool_schema = {
    "type": "function",
    "name": "prepare_quotation",
    "description": (
        "Prepare and send a quotation to the customer via email. "
        "Collect all necessary information before calling this tool: "
        "customer name, email, items with quantities and prices."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "customer_name": {
                "type": "string",
                "description": "Customer's full name"
            },
            "customer_email": {
                "type": "string",
                "description": "Customer's email address"
            },
            "customer_company": {
                "type": "string",
                "description": "Customer's company name (optional)"
            },
            "items": {
                "type": "array",
                "description": "List of quoted items",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "quantity": {"type": "integer"},
                        "unit_price": {"type": "number"}
                    },
                    "required": ["name", "quantity", "unit_price"]
                }
            },
            "notes": {
                "type": "string",
                "description": "Additional notes for the quote"
            }
        },
        "required": ["customer_name", "customer_email", "items"]
    }
}

_collect_quote_info_schema = {
    "type": "function",
    "name": "collect_quote_information",
    "description": (
        "Internal tool to track quote information collection progress. "
        "Use this to check what information is still needed for the quote."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "collected_fields": {
                "type": "object",
                "description": "Fields already collected"
            }
        },
        "required": ["collected_fields"]
    }
}

async def _prepare_quotation_tool(
    quote_service,
    email_service,
    args: Dict
) -> ToolResult:
    """生成并发送报价单"""
    
    # 1. 验证数据
    is_valid, missing = quote_service.validate_quote_data(args)
    
    if not is_valid:
        return ToolResult(
            {
                "status": "incomplete",
                "message": f"Missing required information: {', '.join(missing)}",
                "missing_fields": missing
            },
            ToolResultDirection.TO_SERVER
        )
    
    # 2. 生成PDF
    try:
        pdf_bytes = await quote_service.generate_quote_pdf(args)
    except Exception as e:
        return ToolResult(
            {
                "status": "error",
                "message": f"Failed to generate quote: {str(e)}"
            },
            ToolResultDirection.TO_SERVER
        )
    
    # 3. 发送邮件
    try:
        success = await email_service.send_quote(
            to_email=args["customer_email"],
            customer_name=args["customer_name"],
            pdf_content=pdf_bytes
        )
        
        if success:
            return ToolResult(
                {
                    "status": "sent",
                    "message": f"Quote successfully sent to {args['customer_email']}",
                    "quote_number": quote_service._generate_quote_number()
                },
                ToolResultDirection.TO_CLIENT
            )
        else:
            return ToolResult(
                {
                    "status": "error",
                    "message": "Failed to send quote email"
                },
                ToolResultDirection.TO_SERVER
            )
    except Exception as e:
        return ToolResult(
            {
                "status": "error",
                "message": f"Error sending quote: {str(e)}"
            },
            ToolResultDirection.TO_SERVER
        )

def attach_quote_tools(rtmt, quote_service, email_service):
    """注册报价工具"""
    rtmt.tools["prepare_quotation"] = Tool(
        schema=_quote_tool_schema,
        target=lambda args: _prepare_quotation_tool(
            quote_service, email_service, args
        )
    )
```

#### Step 3: 扩展邮件服务

修改 `app/backend/integrations/email_service.py`，添加发送报价单方法：

```python
# 在EmailService类中添加：

async def send_quote(
    self, 
    to_email: str, 
    customer_name: str,
    pdf_content: bytes
) -> bool:
    """发送报价单邮件"""
    from email.mime.application import MIMEApplication
    
    message = MIMEMultipart()
    message["Subject"] = f"Quotation for {customer_name}"
    message["From"] = self.username
    message["To"] = to_email
    
    # 邮件正文
    body = f"""
    Dear {customer_name},
    
    Thank you for your interest in our services.
    
    Please find attached your quotation. This quote is valid for 30 days 
    from the date of issue.
    
    If you have any questions or would like to proceed with this quote, 
    please don't hesitate to contact us.
    
    Best regards,
    George Fethers & Co.
    """
    
    text_part = MIMEText(body, "plain")
    message.attach(text_part)
    
    # 附加PDF
    pdf_part = MIMEApplication(pdf_content, _subtype="pdf")
    pdf_part.add_header(
        'Content-Disposition', 
        'attachment', 
        filename=f'quotation_{datetime.now().strftime("%Y%m%d")}.pdf'
    )
    message.attach(pdf_part)
    
    try:
        await aiosmtplib.send(
            message,
            hostname=self.smtp_host,
            port=self.smtp_port,
            username=self.username,
            password=self.password,
            use_tls=True
        )
        return True
    except Exception as e:
        print(f"Quote email sending failed: {e}")
        return False
```

### 3.3.2 AI指导

更新 `system_message` 让AI知道如何收集报价信息：

```python
rtmt.system_message = """
You are a helpful assistant...

You can prepare and send quotations using the 'prepare_quotation' tool. 
When a customer asks for a quote:
1. Collect the following information through conversation:
   - Customer's full name
   - Customer's email address
   - Customer's company name (optional)
   - List of items/services they want quoted
   - For each item: name, description, quantity, unit price
   
2. Confirm all details with the customer before generating the quote

3. Once you have all required information, use 'prepare_quotation' tool

4. Inform the customer that the quote will be sent to their email

Example dialogue:
- "What items would you like a quote for?"
- "How many units do you need?"
- "Can I have your email address to send the quote?"

Keep your responses short for audio listening.
""".strip()
```

### 3.3.3 配置

在 `.env` 添加：

```bash
# 公司信息（用于报价单）
COMPANY_NAME=George Fethers & Co.
COMPANY_ADDRESS=123 Business St, City, Country
COMPANY_PHONE=+1234567890
COMPANY_EMAIL=info@georgefethers.com
QUOTE_TAX_RATE=0.10  # 10% 税率
```

### 3.3.4 依赖安装

更新 `app/backend/requirements.txt`：

```txt
# 添加新的依赖
reportlab==4.0.7  # PDF生成
aiosmtplib==3.0.1  # 异步SMTP
```

---

## 4. 测试方案

### 4.1 功能一测试：CRM集成

**测试场景**：
```python
# 测试脚本: tests/test_crm_integration.py

import pytest
from services.transcript_service import TranscriptService
from services.summary_service import SummaryService
from integrations.crm_integration import SalesforceCRM

@pytest.mark.asyncio
async def test_transcript_to_crm_flow():
    # 1. 创建模拟对话
    transcript_service = TranscriptService()
    session_id = "test_session_001"
    transcript_service.start_session(session_id)
    
    transcript_service.add_message(session_id, "user", "I need help with my account")
    transcript_service.add_message(session_id, "assistant", "I'd be happy to help...")
    
    # 2. 生成摘要
    summary_service = SummaryService(endpoint=..., credential=...)
    summary = await summary_service.generate_summary(
        transcript_service.get_transcript(session_id)
    )
    
    # 3. 验证摘要结构
    assert "summary" in summary
    assert "key_points" in summary
    assert "customer_info" in summary
    
    # 4. 更新CRM（使用mock）
    # ...
```

**手动测试清单**：
- [ ] 完整对话转录存储
- [ ] 摘要生成准确性
- [ ] CRM字段正确更新
- [ ] 邮件成功发送到数据库地址
- [ ] 错误处理（API失败、网络问题）

### 4.2 功能二测试：人工转接

**测试场景**：
```python
# tests/test_handoff.py

@pytest.mark.asyncio
async def test_handoff_trigger():
    # 模拟AI判断需要转接
    handoff_service = HandoffService(telephony_provider, transcript_service)
    
    result = await handoff_service.initiate_handoff(
        session_id="test_session",
        call_sid="CA1234567890",
        reason="complex_inquiry"
    )
    
    assert result["success"] == True
    assert "agent_id" in result
    assert "context" in result
```

**手动测试清单**：
- [ ] AI正确识别转接场景
- [ ] 用户请求人工时立即响应
- [ ] 转接过程无中断
- [ ] 上下文成功传递给客服
- [ ] 无可用客服时的降级处理
- [ ] 转接失败重试机制

### 4.3 功能三测试：报价单生成

**测试场景**：
```python
# tests/test_quote.py

@pytest.mark.asyncio
async def test_quote_generation():
    quote_service = QuoteService(company_info={...})
    
    quote_data = {
        "customer_name": "John Doe",
        "customer_email": "john@example.com",
        "items": [
            {"name": "Product A", "quantity": 2, "unit_price": 100.0}
        ]
    }
    
    # 验证数据
    is_valid, missing = quote_service.validate_quote_data(quote_data)
    assert is_valid == True
    
    # 生成PDF
    pdf_bytes = await quote_service.generate_quote_pdf(quote_data)
    assert len(pdf_bytes) > 0
    assert pdf_bytes[:4] == b'%PDF'  # PDF魔术数字
```

**手动测试清单**：
- [ ] AI逐步收集所有必需信息
- [ ] 数据验证准确
- [ ] PDF格式正确、可读
- [ ] 价格计算准确（含税）
- [ ] 邮件附件完整
- [ ] 客户成功接收报价单

---

## 5. 部署方案

### 5.1 开发环境部署

```bash
# 1. 安装新依赖
cd /Users/alserial/Documents/infinite_social/voice-Agent
source .venv/bin/activate
pip install reportlab aiosmtplib

# 2. 更新环境变量
cat >> app/backend/.env << EOF

# CRM Configuration
CRM_TYPE=salesforce
CRM_INSTANCE_URL=https://your-instance.salesforce.com
CRM_ACCESS_TOKEN=your_token
CRM_DATABASE_EMAIL=database@company.com

# Telephony Configuration
TELEPHONY_PROVIDER=twilio
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
AGENT_PHONE_NUMBER=+1234567890

# Email Configuration
SMTP_HOST=smtp.office365.com
SMTP_PORT=587
SMTP_USERNAME=noreply@company.com
SMTP_PASSWORD=your_password

# Company Information
COMPANY_NAME=George Fethers & Co.
COMPANY_ADDRESS=123 Business St
COMPANY_PHONE=+1234567890
COMPANY_EMAIL=info@georgefethers.com
QUOTE_TAX_RATE=0.10
EOF

# 3. 启动服务
./scripts/start.sh
```

### 5.2 生产环境部署（Azure）

#### 更新 `azure.yaml`:

```yaml
# 无需修改，新环境变量会自动注入
```

#### 配置 Azure 环境变量:

```bash
# CRM
azd env set CRM_TYPE salesforce
azd env set CRM_INSTANCE_URL https://your-instance.salesforce.com
azd env set CRM_ACCESS_TOKEN <secret-token>
azd env set CRM_DATABASE_EMAIL database@company.com

# Telephony
azd env set TELEPHONY_PROVIDER twilio
azd env set TWILIO_ACCOUNT_SID <account-sid>
azd env set TWILIO_AUTH_TOKEN <auth-token>
azd env set AGENT_PHONE_NUMBER +1234567890

# Email
azd env set SMTP_HOST smtp.office365.com
azd env set SMTP_PORT 587
azd env set SMTP_USERNAME noreply@company.com
azd env set SMTP_PASSWORD <password>

# Company
azd env set COMPANY_NAME "George Fethers & Co."
azd env set COMPANY_ADDRESS "123 Business St"
azd env set COMPANY_PHONE +1234567890
azd env set COMPANY_EMAIL info@georgefethers.com
azd env set QUOTE_TAX_RATE 0.10

# 部署
azd up
```

#### 使用 Azure Key Vault（推荐）：

```bash
# 将敏感信息存储在Key Vault
az keyvault secret set --vault-name <vault-name> --name CRM-ACCESS-TOKEN --value <token>
az keyvault secret set --vault-name <vault-name> --name TWILIO-AUTH-TOKEN --value <token>
az keyvault secret set --vault-name <vault-name> --name SMTP-PASSWORD --value <password>

# 更新Bicep模板引用Key Vault
# infra/main.bicep 中添加Key Vault引用
```

### 5.3 数据库配置（可选，用于持久化存储）

对于生产环境，建议使用数据库存储转录记录：

```bash
# 使用Azure Cosmos DB或PostgreSQL
azd env set DATABASE_TYPE cosmosdb
azd env set COSMOS_ENDPOINT https://<account>.documents.azure.com:443/
# Cosmos DB凭据会通过Managed Identity自动处理
```

修改 `transcript_service.py` 支持数据库存储：

```python
from azure.cosmos.aio import CosmosClient

class TranscriptService:
    def __init__(self, cosmos_client=None):
        if cosmos_client:
            self.use_db = True
            self.client = cosmos_client
            self.database = self.client.get_database_client("voicerag")
            self.container = self.database.get_container_client("transcripts")
        else:
            self.use_db = False
            self.sessions = {}
    
    async def start_session(self, session_id: str):
        if self.use_db:
            await self.container.upsert_item({
                "id": session_id,
                "start_time": datetime.utcnow().isoformat(),
                "messages": []
            })
        else:
            # 现有内存存储逻辑
            pass
```

---

## 6. 监控与日志

### 6.1 日志配置

在 `app/backend/app.py` 中添加结构化日志：

```python
import logging
import sys

# 配置JSON结构化日志（适用于Azure Application Insights）
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s", "level":"%(levelname)s", "module":"%(name)s", "message":"%(message)s"}',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger("voicerag")

# 在关键操作点添加日志
logger.info("CRM update initiated", extra={
    "session_id": session_id,
    "contact_id": contact_id,
    "operation": "crm_update"
})
```

### 6.2 性能监控

使用Azure Application Insights跟踪关键指标：

```python
from applicationinsights import TelemetryClient

tc = TelemetryClient(os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"))

# 跟踪自定义事件
tc.track_event("handoff_initiated", {
    "session_id": session_id,
    "reason": reason
})

# 跟踪依赖调用
tc.track_dependency(
    "CRM",
    "Salesforce",
    "update_contact",
    duration_ms,
    success
)

tc.flush()
```

### 6.3 关键指标仪表板

监控以下指标：
- **CRM集成**：
  - 摘要生成成功率
  - CRM更新延迟
  - 邮件发送成功率
  
- **人工转接**：
  - 转接触发频率
  - 转接成功率
  - 平均转接时间
  
- **报价单**：
  - 报价单生成成功率
  - PDF生成时间
  - 邮件发送成功率

---

## 7. 安全考虑

### 7.1 数据隐私

- **加密传输**：所有API通信使用TLS 1.2+
- **静态加密**：敏感数据（转录、CRM数据）使用Azure Storage加密
- **数据保留**：实施数据保留策略（例如：转录7天后自动删除）

### 7.2 身份验证

- **CRM API**：使用OAuth 2.0，定期轮换令牌
- **邮件服务**：使用应用专用密码
- **电话系统**：使用Webhook签名验证请求来源

```python
# Twilio Webhook验证示例
from twilio.request_validator import RequestValidator

def validate_twilio_request(request):
    validator = RequestValidator(os.environ["TWILIO_AUTH_TOKEN"])
    signature = request.headers.get("X-Twilio-Signature", "")
    url = str(request.url)
    params = await request.post()
    
    return validator.validate(url, params, signature)
```

### 7.3 访问控制

- 使用Azure Managed Identity进行服务间认证
- 实施基于角色的访问控制（RBAC）
- 最小权限原则：每个服务只获取必需的权限

---

## 8. 成本估算

基于现有Azure资源，新增功能的月度成本估算：

| 服务 | 用途 | 估算成本（USD/月） |
|------|------|-------------------|
| Azure OpenAI (GPT-4) | 摘要生成 | $50-200（取决于调用量）|
| Azure Cosmos DB (可选) | 转录存储 | $25-100 |
| Twilio | 电话转接 | $0.01/分钟 × 使用量 |
| SendGrid/Office 365 | 邮件发送 | $0-15（取决于发送量）|
| Azure Storage | PDF存储 | $5-10 |
| **总计** | | **$80-325/月** |

*注：成本会随使用量变化，建议设置预算警报*

---

## 9. 项目时间表

### 第1周：基础架构
- [ ] 创建新模块结构
- [ ] 实现转录服务
- [ ] 实现摘要服务
- [ ] 基础集成测试

### 第2周：CRM集成
- [ ] 实现CRM集成类（Salesforce/HubSpot）
- [ ] 实现邮件服务
- [ ] 创建CRM工具
- [ ] 单元测试

### 第3周：人工转接
- [ ] 实现电话系统集成
- [ ] 实现转接服务
- [ ] 创建转接工具
- [ ] 更新系统提示词
- [ ] 集成测试

### 第4周：报价单生成
- [ ] 实现报价单服务
- [ ] PDF生成模板
- [ ] 创建报价工具
- [ ] 邮件发送集成
- [ ] 端到端测试

### 第5周：测试与优化
- [ ] 完整端到端测试
- [ ] 性能优化
- [ ] 错误处理改进
- [ ] 用户验收测试

### 第6周：部署与文档
- [ ] 生产环境部署
- [ ] 监控配置
- [ ] 用户文档
- [ ] 运维手册

---

## 10. 风险与缓解措施

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| CRM API限流 | 高 | 中 | 实施请求队列和重试机制 |
| 电话转接失败 | 高 | 低 | 降级方案：提供回拨号码 |
| PDF生成性能问题 | 中 | 低 | 使用异步任务队列 |
| 邮件被标记为垃圾邮件 | 中 | 中 | 配置SPF/DKIM/DMARC记录 |
| 数据隐私合规 | 高 | 低 | GDPR/CCPA合规审查 |

---

## 11. 后续改进建议

### 11.1 功能增强

1. **多语言支持**：扩展报价单和邮件模板支持多语言
2. **智能路由**：基于技能的客服路由算法
3. **报价单模板自定义**：允许管理员自定义PDF模板
4. **CRM双向同步**：从CRM拉取客户历史记录

### 11.2 技术优化

1. **缓存层**：Redis缓存频繁访问的CRM数据
2. **消息队列**：使用Azure Service Bus处理异步任务
3. **A/B测试**：测试不同系统提示词的效果
4. **机器学习**：训练模型预测最佳转接时机

---

## 12. 结论

本技术方案提供了完整的实施路径，用于扩展Voice-Agent的企业级功能：

✅ **转录摘要与CRM集成**：自动化客户数据管理  
✅ **人工客服转接**：无缝的人机协作体验  
✅ **报价单生成**：专业的销售流程自动化

所有方案基于现有架构，采用模块化设计，易于维护和扩展。预计实施周期为6周，总投入约80-325美元/月的运营成本。

### 下一步行动

1. **立即**: 审查技术方案，确认需求符合度
2. **本周**: 设置开发环境，配置外部服务账号
3. **下周**: 开始第一阶段开发（基础架构）

### 联系与支持

如有任何技术问题或需要进一步讨论，请联系开发团队。

---

**文档版本**: 1.0  
**最后更新**: 2025-11-10  
**作者**: Voice-Agent 技术团队  
**审核**: 待定

