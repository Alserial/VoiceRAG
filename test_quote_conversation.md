# 报价功能测试对话脚本

## 测试场景 1: 完整信息（一次性提供所有信息）

**用户**: "Hi, I need a quote for 10 units of Product A. My name is John Smith and my email is john.smith@example.com. I need it to start by January 15th, 2025. Please note that I need fast delivery."

**预期结果**: 
- 系统应该检测到报价请求
- 提取所有信息：姓名、邮箱、产品、数量、日期、备注
- 确认信息并告知将准备报价

---

## 测试场景 2: 部分信息（需要询问缺失信息）

**用户**: "I'd like to get a quote please."

**系统预期回复**: "I'd be happy to help you with a quote. I need some information: What is your name?"

**用户**: "My name is Sarah Johnson."

**系统预期回复**: "Thank you Sarah. What is your email address?"

**用户**: "sarah.j@company.com"

**系统预期回复**: "Which product are you interested in? Available products: [列出产品列表]"

**用户**: "I need Product B"

**系统预期回复**: "What quantity do you need?"

**用户**: "I need 5 units"

**系统预期回复**: "Perfect! I have all the information. I'll prepare a quote for you."

---

## 测试场景 3: 产品名称模糊匹配

**用户**: "Hi, I need a quote. My name is Mike Chen, email is mike@test.com. I'm interested in Product C, but I'm not sure about the exact name. I think it's called 'Pro C' or something similar. I need 20 units."

**预期结果**:
- 系统应该能够匹配到 "Product C"（即使用户说的是 "Pro C"）
- 提取其他信息
- 询问缺失的日期信息

---

## 测试场景 4: 分多次提供信息

**用户**: "Can you help me get a price estimate?"

**系统**: "I'd be happy to help. What is your name?"

**用户**: "David Wilson"

**系统**: "Thank you David. What is your email address?"

**用户**: "david.wilson@business.com"

**系统**: "Which product are you interested in?"

**用户**: "I'm looking at Product A"

**系统**: "What quantity do you need?"

**用户**: "15 units please"

**系统**: "When do you need this to start?"

**用户**: "March 1st, 2025"

**系统**: "Great! I have all the information. I'll prepare your quote."

---

## 测试场景 5: 包含备注信息

**用户**: "I need a quote for Product B. I'm Lisa Anderson, lisa.anderson@email.com. I need 8 units. We need to start by February 20th. Also, please note that we require installation support."

**预期结果**:
- 提取所有信息包括备注 "installation support"
- 信息完整，系统确认

---

## 测试场景 6: 产品名称完全不匹配

**用户**: "Hi, I want a quote. My name is Tom Brown, tom@email.com. I need something called 'XYZ Widget' - 12 units."

**预期结果**:
- 系统无法匹配产品（如果 "XYZ Widget" 不在产品列表中）
- 系统应该询问正确的产品名称或列出可用产品

---

## 快速测试脚本（推荐先用这个）

### 简单测试（信息完整）
**你说**: "I need a quote for Product A. My name is John Doe, email is john@test.com. I need 10 units."

**系统应该**: 检测到报价请求，提取信息，可能询问日期，然后确认。

### 中等测试（缺少信息）
**你说**: "I'd like to get a quote please."

**系统应该**: 开始询问你的姓名、邮箱、产品、数量等信息。

**你继续**: "My name is Jane Smith, jane@test.com, I need Product B, 5 units."

**系统应该**: 确认信息或询问缺失的日期。

---

## 测试要点

1. ✅ **检测报价请求**: 说 "quote", "quotation", "price estimate" 等关键词
2. ✅ **信息提取**: 系统应该能从对话中提取姓名、邮箱、产品、数量等
3. ✅ **产品匹配**: 如果产品名称不完全匹配，系统应该找到最相似的产品
4. ✅ **智能询问**: 如果信息不完整，系统应该逐个询问缺失的信息
5. ✅ **确认信息**: 当信息完整时，系统应该确认并告知将准备报价

---

## 注意事项

- 说话要清晰，因为系统使用语音转录
- 可以分多次提供信息，系统会记住之前的对话
- 产品名称不需要完全准确，系统会尝试匹配
- 如果系统询问信息，请耐心回答

