"""全局配置。所有可调参数集中在这里，避免散落在各个 agent 里。

教学要点：
    把"预算"（轮数/时间/token）做成显式配置，而不是靠感觉停机。
    这是多智能体系统能不能稳定演示的第一道保险。
"""
from __future__ import annotations

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
RUNS_DIR = os.path.join(BASE_DIR, "runs")
ENV_FILE = os.path.join(BASE_DIR, ".env")


def _load_dotenv(path: str = ENV_FILE) -> dict:
    """读项目根目录的 .env（极简实现，不引入 python-dotenv）。

    为什么需要：双击 start.bat 时进程读不到你临时 export 的变量，
    把 key 写在 .env 里才能"一次填好、双击就跑"。
    已存在的系统环境变量优先，不会被 .env 覆盖。
    """
    loaded = {}
    if not os.path.exists(path):
        return loaded
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if not k:
                continue
            if os.getenv(k):
                continue                 # 系统环境变量优先
            os.environ[k] = v
            loaded[k] = v
    return loaded


_DOTENV = _load_dotenv()


class Config:
    def __init__(self, **kw):
        # ---- 模型（任何 OpenAI 兼容接口都可以）----
        self.api_key = kw.get("api_key", os.getenv("IC_API_KEY", ""))
        # key 从哪来，用于 check_api.py 的排错提示
        if kw.get("api_key"):
            self.key_source = "构造参数"
        elif _DOTENV.get("IC_API_KEY"):
            self.key_source = ".env 文件"
        elif os.getenv("IC_API_KEY"):
            self.key_source = "系统环境变量"
        else:
            self.key_source = ""
        self.base_url = kw.get("base_url", os.getenv("IC_BASE_URL", "https://api.deepseek.com/v1"))
        self.model = kw.get("model", os.getenv("IC_MODEL", "deepseek-chat"))
        self.temperature = float(kw.get("temperature", 0.0))

        # ---- 终止条件（三重，缺一不可）----
        # 支持 kw 显式传入，也支持环境变量覆盖（演示现场改预算不用改代码）
        self.max_turns = int(kw.get("max_turns", os.getenv("IC_MAX_TURNS", 12)))
        self.max_seconds = float(kw.get("max_seconds", os.getenv("IC_MAX_SECONDS", 240)))
        self.max_tokens = int(kw.get("max_tokens", os.getenv("IC_MAX_TOKENS", 200_000)))
        self.max_repeat_issue = int(kw.get("max_repeat_issue", os.getenv("IC_MAX_REPEAT", 2)))

        # ---- 沙箱 ----
        self.sandbox_timeout = float(kw.get("sandbox_timeout",
                                            os.getenv("IC_SANDBOX_TIMEOUT", 30)))

        # ---- 离线剧本模式：没有 API key 时自动启用，用于演示与单元测试 ----
        # IC_FORCE_OFFLINE=1 可以强制离线（配了 key 也想录稳定 demo 时用）
        self.force_offline = bool(kw.get("force_offline", False)) or \
            os.getenv("IC_FORCE_OFFLINE", "").strip().lower() in ("1", "true", "yes", "on")

    @property
    def offline(self) -> bool:
        return self.force_offline or not self.api_key
