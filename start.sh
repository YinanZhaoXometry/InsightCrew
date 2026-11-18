#!/usr/bin/env bash
# InsightCrew 启动器(macOS / Linux 版),与 start.bat 等价:
# 自动挑选可用的 Python 3.9+,交互式提问,跑完自动打开回放页面。
#
# 用法:
#   bash start.sh                                  # 最省事,不需要执行权限
#   chmod +x start.sh && ./start.sh                # 赋权后可直接运行
#   ./start.sh "八月华南区的营收是多少?" --compare   # 参数原样传给 ask.py
#
# 注:macOS 下双击 .sh 默认会用文本编辑器打开;
#     想双击运行请右键 → 打开方式 → 终端(实用),或直接在终端里 bash start.sh。
set -u
cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8

echo "=================================================="
echo "  InsightCrew  -  Multi-Agent Data Analyst"
echo "  Question in, audited number out."
echo "=================================================="
echo

# ---- 挑解释器(macOS 没有 py 启动器,依次试 python3 / python)----
PYEXE=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "import sys" >/dev/null 2>&1; then
        PYEXE="$cand"
        break
    fi
done
if [ -z "$PYEXE" ]; then
    echo "[ERROR] No usable Python found."
    echo "Install Python 3.9+ from https://www.python.org/downloads/"
    echo "(or: brew install python)"
    exit 1
fi
# 本项目实际需要 3.9+(core/orchestrator.py 用了 str.removesuffix)
if ! "$PYEXE" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"; then
    echo "[ERROR] $($PYEXE -V) 版本低于 3.9,本项目需要 Python 3.9+。"
    exit 1
fi
echo "Interpreter:"
"$PYEXE" -V
echo

exec "$PYEXE" ask.py "$@"
