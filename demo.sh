#!/usr/bin/env bash
# InsightCrew 固定演示(macOS / Linux 版),与 demo.bat 等价:
# 跑默认问题(离线模式输出每次一致,适合录屏),然后生成可视化回放。
#
# 用法:
#   bash demo.sh                        # 最省事,不需要执行权限
#   chmod +x demo.sh && ./demo.sh       # 赋权后可直接运行
set -u
cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8

echo "=================================================="
echo "  InsightCrew  -  Fixed demo run"
echo "  Runs the default question, then builds the report."
echo "=================================================="
echo

# ---- 挑解释器(与 start.sh 相同的顺序)----
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
if ! "$PYEXE" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"; then
    echo "[ERROR] $($PYEXE -V) 版本低于 3.9,本项目需要 Python 3.9+。"
    exit 1
fi
echo "Interpreter:"
"$PYEXE" -V
echo

"$PYEXE" run_demo.py || exit 1
echo
"$PYEXE" -m ui.render_report
echo
LATEST=$(ls -1t runs 2>/dev/null | head -1)
echo "Report folder: $(pwd)/runs"
if [ -n "${LATEST:-}" ] && [ -f "runs/$LATEST/report.html" ]; then
    echo "Newest report: $(pwd)/runs/$LATEST/report.html"
    echo "在浏览器打开:open \"$(pwd)/runs/$LATEST/report.html\""
fi
