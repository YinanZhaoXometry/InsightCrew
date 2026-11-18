"""受限执行沙箱：跑模型生成的 Python 代码。

三层防护，答辩时按这个顺序讲：
    1) 静态检查（AST）：禁止导入 os/subprocess/socket 等，禁止 eval/exec；
    2) 进程隔离：子进程 + 独立临时目录 + 最小环境变量（不把 API key 传进去）；
    3) 资源限制：超时 + 输出截断。

为什么不直接 exec()：
    同进程执行意味着生成的代码能读到 os.environ["IC_API_KEY"]、
    能删除工作区文件、能发网络请求。这是安全事故，不是严格的体现。

注意：这是课程项目的合理防护，生产环境应换 Docker / seccomp。
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

BANNED_MODULES = {
    "os", "sys", "subprocess", "socket", "shutil", "pathlib", "importlib",
    "ctypes", "urllib", "http", "requests", "multiprocessing", "threading",
    "pickle", "marshal", "builtins", "code", "codeop",
}
BANNED_CALLS = {
    "eval", "exec", "compile", "__import__", "globals", "locals",
    "setattr", "delattr", "breakpoint", "input", "open_", "system",
}
TB_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Interrupt))", re.M)


class SandboxError(RuntimeError):
    """代码没通过静态检查。"""


def static_check(code: str) -> None:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise SandboxError(f"语法错误：{e.msg}（第 {e.lineno} 行）") from e

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in BANNED_MODULES:
                    raise SandboxError(f"禁止导入模块 {a.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod in BANNED_MODULES:
                raise SandboxError(f"禁止从 {node.module} 导入")
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else "")
            if name in BANNED_CALLS:
                raise SandboxError(f"禁止调用 {name}()")


def _parse_result(stdout: str) -> dict | None:
    """约定：代码最后打印一行 `RESULT:{json}`，沙箱负责解析。

    这是"结构化输出契约"——没有它，上层只能靠正则猜数字。
    """
    for line in reversed(stdout.splitlines()):
        if line.startswith("RESULT:"):
            payload = line[len("RESULT:"):].strip()
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                # 模型经常把 RESULT 写成 Python 字典字面量（单引号），不是 JSON。
                # 语义完全正确，只是引号不同——不该判失败、让它白花一次调用重写。
                # 实测：不兜这一下的那一次运行，a1 的 value 是 None，多花了 1 次模型调用。
                try:
                    obj = ast.literal_eval(payload)
                except (ValueError, SyntaxError):
                    return None
                return obj if isinstance(obj, dict) else None
    return None


def run(code: str, data_files: list[str], timeout: float = 30.0) -> dict:
    """执行代码，返回结构化结果。不抛异常——失败信息本身是给 agent 的输入。"""
    try:
        static_check(code)
    except SandboxError as e:
        return {"ok": False, "stdout": "", "stderr": f"SandboxError: {e}",
                "tb_type": "SandboxError", "elapsed": 0.0, "result": None, "workdir": None}

    workdir = tempfile.mkdtemp(prefix="ic_sbx_")
    for p in data_files:
        if os.path.exists(p):
            shutil.copy(p, workdir)
    prog = os.path.join(workdir, "prog.py")
    with open(prog, "w", encoding="utf-8") as f:
        f.write(code)

    # 最小环境：故意不继承 os.environ，避免 API key 泄漏给生成代码。
    # TEMP/TMP 指向沙箱自己的目录，子进程写临时文件也出不这个门。
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8",
           "PYTHONHASHSEED": "0", "LANG": "C.UTF-8",
           "TEMP": workdir, "TMP": workdir}
    if os.name == "nt":
        # Windows + Python 3.8 必需：缺 SystemRoot 会拿不到随机熵，
        # 子进程直接 "Fatal Python error: _Py_HashRandomization_Init"。
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT") or os.environ.get("SystemRoot", r"C:\Windows")

    t0 = time.time()
    ok, out, err = False, "", ""
    try:
        # -I 隔离模式 + -X utf8 强制 UTF-8。
        # 两者缺一不可：-I 会忽略 PYTHONIOENCODING 等环境变量，
        # 于是 Windows 上子进程按 GBK 输出，中文全变乱码（"???")——
        # 代码里的中文 method 说明会毁掉回放页面，也会让模型看不懂自己的报错。
        p = subprocess.run([sys.executable, "-I", "-X", "utf8", "prog.py"],
                           cwd=workdir, env=env,
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        ok, out, err = p.returncode == 0, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        err = f"TimeoutExpired: 执行超过 {timeout}s 被强制终止"
    elapsed = time.time() - t0

    m = TB_RE.findall(err or "")
    tb_type = m[-1].split(".")[-1] if m else ("" if ok else "UnknownError")

    return {"ok": ok, "stdout": (out or "")[:4000], "stderr": (err or "")[:4000],
            "tb_type": tb_type, "elapsed": round(elapsed, 3),
            "result": _parse_result(out or ""), "workdir": workdir if not ok else None}
