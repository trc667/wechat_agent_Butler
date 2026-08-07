"""小管家 — 微信智能管家主程序（原「小淇」AI 女友，2026-08-02 转为智能管家）。

流程：收到消息 -> 备忘录/待办路由（manager.py）-> 组装人设+记忆+上下文
-> DeepSeek 生成回复 -> 回发。
另带：长期记忆提炼、回复频率限制。

用法：
    python bot.py --dry-run    # 不连微信，用模拟消息跑通全链路（测试用）
    python ilink_bot.py        # 微信版入口（ClawBot 官方通道）
    （run_wecom 企微模式已停用，代码保留备查）
"""

import argparse
import re
import sys
import threading
import time
from datetime import datetime

from textfilter import strip_emoji

# Windows 控制台默认 GBK，打印 emoji 会 UnicodeEncodeError 崩溃；统一 UTF-8 + 容错替换。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from config import load_config, api_key_valid
from deepseek import DeepSeek, DeepSeekError
from manager import LifeManager
from memory import Memory
from persona import build_system_prompt, build_context
from reminder import ReminderManager


class FakeDeepSeek:
    """无 API key 时干跑（--dry-run）用的假模型：回复固定话术，记忆提炼返回预设 JSON。"""

    def chat(self, messages, **kw):
        text = messages[-1]["content"]
        if "记忆助手" in text:
            return ('{"profile": {"职业": "程序员"},'
                    ' "new_facts": ["用户是程序员，常用 Claude Code 写代码"],'
                    ' "dates": []}')
        if "待办提取助手" in text:
            return '{"text": "交周报", "due": "2026-08-03"}'
        return "（模拟回复）好的，已收到。"

    def ping(self):
        pass


class XiaoQiBot:
    def __init__(self, deepseek, memory, cfg, send, reminder_push=None):
        self.ds = deepseek
        self.mem = memory
        self.cfg = cfg
        self._send = send          # send(sender_wxid, text)，真实环境是 wcf.send_text
        self._last_reply_ts = 0.0  # 上次发消息时间，用于频率限制
        self._user_msgs_since_extract = 0
        self._last_sender = None   # 最近私聊过的人（定时问候发给 ta）
        self._extract_lock = threading.Lock()
        self.mgr = LifeManager()          # 备忘录/待办（manager.py）
        # 主动提醒：待办到期 + 每日晨报。微信直推（reminder_push）优先，企微兑底。
        self.reminder = ReminderManager(self.mgr, cfg, push=reminder_push,
                                        weather_fn=self._weather_line)
        if self.reminder.available():
            if reminder_push is not None:
                print("[提醒] 微信主动提醒已启用：待办到期 + 每日晨报将直推微信")
            else:
                print("[提醒] 待办到期提醒 + 每日晨报已启用（企微推送，收件人: %s，晨报 %s）"
                      % (self.reminder.target, self.reminder._digest_time))
            self.reminder.start()
        else:
            print("[提醒] 未配置推送通道，主动提醒未启用（不影响微信聊天）")

    # ---------- 入口（主线程调用） ----------

    def _weather_line(self):
        """晨报附加天气行（查失败返回 None，晨报照常发）。"""
        try:
            from weather import fetch_weather
            return fetch_weather(self.cfg.get("weather_city") or "北京")
        except Exception:
            return None

    def on_text(self, sender, text):
        """收到一条用户私聊文本：记历史、排队回复。"""
        self._last_sender = sender
        self._user_msgs_since_extract += 1
        self.mem.append_history("user", text)
        threading.Thread(target=self._reply_worker, args=(sender, text), daemon=True).start()

    # ---------- 回复工作线程 ----------

    def _reply_worker(self, sender, text):
        self._rate_limit()
        handled, hint = self.mgr.handle(text, self.ds)
        if not handled:  # 不是管家命令，试试天气
            handled, hint = self._try_weather(text)
            if handled:  # 天气是确定性数据，直接发，不走模型（避免带出备忘录/多说话）
                self._send(sender, hint)
                self.mem.append_history("assistant", hint)
                return
        history = self.mem.recent_history(self.cfg.get("history_keep", 12))
        # 备忘录永远挂在 system prompt 里：用户问「那个XX是什么」时管家能直接查到
        base = build_system_prompt(self.mem) + self.mgr.memo_prompt()
        messages = [{"role": "system", "content": base}]
        if handled and hint:
            messages.append({"role": "system", "content": hint})
        messages += build_context(history, self.cfg.get("history_keep", 12))
        try:
            reply = self.ds.chat(messages)
            if not reply:
                raise DeepSeekError("模型返回空回复")
        except DeepSeekError as e:
            print("[错误] 回复失败: %s" % e)
            reply = "呜…我这边网络开小差了，宝贝再说一次好不好"
        reply = strip_emoji(reply)  # 用户要求：回复里一个 emoji 都不能有
        self._send(sender, reply)
        self.mem.append_history("assistant", reply)
        self._maybe_extract()

    def _try_weather(self, text):
        """天气路由：说「看看今天天气/深圳天气」→ 查 wttr.in → 返回纯文本回复。
        返回 (True, 回复文本) 表示命中并已拿到数据；否则 (False, None)。"""
        if not re.search(r"天气", text or ""):
            return False, None
        from weather import extract_city, fetch_weather
        city = extract_city(text, self.cfg.get("weather_city") or "北京")
        w = fetch_weather(city)
        if not w:
            return True, "天气服务暂时没查到 %s 的天气，稍后再试试" % city
        return True, w

    def _rate_limit(self):
        """防连发：两条回复之间至少隔 min_reply_interval 秒（防风控）。"""
        interval = float(self.cfg.get("min_reply_interval", 3))
        wait = self._last_reply_ts + interval - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last_reply_ts = time.time()

    def _maybe_extract(self):
        """每 N 轮用户消息做一次记忆提炼（低频、便宜的小调用）。"""
        every = int(self.cfg.get("memory_extract_every", 5))
        if self._user_msgs_since_extract < every:
            return
        if not self._extract_lock.acquire(blocking=False):
            return
        try:
            self._user_msgs_since_extract = 0
            slice_ = self.mem.recent_history(self.cfg.get("history_keep", 12) * 2)
            if self.mem.extract_and_merge(self.ds, slice_):
                print("[记忆] 记住了新信息，下次用得上")
        except DeepSeekError as e:
            print("[错误] 记忆提炼失败: %s" % e)
        finally:
            self._extract_lock.release()

    # ---------- 定时问候 ----------

    def greeting_loop(self):
        """每天到点给最近聊过的人发一句早安（需要先有人聊过，才知道发给谁）。"""
        g = self.cfg.get("daily_greeting") or {}
        if not g.get("enabled") or not g.get("text"):
            return
        target_time = str(g.get("time", "09:00"))
        last_sent_day = None
        while True:
            time.sleep(20)
            # 优先发给最近聊过的人；没聊过但有配置 admin_userid 就发给他
            target = self._last_sender or self.cfg.get("admin_userid")
            if not target:
                continue
            now = datetime.now()
            if now.strftime("%H:%M") == target_time and last_sent_day != now.date():
                self._send(target, str(g["text"]))
                last_sent_day = now.date()


# ---------- 企业微信模式 ----------

def run_wecom(deepseek, mem, cfg):
    from wecom import WeComClient, WeComCrypto, WeComError, start_callback_server
    from wecom_crypto import WeComCryptoError

    wc = cfg.get("wecom") or {}
    missing = [k for k in ("corpid", "agentid", "secret", "token", "encoding_aes_key")
               if not wc.get(k)]
    if missing:
        print("[错误] config.json 的 wecom 配置不完整，缺少: %s" % ", ".join(missing))
        print("请按 README 完成企业微信配置（注册 -> 创建自建应用 -> 填密钥）。")
        return
    mode = wc.get("mode", "safe")
    port = int(wc.get("callback_port", 9000))
    path = wc.get("callback_path", "/wechat/callback")

    client = WeComClient(wc["corpid"], wc["agentid"], wc["secret"], wc.get("api_base"))
    try:
        crypto = WeComCrypto(wc["token"], wc["encoding_aes_key"], wc["corpid"])
    except WeComCryptoError as e:
        print("[错误] EncodingAESKey 配置有误: %s" % e)
        return

    bot = XiaoQiBot(deepseek, mem, cfg, send=client.send_text)
    bundle = {"crypto": crypto, "bot": bot, "callback_path": path, "mode": mode}
    httpd = start_callback_server(bundle, port, path)

    print("=" * 46)
    print("  小淇已上线 💗（企业微信）")
    print("  企业:      %s" % wc["corpid"])
    print("  应用 ID:   %s" % wc["agentid"])
    print("  回调模式:  %s" % ("安全模式" if mode == "safe" else "明文模式"))
    print("  本地回调:  http://127.0.0.1:%d%s" % (port, path))
    print("  （管理后台填的是公网隧道地址，见 README）")
    print("  模型:      %s" % cfg.get("model"))
    print("  按 Ctrl+C 退出")
    print("=" * 46)

    if not api_key_valid():
        print("[警告] config.json 里的 deepseek_api_key 还是占位符，小淇会说不出话！")
    else:
        def ping_async():
            try:
                deepseek.ping()
                print("[自检] DeepSeek 连接正常 ✅")
            except DeepSeekError as e:
                print("[警告] DeepSeek 自检失败: %s" % e)
        threading.Thread(target=ping_async, daemon=True).start()

    threading.Thread(target=bot.greeting_loop, daemon=True).start()
    try:
        while True:
            time.sleep(3600)  # 消息全部走回调线程，主线程只需保活
    except KeyboardInterrupt:
        httpd.shutdown()
        raise


# ---------- 干跑模式 ----------

def run_dry_run(deepseek, mem, cfg):
    import tempfile
    if not api_key_valid():
        print("[干跑] 未配置 API key，使用假模型模拟回复（只测流程，不花钱）")
        deepseek = FakeDeepSeek()
    tmp_dir = tempfile.mkdtemp(prefix="xiaoqi_dryrun_")
    mem = Memory(deepseek, data_dir=tmp_dir)  # 干跑用临时目录，不污染真实记忆
    cfg["memory_extract_every"] = 1  # 干跑时每条消息都触发一次记忆提炼，便于验证

    bot = XiaoQiBot(deepseek, mem, cfg, send=lambda sender, text: print("  [回复→%s] %s" % (sender, text)))
    print("=" * 46)
    print("  管家干跑模式：不连微信，模拟 3 条消息")
    print("=" * 46)
    for text in ["记住线上测试环境地址 http://10.10.0.8:8080",
                 "明天交周报，记一下",
                 "那个测试环境地址是什么？"]:
        print("[模拟收到] %s" % text)
        bot.on_text("wxid_demo", text)
        time.sleep(1)

    time.sleep(2)  # 等 worker 线程收尾
    print("-" * 46)
    print("记忆内容（提炼结果）：")
    print(mem.text() or "（暂无记忆）")
    print("聊天记录条数：%d" % len(mem.recent_history(100)))
    print("干跑结束 ✅（未连接企业微信，未写入真实记忆库）")


def main():
    parser = argparse.ArgumentParser(description="小管家 - 微信 AI 智能管家")
    parser.add_argument("--dry-run", action="store_true", help="不连微信，模拟消息跑通全链路")
    args = parser.parse_args()

    cfg = load_config()
    deepseek = DeepSeek()
    mem = Memory(deepseek)

    if args.dry_run:
        run_dry_run(deepseek, mem, cfg)
    else:
        run_wecom(deepseek, mem, cfg)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n管家已下线")
    except Exception as e:  # 启动级错误给出友好提示（如配置不完整）
        print("[错误] %s" % e)
        print("提示：请按 README 完成企业微信配置（corpid/agentid/secret/token/EncodingAESKey）和 DeepSeek key")
