#!/usr/bin/env bash
# 一条命令跑完版本 2.3 的证明（BLUEPRINT 20.4、20.12）：
#   1-3  三项检查：pytest / ruff / pyright
#   4    uv run insightcrew scenarios      —— 每个既有场景 ✓ 相符
#   5    每份归档 IC_FORCE_OFFLINE=1 重放   —— 一致
#   6    uv run insightcrew eval all       —— 退出 0
# 全部阶段都会跑完并各打印一行结果；任一阶段失败，脚本以 1 退出。
# 从仓库内的任何目录调用都可以。
set -u

cd "$(dirname "$0")/.." || exit 1

if command -v uv >/dev/null 2>&1; then
    UV=uv
elif [ -x "$HOME/.local/bin/uv" ]; then
    UV="$HOME/.local/bin/uv"
else
    echo "找不到 uv（PATH 与 ~/.local/bin 里都没有），无法继续。" >&2
    exit 1
fi

failures=0

stage() {
    # 用法：stage <阶段名> <命令…>。跑一个阶段，打印它的结果行。
    name="$1"
    shift
    echo
    echo "──── $name ────"
    "$@"
    if [ "$?" -eq 0 ]; then
        echo "结果：$name 通过"
    else
        echo "结果：$name 失败"
        failures=$((failures + 1))
    fi
}

stage "1/6 pytest（三项检查之一）" "$UV" run pytest -q
stage "2/6 ruff（三项检查之二）" "$UV" run ruff check .
stage "3/6 pyright（三项检查之三）" "$UV" run pyright
stage "4/6 scenarios 场景证明" "$UV" run insightcrew scenarios

echo
echo "──── 5/6 replay 归档重放（每份一次）────"
ok=0
bad=0
for d in insightcrew/evals/archive/*/; do
    [ -d "$d" ] || continue
    out="$(IC_FORCE_OFFLINE=1 "$UV" run insightcrew replay "$d" 2>&1)"
    if [ "$?" -eq 0 ]; then
        ok=$((ok + 1))
    else
        bad=$((bad + 1))
        echo "不一致：$d"
        echo "$out"
    fi
done
if [ "$bad" -eq 0 ] && [ "$ok" -gt 0 ]; then
    echo "结果：5/6 replay 通过，$ok 份归档全部一致"
else
    [ "$ok" -gt 0 ] || echo "没有找到任何归档目录（insightcrew/evals/archive/ 为空？）"
    echo "结果：5/6 replay 失败（一致 $ok 份，不一致 $bad 份）"
    failures=$((failures + 1))
fi

stage "6/6 eval all 评测证明" "$UV" run insightcrew eval all

echo
if [ "$failures" -eq 0 ]; then
    echo "check.sh：全部阶段通过。"
    exit 0
else
    echo "check.sh：$failures 个阶段失败。"
    exit 1
fi
