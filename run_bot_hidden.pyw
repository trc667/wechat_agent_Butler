# -*- coding: utf-8 -*-
"""无窗口守护进程：循环启动 ilink_bot.py，崩溃 5 秒后自动重启。

用法（不用自己开终端）：
    pythonw run_bot_hidden.pyw

日志写到 logs/bot.log（启动时旧日志备份为 logs/bot_old.log）。
会话过期时日志里会打印扫码链接，浏览器打开 + 手机微信扫即可恢复。
"""
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE, "logs")
LOG = os.path.join(LOG_DIR, "bot.log")
OLD = os.path.join(LOG_DIR, "bot_old.log")


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))


def _can_import_requests(python):
    """该 python 解释器能不能 import requests（依赖装在哪就得用哪个 python）。"""
    if not os.path.isfile(python):
        return False
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags = subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        r = subprocess.run([python, "-c", "import requests"], capture_output=True,
                           timeout=15, startupinfo=si)
        return r.returncode == 0
    except Exception:
        return False


def _pick_python():
    """选一个能 import requests 的 python 解释器。

    防止 .pyw 被系统关联到错误的 Python（比如 Python311 没装 requests）
    导致 bot 永远起不来。优先当前解释器 -> Anaconda。
    """
    here = sys.executable
    if here.lower().endswith("pythonw.exe"):
        here = here[:-11] + "python.exe"   # 子进程要用有控制台的 python
    candidates = [here]
    for extra in (r"D:\anaconda\anaconda1\python.exe", r"D:\anaconda\python.exe"):
        if extra.lower() != here.lower():
            candidates.append(extra)
    for c in candidates:
        if _can_import_requests(c):
            return c
    return here  # 兜底：都不可用就退回当前解释器（错误会写进日志，便于排查）


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    if os.path.exists(LOG):
        try:
            os.replace(LOG, OLD)
        except OSError:
            pass

    python = _pick_python()
    if not _can_import_requests(python):
        log("[警告] 没找到装了 requests 的 python，子进程可能起不来: %s" % python)
    log("using python: %s" % python)

    while True:
        log("=== starting ilink_bot.py ===")
        with open(LOG, "a", encoding="utf-8") as out, \
                open(os.devnull, "rb") as nul:
            # 用隐藏窗口而非 CREATE_NO_WINDOW：后者在某些会话下会导致
            # 子进程 DLL 初始化失败（0xC0000142），bot 反复崩溃重启
            si = subprocess.STARTUPINFO()
            si.dwFlags = subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0  # SW_HIDE：窗口隐藏但进程正常
            p = subprocess.Popen([python, "-u", "ilink_bot.py"],
                                 cwd=BASE, stdin=nul, stdout=out, stderr=out,
                                 startupinfo=si)
        code = p.wait()
        log("bot exited (code=%s), restarting in 5s..." % code)
        time.sleep(5)


if __name__ == "__main__":
    main()
