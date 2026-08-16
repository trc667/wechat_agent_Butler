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


def _hidden_startupinfo():
    """隐藏窗口的 STARTUPINFO（子进程启动参数统一用，避免重复代码）。"""
    si = subprocess.STARTUPINFO()
    si.dwFlags = subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE：窗口隐藏但进程正常
    return si


def _kill_stale_bots():
    """杀掉残留的旧 bot 进程（命令行含 ilink_bot.py），防止多实例互踩。

    场景：上次 bot 异常退出但子进程还活着（孤儿），新守护起来后两个 bot
    同时长轮询会互相抢消息、日志句柄冲突（导致 os.replace 失败）。
    """
    si = _hidden_startupinfo()
    try:
        r = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "processid,commandline", "/format:csv"],
            capture_output=True, timeout=15, startupinfo=si)
        text = r.stdout.decode("utf-8", errors="ignore")
        for line in text.splitlines():
            if "ilink_bot.py" not in line or "run_bot_hidden" in line:
                continue
            pid = None
            for part in line.split(","):
                part = part.strip().strip('"')
                if part.isdigit():
                    pid = int(part)
            if pid and pid != os.getpid():
                try:
                    subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                                   capture_output=True, timeout=10, startupinfo=si)
                    log("killed stale bot pid=%s" % pid)
                except Exception:
                    pass
    except Exception:
        pass  # 杀僵尸失败不阻塞启动


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    _kill_stale_bots()  # 先清理残留旧 bot，释放日志句柄，再替换日志
    if os.path.exists(LOG):
        try:
            os.replace(LOG, OLD)
        except OSError as e:
            # 日志仍被占用（极少数情况）就继续用原文件追加，不阻塞启动
            log("[警告] 备份日志失败(将继续追加): %s" % e)

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
            p = subprocess.Popen([python, "-u", "ilink_bot.py"],
                                 cwd=BASE, stdin=nul, stdout=out, stderr=out,
                                 startupinfo=_hidden_startupinfo())
        code = p.wait()
        log("bot exited (code=%s), restarting in 5s..." % code)
        time.sleep(5)


if __name__ == "__main__":
    main()
