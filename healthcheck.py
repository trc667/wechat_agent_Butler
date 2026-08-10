# -*- coding: utf-8 -*-
"""启动自检：bot 启动时打印配置/依赖/数据状态清单，缺啥一眼看到。

用法（在 ilink_bot.py 启动流程里调用）：
    from healthcheck import run_health_check
    run_health_check()
"""

import os

from config import load_config


def _file_exists(rel):
    return os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), rel))


def run_health_check(cfg=None):
    """打印自检清单。cfg 可注入（测试用），默认读真实配置。"""
    cfg = cfg if cfg is not None else load_config()
    rows = []

    def check(name, ok, note=""):
        rows.append((name, bool(ok), note))

    # ---- 密钥 ----
    check("DeepSeek API key", cfg.get("deepseek_api_key"),
          "对话大脑，缺了无法回复")
    check("识图模型（阿里云百炼）", cfg.get("dashscope_api_key"),
          "可选，缺了发图只回「没看清」")
    # ---- 依赖 ----
    try:
        from Crypto.Cipher import AES  # noqa: F401
        check("pycryptodome", True, "图片解密用")
    except ImportError:
        check("pycryptodome", False, "图片解密不可用，需 pip install pycryptodome")
    # ---- 数据文件 ----
    check("微信登录凭据", _file_exists("data/ilink_cred.json"),
          "缺了启动后会提示重新扫码")
    check("会话令牌（主动推送）", _file_exists("data/ilink_tokens.json"),
          "缺了首次聊天后自动生成")
    check("天气默认城市", cfg.get("weather_city"), "说「今天天气」时用")

    # ---- 输出 ----
    print("=" * 46)
    print("  启动自检")
    print("=" * 46)
    for name, ok, note in rows:
        mark = "OK " if ok else "!! "
        line = "  [%s] %s" % (mark, name)
        if not ok and note:
            line += "（%s）" % note
        print(line)
    bad = sum(1 for _, ok, _ in rows if not ok)
    print("  就绪 %d/%d%s" % (len(rows) - bad, len(rows),
                              "，有缺项见上（不影响聊天的可忽略）" if bad else ""))
    print("=" * 46)
    return rows  # 便于测试
