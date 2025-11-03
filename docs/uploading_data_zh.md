# 上传新数据文件指南（中文版）

本文档介绍如何向 VoiceRAG 应用添加新的数据文件，使其可以在语音对话中被查询。

> 📖 **English Version**: See [uploading_data.md](./uploading_data.md) for the full English documentation.

---

## 🚀 快速开始

### 最简单的方法（推荐）

**步骤 1**: 将新文件放入 `data/` 目录

```bash
# 将您的 PDF、Markdown 或其他文档复制到 data/ 目录
copy "您的文件路径\document.pdf" data\
```

**步骤 2**: 运行上传脚本

```powershell
# Windows PowerShell
.\scripts\upload_new_data.ps1

# Linux/Mac
./scripts/upload_new_data.sh
```

**步骤 3**: 等待 2-5 分钟，完成！

文件会自动上传到 Azure Blob Storage，索引器会自动处理并生成向量嵌入。

---

## 📋 支持的文件格式

| 格式 | 文件扩展名 | 说明 |
|------|-----------|------|
| ✅ **PDF** | `.pdf` | 包括文本 PDF 和扫描 PDF（需 OCR） |
| ✅ **Markdown** | `.md` | 纯文本，保留格式 |
| ✅ **Word** | `.docx`, `.doc` | Microsoft Word 文档 |
| ✅ **文本** | `.txt` | 纯文本文件 |
| ✅ **PowerPoint** | `.pptx`, `.ppt` | 演示文稿 |
| ✅ **Excel** | `.xlsx`, `.xls` | 表格数据 |
| ✅ **HTML** | `.html` | 网页内容 |

**支持中文！** ✅ 完全支持中文文档和中文语音对话。

---

## 🔍 验证文件已成功索引

### 方法 1: 在应用中测试（推荐）

1. 打开您的 VoiceRAG 应用
2. 点击 "Start conversation" 开始对话
3. 询问与新上传文档相关的问题

**示例**：
- "告诉我关于[新文档主题]的内容"
- "新文档里说了什么关于[关键词]的信息？"

如果系统能够回答并引用新文档，说明索引成功！

### 方法 2: 通过 Azure Portal 检查

1. 登录 [Azure Portal](https://portal.azure.com)
2. 找到您的 AI Search 服务（例如：`gptkb-bgvscddssk7zk`）
3. 点击 "索引器" (Indexers)
4. 查看 "执行历史记录" 中的状态
5. 确认状态为 "成功"，且处理的文档数增加

---

## 🔧 其他上传方法

### 方法 2: 通过 Azure Portal 直接上传

**适合场景**: 上传单个或少量文件

**步骤**:
1. 登录 Azure Portal
2. 找到资源组（例如：`rg-voicerag-prod`）
3. 进入存储账户（例如：`stbgvscddssk7zk`）
4. 点击 "容器" → 选择数据容器
5. 点击 "上传" 按钮
6. 选择文件并上传
7. （可选）手动运行索引器

### 方法 3: 使用 Azure CLI

**适合场景**: 批量上传或自动化

```bash
# 上传单个文件
az storage blob upload \
  --account-name stbgvscddssk7zk \
  --container-name content \
  --name document.pdf \
  --file local/path/document.pdf \
  --auth-mode login

# 批量上传整个目录
az storage blob upload-batch \
  --account-name stbgvscddssk7zk \
  --destination content \
  --source ./data/ \
  --auth-mode login
```

---

## ❓ 常见问题

### Q: 上传后多久可以查询？
**A**: 通常 2-5 分钟。大文件或批量上传可能需要更长时间。

### Q: 可以上传中文文档吗？
**A**: ✅ 完全支持！确保文件使用 UTF-8 编码。

### Q: 如何更新已存在的文件？
**A**: 使用相同的文件名重新上传，系统会自动覆盖并重新索引。

### Q: 如何删除已索引的文件？
**A**: 
1. 在 Azure Portal 中从 Blob Storage 删除文件
2. 运行索引器，会自动从索引中移除

### Q: 为什么搜索不到刚上传的内容？
**A**: 可能的原因：
- 索引未完成（等待 5-10 分钟）
- 浏览器缓存（刷新页面）
- 使用文档中的确切关键词进行搜索

### Q: 上传会产生额外费用吗？
**A**: 会，但通常很少：
- Blob Storage: ~￥0.15/GB/月
- 嵌入生成: 一次性费用，约 ￥3-5/100个文档

---

## 🛠️ 故障排除

### 问题 1: 脚本运行失败

**错误**: `Unable to find Python environment`

**解决方案**:
```bash
# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境（Windows）
.venv\Scripts\activate

# 激活虚拟环境（Linux/Mac）
source .venv/bin/activate

# 安装依赖
pip install -r app/backend/requirements.txt
```

### 问题 2: 中文内容显示乱码

**解决方案**:
1. 确保文件使用 UTF-8 编码
2. 在文本编辑器中另存为 UTF-8
3. 使用 Visual Studio Code 等工具检查编码

### 问题 3: 索引器显示失败

**排查步骤**:
1. 在 Azure Portal 查看详细错误信息
2. 检查文件是否损坏
3. 验证文件格式是否支持
4. 尝试转换文件格式（如 PDF → Markdown）

### 问题 4: 身份验证失败

**解决方案**:
```bash
# 重新登录 Azure
azd auth login

# 或使用设备代码登录
azd auth login --use-device-code
```

---

## 📚 工作原理

VoiceRAG 使用 Azure AI Search 的**集成向量化**功能：

```
您的文件 → Azure Blob Storage → AI Search 索引器 → 生成向量嵌入 → 可被查询
```

**自动化流程**:
1. 📤 文件上传到 Azure Blob Storage
2. 🔍 索引器自动检测新文件
3. ✂️ 将文档分割成小块（每块约 2000 字符）
4. 🧠 使用 text-embedding-3-large 生成向量嵌入
5. 💾 存储到搜索索引中
6. ✅ 可以通过语音查询

**关键组件**:
- **Azure Blob Storage**: 存储原始文档
- **Azure AI Search**: 索引和搜索引擎
- **Azure OpenAI Embeddings**: 生成语义向量
- **GPT-4o Realtime API**: 语音对话和回答生成

---

## 📞 获取帮助

**完整文档**:
- [英文详细版](./uploading_data.md) - 包含所有方法和详细说明
- [项目 README](../README.md) - 项目总体说明
- [自定义部署](./customizing_deploy.md) - 自定义配置选项

**遇到问题？**
1. 查看完整的英文文档获取更多详细信息
2. 在项目 GitHub 提交 Issue
3. 查看 Azure AI Search 官方文档

---

## ✅ 核心要点

- ✅ **最简单方法**: 文件放入 `data/` → 运行脚本 → 等待 2-5 分钟
- ✅ **支持格式**: PDF、Word、Markdown、文本等
- ✅ **支持中文**: 完全支持中文文档和语音对话
- ✅ **全自动化**: 索引和向量化自动完成
- ✅ **快速验证**: 在应用中直接询问相关问题

**祝您使用愉快！** 🎉

---

*最后更新: 2025年10月*

