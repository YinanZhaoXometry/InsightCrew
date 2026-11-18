"""接入真模型前的体检脚本。

它回答三个问题：
    1) 系统现在读到了哪把 key、指向哪个模型；
    2) 这个 key / 地址 / 模型名到底通不通（不是猜，是发一次真实请求）；
    3) 通了之后，跑一道题会得到什么，和离线剧本差在哪。

用法：
    python check_api.py                    # 只体检连通性
    python check_api.py --run              # 体检 + 跑一道题（会花几分钱）
    python check_api.py --run "问题..."     # 指定题目
"""
from __future__ import annotations

import argparse
import sys
import time

from config import Config
from core.llm import LLM


def _mask(key: str) -> str:
    return f"{key[:6]}...{key[-4:]}（长 {len(key)}）" if len(key) > 12 else "（已设置）"


def _explain(msg: str) -> str:
    """把 HTTP 状态码翻译成"你该改哪一行"。"""
    table = {
        "401": "key 无效或已过期 → 检查 IC_API_KEY 是否抄全、有没有多余空格",
        "403": "key 无权访问该模型 → 换模型名，或确认账号已开通",
        "404": "地址或模型名不对 → 检查 IC_BASE_URL 是否以 /v1 结尾、IC_MODEL 拼写",
        "429": "触发限流或余额不足 → 等一会儿，或充值",
        "402": "余额不足 → 充值后再试",
        "400": "请求被拒 → 多半是模型名不存在，或该模型不支持 JSON 模式",
        "500": "服务端错误 → 稍后重试",
        "502": "网关错误 → 稍后重试",
        "503": "服务不可用 → 稍后重试",
    }
    for code, tip in table.items():
        if code in msg:
            return tip
    if "timed out" in msg.lower() or "urlopen" in msg.lower():
        return "网络不通 → 检查是否开了代理、地址是否可访问"
    return "看上面的原始报错"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?", default="2026年第三季度华东区的营收是多少？")
    ap.add_argument("--run", action="store_true", help="体检通过后真跑一道题")
    args = ap.parse_args()

    cfg = Config()
    print("=" * 60)
    print("1) 当前配置")
    print("=" * 60)
    print(f"  IC_BASE_URL = {cfg.base_url}")
    print(f"  IC_MODEL    = {cfg.model}")
    print(f"  IC_API_KEY  = {_mask(cfg.api_key) if cfg.api_key else '（未设置）'}")
    if cfg.api_key:
        print(f"  key 来源    = {cfg.key_source}")
    print(f"  运行模式    = {'离线剧本' if cfg.offline else '真实模型'}")

    if cfg.offline:
        print()
        print("现在没有可用的 key，所以跑的是离线剧本：编排/沙箱/审计都是真的，")
        print("只有『写代码』这一步由规则生成。要接真模型，二选一：")
        print()
        print("  方式 A（推荐，双击也能生效）：")
        print("    把 .env.example 复制成 .env，在 IC_API_KEY= 后面粘贴 key")
        print()
        print("  方式 B（只对当前 cmd 窗口有效）：")
        print("    set IC_API_KEY=sk-你的key")
        print()
        return 1

    print()
    print("=" * 60)
    print("2) 发一次真实请求（1 个 token 级别的小请求）")
    print("=" * 60)
    llm = LLM(cfg)
    t0 = time.time()
    try:
        out = llm.chat_json(
            "probe",
            '你是一个连通性探针。只输出 JSON：{"ok": true, "echo": "pong"}',
            "ping",
        )
    except Exception as e:                          # noqa: BLE001
        msg = str(e)
        print(f"  ✗ 失败（{time.time() - t0:.1f}s）")
        print(f"    原始报错：{msg[:300]}")
        print(f"    可能原因：{_explain(msg)}")
        return 2

    dt = time.time() - t0
    print(f"  ✓ 通了（{dt:.1f}s，{llm.tokens} tokens）")
    print(f"    模型返回：{out}")
    if llm.used_fallback:
        print("    注意：该接口不支持 response_format，已自动降级为文本解析（能跑，但更依赖模型听话）")
    if dt > 30:
        print("    提醒：单次响应偏慢，建议把 IC_MAX_SECONDS 调到 600，否则多轮会触发时间预算降级")

    est = 0.002 * 3 * 2       # 粗估：一道题 2 轮 × 3 次调用
    print(f"    成本粗估：一道题 2 轮约 6 次调用，量级 ¥{est:.3f}（按 deepseek-chat 价）")

    if not args.run:
        print()
        print("体检通过。要真跑一道题：python check_api.py --run")
        return 0

    print()
    print("=" * 60)
    print("3) 端到端跑一道题（真实模型）")
    print("=" * 60)
    from core.orchestrator import Orchestrator      # 延迟导入，避免体检阶段的重依赖

    t1 = time.time()
    orch = Orchestrator(args.question, cfg=cfg, verbose=False)
    bb = orch.run()
    print(f"  问题：{args.question}")
    print(f"  轮数：{bb.turn}　耗时：{time.time() - t1:.1f}s　tokens：{bb.tokens}")
    print(f"  判定链：{['%s(%s)' % (c.verdict, c.layer) for c in bb.critiques]}")
    v = bb.latest_value
    print(f"  结论：{bb.target_metric or '-'} = {v:,.2f}" if v is not None else "  结论：未给出数字")
    if bb.degraded:
        print(f"  降级：{bb.notes[-1] if bb.notes else '-'}")
    print(f"  回放：runs/{orch.run_id}")
    print()
    print("对比离线剧本（同一题）：python ask.py \"%s\" --no-open --quiet" % args.question)
    return 0


if __name__ == "__main__":
    sys.exit(main())
