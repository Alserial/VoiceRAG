# 上传新数据文件指南

本文档介绍如何向 VoiceRAG 应用添加新的数据文件，使其可以在语音对话中被查询。

## 📋 目录

- [概述](#概述)
- [工作原理](#工作原理)
- [方法 1: 使用本地上传脚本（推荐）](#方法-1-使用本地上传脚本推荐)
- [方法 2: 通过 Azure Portal 上传](#方法-2-通过-azure-portal-上传)
- [方法 3: 使用 Azure CLI 上传](#方法-3-使用-azure-cli-上传)
- [方法 4: 使用 Python 脚本直接上传](#方法-4-使用-python-脚本直接上传)
- [支持的文件格式](#支持的文件格式)
- [验证文件已成功索引](#验证文件已成功索引)
- [常见问题](#常见问题)
- [故障排除](#故障排除)

---

## 概述

VoiceRAG 使用 **Azure AI Search 的集成向量化功能**来自动处理和索引文档。当您上传新文件到 Azure Blob Storage 后，索引器会自动：

1. 提取文件内容
2. 将文档分割成小块（chunks）
3. 生成向量嵌入（embeddings）
4. 将内容索引到搜索服务中

整个过程完全自动化，通常在 2-5 分钟内完成。

---

## 工作原理

```
本地文件 → Azure Blob Storage → AI Search 索引器 → 向量化 → 搜索索引
   ↓              ↓                    ↓              ↓           ↓
data/目录      容器存储            自动触发        生成嵌入    可被查询
```

**关键组件**：
- **Azure Blob Storage**: 存储原始文档
- **Azure AI Search 索引器**: 监控存储容器，自动处理新文件
- **Azure OpenAI Embeddings**: 生成文本向量（text-embedding-3-large）
- **Search Index**: 存储向量化后的内容，支持语义搜索

---

## 方法 1: 使用本地上传脚本（推荐）

这是最简单的方法，适合批量上传多个文件。

### 前提条件

- 已完成 `azd up` 部署
- 已通过 `azd auth login` 登录 Azure
- Python 虚拟环境已配置

### 步骤

#### 1️⃣ 准备文件

将您要上传的文件放入项目的 `data/` 目录：

```bash
# Windows
copy "C:\your\files\document.pdf" data\

# Linux/Mac
cp /path/to/your/document.pdf data/
```

**支持的文件类型**：PDF、Markdown、Word、文本文件等（详见后文）

#### 2️⃣ 运行上传脚本

**Windows PowerShell**:
```powershell
.\scripts\upload_new_data.ps1
```

**Linux/Mac**:
```bash
chmod +x scripts/upload_new_data.sh
./scripts/upload_new_data.sh
```

#### 3️⃣ 等待索引完成

脚本会自动：
- ✅ 上传 `data/` 目录中的所有文件到 Azure Blob Storage
- ✅ 跳过已存在的文件（避免重复）
- ✅ 触发索引器运行
- ✅ 显示上传结果

**预期输出**：
```
=== 上传新数据文件到 VoiceRAG ===

[1/3] 加载 Python 虚拟环境...
[2/3] data/ 目录中的文件:
  - new_document.pdf (1.2 MB)
  - company_policy.md (45 KB)

[3/3] 上传文件并触发索引...
Uploading blob for file: new_document.pdf
Uploading blob for file: company_policy.md
Indexer started. Any unindexed blobs should be indexed in a few minutes.

✅ 完成! 文件已上传到 Azure Blob Storage。
```

#### 4️⃣ 验证索引状态

等待 2-5 分钟后，通过以下方式验证：

1. **在应用中测试**：访问应用 URL，询问与新文件相关的问题
2. **查看 Azure Portal**：AI Search → 索引器 → 运行历史记录

---

## 方法 2: 通过 Azure Portal 上传

适合上传单个或少量文件，不需要命令行操作。

### 步骤

#### 1️⃣ 登录 Azure Portal

访问：https://portal.azure.com

#### 2️⃣ 找到您的资源组

1. 在搜索栏输入资源组名称（例如：`rg-voicerag-prod`）
2. 点击进入资源组

#### 3️⃣ 打开存储账户

1. 在资源列表中找到存储账户（名称类似：`stxxxxxxxx`）
2. 点击进入存储账户

#### 4️⃣ 导航到容器

1. 在左侧菜单中选择 **"容器"** (Containers)
2. 找到用于存储文档的容器（通常名为 `content` 或与索引名称相同）
3. 点击容器名称

#### 5️⃣ 上传文件

1. 点击顶部的 **"上传"** 按钮
2. 点击 **"浏览文件"** 或拖放文件
3. 选择要上传的文件
4. （可选）在 "高级" 选项中设置：
   - 如果文件已存在：选择 "覆盖" 或 "跳过"
   - 身份验证类型：默认即可
5. 点击 **"上传"** 按钮

#### 6️⃣ 触发索引器（可选）

索引器会定期自动运行，但您也可以手动触发：

1. 返回资源组
2. 找到并点击 AI Search 服务（名称类似：`gptkb-xxxxxxxx`）
3. 在左侧菜单选择 **"索引器"** (Indexers)
4. 选择您的索引器（与索引同名）
5. 点击 **"运行"** 按钮

#### 7️⃣ 监控索引进度

在索引器页面：
1. 点击 **"执行历史记录"** (Execution History)
2. 查看最新运行的状态：
   - ✅ **成功** (Success): 索引完成
   - 🔄 **进行中** (InProgress): 正在处理
   - ❌ **失败** (Failed): 查看错误详情

---

## 方法 3: 使用 Azure CLI 上传

适合需要自动化或脚本化的场景。

### 前提条件

- 已安装 [Azure CLI](https://docs.microsoft.com/cli/azure/install-azure-cli)
- 已登录：`az login`

### 上传单个文件

```bash
# 设置变量（替换为您的实际值）
STORAGE_ACCOUNT="stbgvscddssk7zk"
CONTAINER_NAME="content"  # 或您的容器名称
LOCAL_FILE="path/to/your/document.pdf"
BLOB_NAME="document.pdf"

# 上传文件
az storage blob upload \
  --account-name $STORAGE_ACCOUNT \
  --container-name $CONTAINER_NAME \
  --name $BLOB_NAME \
  --file $LOCAL_FILE \
  --auth-mode login
```

### 批量上传目录

```bash
# 上传整个目录
az storage blob upload-batch \
  --account-name $STORAGE_ACCOUNT \
  --destination $CONTAINER_NAME \
  --source ./data/ \
  --auth-mode login
```

### 触发索引器

```bash
# 设置变量
SEARCH_SERVICE="gptkb-bgvscddssk7zk"
RESOURCE_GROUP="rg-voicerag-prod"
INDEXER_NAME="<您的索引器名称>"

# 运行索引器
az search indexer run \
  --name $INDEXER_NAME \
  --service-name $SEARCH_SERVICE \
  --resource-group $RESOURCE_GROUP
```

### 检查索引器状态

```bash
az search indexer show \
  --name $INDEXER_NAME \
  --service-name $SEARCH_SERVICE \
  --resource-group $RESOURCE_GROUP
```

---

## 方法 4: 使用 Python 脚本直接上传

如果您需要更多控制或自定义上传逻辑。

### 创建自定义上传脚本

创建文件 `upload_custom.py`:

```python
import os
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from azure.search.documents.indexes import SearchIndexerClient

# 配置
STORAGE_ENDPOINT = "https://stbgvscddssk7zk.blob.core.windows.net"
CONTAINER_NAME = "content"
SEARCH_ENDPOINT = "https://gptkb-bgvscddssk7zk.search.windows.net"
INDEXER_NAME = "<您的索引器名称>"

# 认证
credential = DefaultAzureCredential()

# 上传文件
def upload_file(file_path):
    blob_service_client = BlobServiceClient(
        account_url=STORAGE_ENDPOINT, 
        credential=credential
    )
    container_client = blob_service_client.get_container_client(CONTAINER_NAME)
    
    filename = os.path.basename(file_path)
    with open(file_path, "rb") as data:
        blob_client = container_client.upload_blob(
            name=filename, 
            data=data, 
            overwrite=True
        )
    print(f"✅ 已上传: {filename}")

# 触发索引器
def trigger_indexer():
    indexer_client = SearchIndexerClient(SEARCH_ENDPOINT, credential)
    indexer_client.run_indexer(INDEXER_NAME)
    print("✅ 索引器已触发")

# 使用示例
if __name__ == "__main__":
    # 上传单个文件
    upload_file("data/new_document.pdf")
    
    # 或批量上传
    for file in os.listdir("data"):
        file_path = os.path.join("data", file)
        if os.path.isfile(file_path):
            upload_file(file_path)
    
    # 触发索引器
    trigger_indexer()
```

### 运行脚本

```bash
# 激活虚拟环境
source .venv/bin/activate  # Linux/Mac
# 或
.venv\Scripts\activate  # Windows

# 运行脚本
python upload_custom.py
```

---

## 支持的文件格式

Azure AI Search 的集成向量化支持多种文件格式：

### ✅ 完全支持

| 格式 | 扩展名 | 说明 |
|------|--------|------|
| **PDF** | `.pdf` | 包括文本和图片中的文本（需 OCR） |
| **Markdown** | `.md` | 纯文本，保留格式 |
| **纯文本** | `.txt` | 最简单的格式 |
| **Word** | `.docx`, `.doc` | Microsoft Word 文档 |
| **PowerPoint** | `.pptx`, `.ppt` | 演示文稿内容 |
| **Excel** | `.xlsx`, `.xls` | 表格数据 |
| **HTML** | `.html`, `.htm` | 网页内容 |

### ⚠️ 需要额外配置

| 格式 | 扩展名 | 需要的配置 |
|------|--------|-----------|
| **图片** | `.jpg`, `.png` | 需要添加 OCR 技能 |
| **JSON** | `.json` | 需要自定义解析技能 |
| **CSV** | `.csv` | 需要自定义解析技能 |
| **XML** | `.xml` | 需要自定义解析技能 |

### 📝 文件要求

- **最大文件大小**: 建议 < 100MB（取决于 Azure 配置）
- **编码**: UTF-8（推荐）
- **文件名**: 
  - 建议使用英文和数字
  - 避免特殊字符：`< > : " / \ | ? *`
  - 中文文件名需要确保编码正确

---

## 验证文件已成功索引

### 方法 1: 通过 Azure Portal 检查

#### 查看索引器状态

1. 登录 Azure Portal
2. 导航到 AI Search 服务
3. 点击 **"索引器"** (Indexers)
4. 查看索引器的 **"上次运行状态"**
5. 点击查看 **"执行历史记录"**

**成功的标志**：
- ✅ 状态显示 "成功" (Success)
- ✅ "已处理的文档数" 增加
- ✅ "失败的文档数" 为 0

#### 查看索引内容

1. 在 AI Search 服务中点击 **"索引"** (Indexes)
2. 选择您的索引
3. 点击 **"搜索浏览器"** (Search explorer)
4. 尝试搜索新文档中的关键词
5. 查看返回的结果

### 方法 2: 使用 Azure CLI 检查

```bash
# 检查索引器状态
az search indexer show \
  --name <索引器名称> \
  --service-name <搜索服务名称> \
  --resource-group <资源组名称> \
  --query "lastResult.status"

# 查看索引文档数
az search index show \
  --name <索引名称> \
  --service-name <搜索服务名称> \
  --resource-group <资源组名称> \
  --query "documentCount"
```

### 方法 3: 在应用中测试

这是最直接的验证方法：

1. 访问您的 VoiceRAG 应用 URL
2. 点击 **"Start conversation"** 开始对话
3. 询问与新上传文档相关的问题

**示例问题**：
- "Tell me about [新文档中的主题]"
- "What does the new document say about [关键词]?"
- "根据新上传的文件，[具体问题]"

**成功的标志**：
- ✅ 系统能够引用新文档的内容
- ✅ 回答中包含新文档的信息
- ✅ 在 "Sources" 或 "References" 中显示新文档名称

---

## 常见问题

### Q1: 上传文件后多久可以查询？

**A**: 通常 2-5 分钟。具体时间取决于：
- 文件大小：大文件需要更长时间
- 文件数量：批量上传需要更长时间
- 索引器调度：可能需要等待下次运行（通常每 5 分钟）

### Q2: 如何更新已存在的文件？

**A**: 有两种方式：

1. **覆盖上传**：
   - 使用相同的文件名上传新版本
   - 索引器会自动检测更改并重新索引

2. **删除后重新上传**：
   - 从 Blob Storage 删除旧文件
   - 上传新文件
   - 运行索引器

### Q3: 可以删除已索引的文件吗？

**A**: 可以，有两种方式：

1. **从 Blob Storage 删除**：
   - 删除 blob 文件
   - 运行索引器，会自动从索引中移除

2. **使用 Azure CLI**：
   ```bash
   # 删除 blob
   az storage blob delete \
     --account-name <存储账户> \
     --container-name <容器名称> \
     --name <文件名> \
     --auth-mode login
   
   # 触发索引器更新
   az search indexer run --name <索引器名称> ...
   ```

### Q4: 为什么我的 PDF 文件没有被正确索引？

**A**: 可能的原因：

1. **PDF 是扫描图片**：需要 OCR 技能来提取文本
2. **PDF 加密或受保护**：需要先解锁
3. **PDF 损坏**：尝试使用 PDF 工具修复
4. **文件太大**：尝试分割成小文件

**解决方案**：
- 检查索引器的执行历史中的错误消息
- 确保 PDF 包含可选择的文本（而不是图片）
- 考虑将 PDF 转换为 Markdown 或 Word 格式

### Q5: 支持中文文档吗？

**A**: ✅ 完全支持！

- Azure AI Search 支持多语言内容
- 嵌入模型 (text-embedding-3-large) 支持中文
- GPT-4o Realtime 支持中文语音对话

**注意事项**：
- 确保文件使用 UTF-8 编码
- 中文文件名可能在某些情况下导致问题，建议使用英文文件名

### Q6: 如何批量上传大量文件？

**A**: 推荐方式：

1. **使用本地脚本**（方法 1）：
   - 将所有文件放入 `data/` 目录
   - 运行 `upload_new_data.ps1` 或 `.sh`

2. **使用 Azure CLI**（方法 3）：
   ```bash
   az storage blob upload-batch \
     --source ./data/ \
     --destination <容器名称> \
     --account-name <存储账户> \
     --auth-mode login
   ```

3. **使用 Azure Storage Explorer**：
   - 图形界面工具
   - 支持拖放批量上传
   - 下载：https://azure.microsoft.com/features/storage-explorer/

### Q7: 上传后应用没有找到新内容怎么办？

**A**: 排查步骤：

1. **检查索引器状态**（见上文"验证方法"）
2. **手动运行索引器**
3. **检查 blob 是否成功上传**
4. **查看索引器错误日志**
5. **等待更长时间**（有时需要 10 分钟）
6. **刷新应用缓存**（重新加载页面）

### Q8: 上传会产生额外费用吗？

**A**: 会，但通常很少：

| 服务 | 计费方式 | 估算成本 |
|------|----------|----------|
| **Blob Storage** | 存储量 + 操作次数 | ~$0.02/GB/月 |
| **AI Search 索引** | 索引大小 | 包含在 Search 服务费用中 |
| **OpenAI Embeddings** | Token 数量 | ~$0.13/百万 tokens |
| **索引器运行** | 运行时间 | 包含在 Search 服务费用中 |

**示例**：
- 上传 100 个 PDF（每个 1MB）
- 存储成本：~$0.002/月
- 嵌入成本：~$0.50（一次性）
- 总计：< $1（首次）

---

## 故障排除

### 问题 1: 上传脚本失败

**错误信息**：
```
Error: Unable to find Python environment
```

**解决方案**：
```bash
# 创建虚拟环境
python3 -m venv .venv

# 激活虚拟环境
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate  # Windows

# 安装依赖
pip install -r app/backend/requirements.txt

# 重新运行脚本
./scripts/upload_new_data.sh
```

---

### 问题 2: 身份验证失败

**错误信息**：
```
AuthenticationError: Failed to authenticate with Azure
```

**解决方案**：
```bash
# 重新登录 Azure
azd auth login

# 或使用设备代码登录
azd auth login --use-device-code

# 验证登录状态
az account show
```

---

### 问题 3: 索引器显示失败

**错误信息**：
```
Indexer execution failed: Unable to extract content from document
```

**可能原因**：
- 文件格式不受支持
- 文件损坏
- 文件过大
- 需要额外的技能（如 OCR）

**解决方案**：
1. 在 Azure Portal 查看详细错误消息
2. 尝试转换文件格式（PDF → Markdown）
3. 验证文件完整性
4. 检查文件大小限制
5. 查看索引器技能集配置

---

### 问题 4: 文件上传但未被索引

**排查步骤**：

1. **检查容器名称是否正确**：
   ```bash
   # 列出所有容器
   az storage container list \
     --account-name <存储账户> \
     --auth-mode login
   ```

2. **检查索引器数据源配置**：
   - 在 Azure Portal → AI Search → 数据源
   - 确认数据源指向正确的容器

3. **手动触发索引器**：
   ```bash
   az search indexer run \
     --name <索引器名称> \
     --service-name <搜索服务> \
     --resource-group <资源组>
   ```

4. **检查索引器调度**：
   - 确认索引器已启用
   - 查看调度频率设置

---

### 问题 5: 中文内容显示乱码

**解决方案**：

1. **确保文件使用 UTF-8 编码**：
   ```bash
   # Linux/Mac: 转换文件编码
   iconv -f GB2312 -t UTF-8 input.txt > output.txt
   ```

2. **使用文本编辑器另存为 UTF-8**：
   - Visual Studio Code: 右下角点击编码 → "Save with Encoding" → UTF-8

3. **验证上传后的编码**：
   - 从 blob 下载文件检查
   - 在 Search Explorer 中查看预览

---

### 问题 6: 搜索不到刚上传的内容

**可能原因与解决方案**：

1. **索引未完成** → 等待 5-10 分钟
2. **应用缓存** → 刷新浏览器
3. **搜索查询不匹配** → 尝试使用文档中的确切关键词
4. **向量化失败** → 检查嵌入模型配置
5. **权限问题** → 确认索引器有读取 blob 的权限

---

## 获取帮助

如果您遇到本文档未涵盖的问题：

1. **查看项目文档**：
   - [README.md](../README.md)
   - [AGENTS.md](../AGENTS.md)
   - [自定义部署](./customizing_deploy.md)

2. **查看 Azure 文档**：
   - [Azure AI Search 文档](https://learn.microsoft.com/azure/search/)
   - [集成向量化指南](https://learn.microsoft.com/azure/search/search-get-started-portal-import-vectors)

3. **检查示例代码**：
   - `app/backend/setup_intvect.py` - 索引设置代码
   - `scripts/upload_new_data.ps1` - 上传脚本

4. **提交 Issue**：
   - GitHub 项目仓库
   - 包含详细的错误信息和步骤

---

## 总结

上传新数据文件的推荐流程：

```
1. 准备文件（PDF/Markdown/Word 等）
   ↓
2. 放入 data/ 目录
   ↓
3. 运行 upload_new_data.ps1（或 .sh）
   ↓
4. 等待 2-5 分钟
   ↓
5. 在应用中测试查询
   ↓
6. ✅ 完成！
```

**关键要点**：
- ✅ 使用本地脚本最简单
- ✅ 支持多种文件格式
- ✅ 索引过程全自动
- ✅ 通常 2-5 分钟即可使用
- ✅ 支持中文内容

祝您使用愉快！🎉




