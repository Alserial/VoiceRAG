# 语言配置说明 (Language Configuration)

本文档说明 VoiceRAG 应用的多语言功能及其工作原理。

---

## 🌐 支持的语言

VoiceRAG 应用支持以下界面语言：

| 语言 | 代码 | 界面翻译 | AI 语音支持 |
|------|------|---------|------------|
| **English** | `en` | ✅ | ✅ |
| **Español** | `es` | ✅ | ✅ |
| **Français** | `fr` | ✅ | ✅ |
| **日本語** | `ja` | ✅ | ✅ |
| **中文** | - | ❌ | ✅ (AI支持) |

---

## 🔍 语言检测机制

### 1. 界面语言检测

应用使用 `i18next-browser-languagedetector` 自动检测用户语言，按以下顺序：

```
1. localStorage 中保存的用户选择 (userLanguage)
   ↓ (如果没有)
2. 浏览器语言设置 (navigator.language)
   ↓ (如果不支持)
3. 回退到英语 (fallbackLng: "en")
```

**代码位置**: `app/frontend/src/i18n/config.ts`

### 2. AI 语音回答语言

GPT-4o Realtime API 是多语言模型，会自动：
- 🎤 检测用户说话的语言
- 💬 用相同的语言回答
- 🌍 支持 50+ 种语言（包括中文）

**行为示例**:
```
用户用西班牙语说: "¿Qué es Contoso?"
→ AI 用西班牙语回答: "Contoso Electronics es una empresa..."

用户用英语说: "What is Contoso?"
→ AI 用英语回答: "Contoso Electronics is a company..."

用户用中文说: "Contoso 是什么？"
→ AI 用中文回答: "Contoso Electronics 是一家..."
```

---

## 🎯 为什么有些用户看到西班牙语界面？

### 原因分析

**场景 1: 浏览器语言设置为西班牙语**
```
用户浏览器设置: 首选语言 = Español
→ 应用检测到西班牙语
→ 界面显示为西班牙语
→ 按钮显示 "Iniciar conversación" 而不是 "Start conversation"
```

**场景 2: 用户在西班牙语地区**
```
用户地理位置: 西班牙/拉丁美洲
→ 浏览器默认语言可能是西班牙语
→ 应用自动切换到西班牙语
```

**场景 3: 用户之前选择了西班牙语**
```
用户之前访问时手动选择了西班牙语
→ 选择保存在 localStorage
→ 下次访问自动使用西班牙语
```

---

## 🛠️ 解决方案

### 方案 1: 用户手动切换浏览器语言

#### Chrome / Edge
1. 打开浏览器设置 (Settings)
2. 搜索 "Language" 或 "语言"
3. 点击 "Language" 部分
4. 将 "English" 拖到列表顶部
5. 重启浏览器或刷新页面

#### Firefox
1. 打开设置 (Preferences)
2. 选择 "General" → "Language"
3. 点击 "Choose" 选择首选语言
4. 将 "English" 设为第一优先级
5. 刷新页面

#### Safari
1. 打开系统偏好设置 (System Preferences)
2. 选择 "Language & Region"
3. 将 "English" 拖到首位
4. 重启浏览器

---

### 方案 2: 使用应用内语言选择器（推荐）

我们已经添加了界面语言选择器，位于应用右上角：

**功能**:
- 🌐 显示地球图标
- 📋 下拉菜单列出所有支持的语言
- 💾 自动保存用户选择到 localStorage
- 🔄 立即切换界面语言

**使用方法**:
1. 访问应用
2. 点击右上角的语言选择器 (🌐)
3. 选择您想要的语言
4. 界面立即切换

**代码位置**: 
- 组件: `app/frontend/src/components/ui/language-selector.tsx`
- 集成: `app/frontend/src/App.tsx`

---

### 方案 3: 强制默认语言为英语

如果您希望所有用户默认看到英语界面，修改配置：

**编辑**: `app/frontend/src/i18n/config.ts`

```typescript
.init({
    // ... 其他配置
    fallbackLng: "en",
    detection: {
        // 注释掉浏览器检测，只使用手动选择
        // order: ['localStorage', 'navigator'],
        order: ['localStorage'],
        caches: ['localStorage'],
        lookupLocalStorage: 'userLanguage'
    },
    // 强制使用英语（如果未找到 localStorage 中的选择）
    lng: "en"  // 添加这一行
})
```

**注意**: 这会覆盖浏览器语言检测，所有新用户默认看到英语界面。

---

## 📝 配置文件说明

### 1. 国际化配置
**文件**: `app/frontend/src/i18n/config.ts`

```typescript
export const supportedLngs: { [key: string]: { name: string; locale: string } } = {
    en: { name: "English", locale: "en-US" },
    es: { name: "Español", locale: "es-ES" },
    fr: { name: "Français", locale: "fr-FR" },
    ja: { name: "日本語", locale: "ja-JP" }
};
```

### 2. 翻译文件
**目录**: `app/frontend/src/locales/`

```
locales/
├── en/translation.json  # 英语翻译
├── es/translation.json  # 西班牙语翻译
├── fr/translation.json  # 法语翻译
└── ja/translation.json  # 日语翻译
```

### 3. 语音配置
**文件**: `app/backend/app.py`

```python
rtmt = RTMiddleTier(
    # ...
    voice_choice=os.environ.get("AZURE_OPENAI_REALTIME_VOICE_CHOICE") or "alloy"
)
```

**可用的语音选项**:
- `alloy` - 中性，平衡 (默认)
- `echo` - 男性，温暖
- `shimmer` - 女性，温和

**更改语音**:
```bash
azd env set AZURE_OPENAI_REALTIME_VOICE_CHOICE shimmer
azd up
```

---

## 🌍 添加新语言支持

如果您想添加中文界面翻译：

### 步骤 1: 创建翻译文件
```bash
mkdir app/frontend/src/locales/zh
```

创建 `app/frontend/src/locales/zh/translation.json`:
```json
{
    "app": {
        "title": "与您的数据对话",
        "startConversation": "开始对话",
        "stopConversation": "停止对话",
        "footer": "由 Azure OpenAI 和 Azure AI Search 提供支持"
    }
}
```

### 步骤 2: 更新配置
编辑 `app/frontend/src/i18n/config.ts`:

```typescript
import zhTranslation from "../locales/zh/translation.json";

export const supportedLngs = {
    // ... 现有语言
    zh: {
        name: "中文",
        locale: "zh-CN"
    }
};

i18next.init({
    resources: {
        // ... 现有资源
        zh: { translation: zhTranslation }
    },
    // ...
});
```

### 步骤 3: 重新构建前端
```bash
cd app/frontend
npm run build
```

---

## 🔧 环境变量

### 语音相关环境变量

| 变量名 | 说明 | 默认值 | 可选值 |
|--------|------|--------|--------|
| `AZURE_OPENAI_REALTIME_VOICE_CHOICE` | AI 语音选择 | `alloy` | `alloy`, `echo`, `shimmer` |

**设置方法**:
```bash
# 通过 azd
azd env set AZURE_OPENAI_REALTIME_VOICE_CHOICE shimmer

# 或在 .env 文件中
AZURE_OPENAI_REALTIME_VOICE_CHOICE=shimmer
```

---

## 🎯 最佳实践

### 1. 为国际用户优化
- ✅ 保留自动语言检测
- ✅ 添加语言选择器让用户手动切换
- ✅ 使用 localStorage 记住用户选择

### 2. 为单一语言环境优化
- ✅ 设置 `lng: "en"` 强制默认语言
- ✅ 隐藏语言选择器（如果只需要一种语言）
- ✅ 移除不需要的翻译文件

### 3. 测试多语言功能
```bash
# 测试西班牙语
localStorage.setItem('userLanguage', 'es');
location.reload();

# 测试法语
localStorage.setItem('userLanguage', 'fr');
location.reload();

# 重置为英语
localStorage.setItem('userLanguage', 'en');
location.reload();
```

---

## 📊 用户语言分析

如果您想了解用户的语言偏好，可以添加分析：

```typescript
// 在 App.tsx 中
import { useTranslation } from "react-i18next";

function App() {
    const { i18n } = useTranslation();
    
    useEffect(() => {
        // 记录用户语言
        console.log('User language:', i18n.language);
        
        // 可选: 发送到分析服务
        // analytics.track('language_detected', { language: i18n.language });
    }, [i18n.language]);
    
    // ...
}
```

---

## ❓ 常见问题

### Q: 为什么界面是西班牙语但 AI 用英语回答？

**A**: 界面语言和 AI 回答语言是独立的：
- 界面语言由浏览器设置决定
- AI 回答语言由您说话的语言决定

**解决**: 用西班牙语提问，AI 会用西班牙语回答。

---

### Q: 如何完全禁用语言检测？

**A**: 修改 `i18n/config.ts`:
```typescript
.init({
    lng: "en",  // 强制英语
    detection: {
        order: [], // 禁用所有检测
    }
})
```

---

### Q: 可以添加更多语言吗？

**A**: 可以！按照 "添加新语言支持" 部分的步骤操作。

---

### Q: AI 语音支持哪些语言？

**A**: GPT-4o Realtime API 支持 50+ 种语言，包括：
- 英语、西班牙语、法语、德语、意大利语
- 中文、日语、韩语
- 俄语、阿拉伯语、葡萄牙语
- 等等...

完整列表请参考: [Azure OpenAI 文档](https://learn.microsoft.com/azure/ai-services/openai/concepts/models#model-languages)

---

## 🚀 部署后更新语言设置

### 更新语音选择
```bash
# 设置新的语音
azd env set AZURE_OPENAI_REALTIME_VOICE_CHOICE echo

# 重新部署
azd deploy
```

### 更新界面翻译
```bash
# 修改翻译文件后
cd app/frontend
npm run build

# 重新部署
cd ../..
azd deploy
```

---

## 📚 相关资源

- [i18next 文档](https://www.i18next.com/)
- [React i18next 文档](https://react.i18next.com/)
- [Azure OpenAI Realtime API](https://learn.microsoft.com/azure/ai-services/openai/how-to/real-time-audio)
- [浏览器语言检测](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/language)

---

**最后更新**: 2025年10月  
**维护者**: VoiceRAG 团队




