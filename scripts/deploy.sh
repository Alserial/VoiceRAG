#!/bin/bash
# VoiceRAG 部署脚本
# 用于将本地更改部署到 Azure (Mac/Linux 版本)

set -e  # 遇到错误立即退出

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GRAY='\033[0;37m'
NC='\033[0m' # No Color

echo -e "${CYAN}=== VoiceRAG 部署到 Azure ===${NC}"

# 步骤 1: 检查是否在正确的目录
if [ ! -f "azure.yaml" ]; then
    echo -e "${RED}错误: 请在项目根目录运行此脚本${NC}" >&2
    exit 1
fi

# 步骤 2: 构建前端
echo ""
echo -e "${YELLOW}[1/3] 构建前端...${NC}"
cd app/frontend
npm run build
if [ $? -ne 0 ]; then
    echo -e "${RED}前端构建失败!${NC}" >&2
    cd ../..
    exit 1
fi
cd ../..
echo -e "${GREEN}前端构建成功!${NC}"

# 步骤 3: 检查 azd 环境
echo ""
echo -e "${YELLOW}[2/3] 检查 azd 环境...${NC}"
ENV_LIST=$(azd env list --output json)
DEFAULT_ENV=$(echo "$ENV_LIST" | grep -o '"IsDefault":\s*true' | head -1)

if [ -z "$DEFAULT_ENV" ]; then
    echo -e "${RED}错误: 没有找到 azd 环境${NC}" >&2
    echo -e "${YELLOW}请先运行: azd env select <环境名称>${NC}" >&2
    exit 1
fi

ENV_NAME=$(echo "$ENV_LIST" | grep -B 5 '"IsDefault":\s*true' | grep '"Name"' | head -1 | sed 's/.*"Name":\s*"\([^"]*\)".*/\1/')
echo -e "${GREEN}使用环境: $ENV_NAME${NC}"

# 步骤 4: 部署到 Azure
echo ""
echo -e "${YELLOW}[3/3] 部署到 Azure...${NC}"
echo -e "${YELLOW}这可能需要 3-5 分钟，请耐心等待...${NC}"
echo ""

azd deploy --service backend

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}=== 部署成功! ===${NC}"
    echo ""
    
    # 获取应用 URL
    ENV_VALUES=$(azd env get-values)
    BACKEND_URI=$(echo "$ENV_VALUES" | grep "BACKEND_URI" | sed 's/.*BACKEND_URI="\([^"]*\)".*/\1/')
    
    if [ -n "$BACKEND_URI" ]; then
        echo -e "${CYAN}应用 URL: $BACKEND_URI${NC}"
    else
        echo -e "${YELLOW}提示: 运行 'azd show' 查看应用 URL${NC}"
    fi
    
    echo ""
    echo -e "${YELLOW}请访问应用并验证:${NC}"
    echo -e "${GRAY}  1. 页面右下角显示版本号 v2.2.0${NC}"
    echo -e "${GRAY}  2. 测试调试面板功能${NC}"
    echo -e "${GRAY}  3. 测试语音确认功能${NC}"
    echo -e "${GRAY}  4. 确认语言保持英语${NC}"
else
    echo ""
    echo -e "${RED}=== 部署失败 ===${NC}" >&2
    echo -e "${YELLOW}请检查错误信息并重试${NC}" >&2
    exit 1
fi
