#!/bin/bash
# 上传新数据文件到 Azure AI Search
# 使用方法: 将新文件放入 data/ 目录，然后运行此脚本

echo "=== 上传新数据文件到 VoiceRAG ==="

# 加载 Python 虚拟环境
echo -e "\n[1/3] 加载 Python 虚拟环境..."
./scripts/load_python_env.sh

# 检测 Python 路径
if [ -f "./.venv/bin/python" ]; then
    VENV_PYTHON="./.venv/bin/python"
else
    VENV_PYTHON="./.venv/scripts/python.exe"
fi

# 检查 data/ 目录是否存在
if [ ! -d "data" ]; then
    echo "错误: data/ 目录不存在!"
    exit 1
fi

# 显示 data/ 目录中的文件
echo -e "\n[2/3] data/ 目录中的文件:"
ls -lh data/

# 运行上传脚本
echo -e "\n[3/3] 上传文件并触发索引..."
$VENV_PYTHON app/backend/setup_intvect.py

echo -e "\n✅ 完成! 文件已上传到 Azure Blob Storage。"
echo "   索引器将在几分钟内自动处理新文件。"
echo -e "\n💡 提示: 可以在 Azure Portal 中查看索引进度"
echo "   Azure Portal > AI Search > 索引器 > 运行历史记录"




