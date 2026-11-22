#!/usr/bin/env bash
# 给 Cloudflare Pages 的后台 git 集成构建用。后台只需两行配置：
#   构建命令（Build command）:        bash scripts/pages-build.sh
#   输出目录（Build output directory）: _pages
# 步骤：
#   1    装好 uv（构建镜像没有就用 python3 -m pip 装）
#   2    uv sync --frozen                    —— 按 uv.lock 复现环境
#   3    IC_FORCE_OFFLINE=1 跑离线 demo      —— 脚本化模型、合成 ACME 数据，零模型花费
#   4    最新 replay.html → _pages/index.html（自包含单页，2026-11-21 手动部署的同法）
#   5    密钥扫描：命中即失败，不发布
# 任一步失败脚本以 1 退出（构建失败 = 不部署）。本地也能跑：产物 _pages/ 已 gitignore。
set -eu

cd "$(dirname "$0")/.." || exit 1

export PATH="$HOME/.local/bin:$PATH"

if command -v uv >/dev/null 2>&1; then
    UV=uv
else
    echo "· 构建环境没有 uv，用 pip 装一个"
    python3 -m pip install --quiet uv
    UV=uv
fi

echo "· uv sync --frozen"
"$UV" sync --frozen

echo "· 生成离线 demo（脚本化模型、合成 ACME 数据，零花费）"
IC_FORCE_OFFLINE=1 "$UV" run insightcrew analyze --scenario demo --no-open --quiet

echo "· 取最新回放页"
mkdir -p _pages
cp "$(ls -t runs/*/replay.html | head -1)" _pages/index.html

echo "· 密钥扫描（精确模式：裸 sk- 会误中 risk-rounds，不能用）"
if grep -Eq 'sk-[A-Za-z0-9_-]{16,}|eyJ[A-Za-z0-9_-]{10,}|Bearer [A-Za-z0-9]|IC_API_KEY|onezhaoyn' _pages/index.html; then
    echo "回放页疑似含密钥或账号信息，拒绝部署。" >&2
    exit 1
fi

echo "pages-build.sh：完成。输出 _pages/index.html（$(wc -c < _pages/index.html) 字节）。"
