#!/usr/bin/env sh
# 安装依赖并启动本地网页。需要 uv；没有密钥时只运行离线演示。
set -e
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
    echo "没有找到 uv。先安装它，再重新运行本脚本："
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "其他安装方式：https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

uv sync
exec uv run insightcrew serve "$@"
