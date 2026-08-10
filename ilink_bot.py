# -*- coding: utf-8 -*-
"""小管家 — 微信官方 ClawBot（iLink）模式：个人微信里的私人 AI 管家。

用法：
    python ilink_bot.py     # 首次运行会打印登录二维码，用手机微信扫码授权

之后保持窗口开着即可：有人发消息 -> 管家自动回复（人设/记忆/备忘录/DeepSeek 全复用）。

说明：官方通道只能回复、不能主动发消息，所以定时任务在这个模式不生效。
"""

import os
import sys
import time

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# 清除系统代理环境变量：微信/DeepSeek/天气都走直连，避免被本地代理软件
# （如魔戒/Clash）劫持导致 ProxyError。代理只给 git/浏览器等外部工具用。
for _var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
             "ALL_PROXY", "all_proxy"):
    os.environ.pop(_var, None)

from config import load_config, api_key_valid
from deepseek import DeepSeek
from healthcheck import run_health_check
from memory import Memory
from bot import XiaoQiBot
from ilink import ILinkClient


def main():
    cfg = load_config()
    run_health_check(cfg)  # 启动自检：密钥/依赖/数据状态一览
    deepseek = DeepSeek()
    mem = Memory(deepseek)
    client = ILinkClient()

    if not client.load_cred():
        client.login()
    else:
        print("[*] 已有登录凭据，直接连接（失效了会自动重新扫码）")

    last_recv_ts = [0.0]  # 收到上一条消息的时间，用来算小淇的思考耗时

    def send_and_log(sender, text):
        """小管家的回复先在控制台打印出来（微信聊天实时镜像），再发出去。"""
        cost = time.time() - last_recv_ts[0]
        print("[%s] 小管家：%s（想了 %.0f 秒）" % (time.strftime("%H:%M:%S"), text, cost))
        client.send_text(sender, text)

    def push_ilink(text):
        """主动提醒：遍历所有聊过的用户（有 context_token 的）直推微信。
        实测确认 iLink 支持用最近 context_token 主动发消息。"""
        ok = False
        for user in list(client._context_tokens.keys()):
            if client.send_text(user, text):
                ok = True
        return ok

    bot = XiaoQiBot(deepseek, mem, cfg, send=send_and_log, reminder_push=push_ilink)

    print("=" * 46)
    print("  小管家已上线（微信 ClawBot 官方通道）")
    print("  Bot ID: %s" % client.bot_id)
    print("  模型:   %s" % cfg.get("model"))
    print("  现在去微信里给「小管家」发消息吧！")
    print("  （支持主动推送：待办到期/每日晨报直推微信）")
    print("  按 Ctrl+C 退出")
    print("=" * 46)

    if not api_key_valid():
        print("[警告] config.json 里的 deepseek_api_key 还是占位符！小淇会说不出话")

    def on_message(sender, text):
        last_recv_ts[0] = time.time()
        print("")
        print("=" * 44)
        print("[%s] 你：%s" % (time.strftime("%H:%M:%S"), text))
        print("[%s] 小管家：正在思考…" % time.strftime("%H:%M:%S"))
        bot.on_text(sender, text)

    try:
        client.update_loop(on_message, on_image=bot.on_image)
    except KeyboardInterrupt:
        print("\n管家已下线")


if __name__ == "__main__":
    main()
