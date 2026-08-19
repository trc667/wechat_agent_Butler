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
import json
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
    def __init__(self, deepseek, memory, cfg, send, reminder_push=None, send_image=None):
        self.ds = deepseek
        self.mem = memory
        self.cfg = cfg
        self._send = send          # send(sender_wxid, text)，真实环境是 wcf.send_text
        self._send_image = send_image  # send_image(user, bytes, caption)（可选，立即推单曲封面用）
        self._last_reply_ts = 0.0  # 上次发消息时间，用于频率限制
        self._user_msgs_since_extract = 0
        self._last_sender = None   # 最近私聊过的人（定时问候发给 ta）
        self._extract_lock = threading.Lock()
        self.mgr = LifeManager()          # 备忘录/待办（manager.py）
        self._pending_image = None        # 图片识别待确认：{kind, items, sender, ts}（防止误判自动入库）
        # Function Calling 工具上下文（tools.py：模型可自主调用备忘/待办/天气等）
        self._tool_ctx = None
        # 主动提醒：待办到期 + 每日晨报。微信直推（reminder_push）优先，企微兑底。
        self.reminder = ReminderManager(self.mgr, cfg, push=reminder_push,
                                        weather_fn=self._weather_line,
                                        memory=self.mem, news_fn=self._news_line,
                                        weather_alert_fn=self._weather_alert_line,
                                        music_fn=self._music_line)
        if self.reminder.available():
            if reminder_push is not None:
                print("[提醒] 微信主动提醒已启用：待办到期 + 每日晨报 + 定时打卡将直推微信")
            else:
                print("[提醒] 待办到期提醒 + 每日晨报已启用（企微推送，收件人: %s，晨报 %s）"
                      % (self.reminder.target, self.reminder._digest_time))
            self.reminder.start()
        else:
            print("[提醒] 未配置推送通道，主动提醒未启用（不影响微信聊天）")

    # ---------- 入口（主线程调用） ----------

    def _capabilities_text(self):
        """小管家能力自述：快捷命令（确定性秒回）+ 工具清单（动态从 TOOLS 生成）。"""
        from tools import TOOLS
        lines = ["我是小管家，能帮你做的事："]
        lines.append("\n【快捷命令】（说这些最快，秒回）")
        lines.append("记住XXX / 备忘录 / 忘掉XXX —— 备忘增查删（说错内容重说一遍即自动更新）")
        lines.append("记一下XXX（可说日期）/ 我有哪些待办 / 完成了XXX —— 待办")
        lines.append("X点提醒我XXX / 我有哪些提醒 / 取消X点的提醒 —— 定时提醒（到点微信推）")
        lines.append("每天早上X点查天气 / 每周五X点提醒我写周报 —— 重复定时任务（可查可取消）")
        lines.append("今天天气 / 明天天气 / 这周天气 —— 天气查询（默认深圳）")
        lines.append("看新闻 / 周五的新闻 —— 科技/AI 新闻（每日自动存档可回看）")
        lines.append("记一笔XXX花多少 / 这个月花了多少 —— 记账汇总")
        lines.append("\n【对话中我还能自动帮你】（说人话就行，我会自己调工具）")
        for t in TOOLS:
            fn = t["function"]
            lines.append("· %s —— %s" % (fn["name"], fn["description"]))
        lines.append("\n试试对我说：「帮我记住测试环境地址，顺便记个待办明天交周报」")
        return "\n".join(lines)

    def _try_capabilities(self, sender, text):
        """「你能做什么/你有什么功能/帮助」→ 输出能力清单。命中返回 True。"""
        if not re.search(r"(你能做什么|你有什么功能|你会什么|你能干嘛|有什么功能|"
                         r"介绍一下自己|帮助|help|会啥|能做啥)", (text or "").lower()):
            return False
        reply = self._capabilities_text()
        self._send(sender, reply)
        self.mem.append_history("assistant", reply)
        return True

    def _weather_line(self):
        """晨报附加天气行：今日 + 明日（查失败返回 None，晨报照常发）。"""
        try:
            from weather import fetch_weather, fetch_weather_day
            city = self.cfg.get("weather_city") or "北京"
            lines = []
            w = fetch_weather(city)
            if w:
                lines.append(w)
            t = fetch_weather_day(city, index=1, label="明日")
            if t:
                lines.append(t)
            return "\n".join(lines) if lines else None
        except Exception:
            return None

    def _try_music_now(self, sender, text):
        """立即推一首每日单曲：说「现在推一首单曲/来首歌」→ 抓网易云热歌榜直接发。
        有专辑封面且支持发图时先发封面图再发链接文本（观感接近卡片）。
        返回 (True, 回复文本) 表示命中；否则 (False, None)。"""
        # 意图词开头（推/来/放/推荐/点）+ 目标词（单曲/首歌），避免误伤「这首歌很好听」
        if not re.search(r"(推|来|放|推荐|点)[^，。！？]{0,4}(每日单曲|单曲|首歌)",
                         text or ""):
            return False, None
        from music import fetch_daily_song_full
        m = fetch_daily_song_full()
        if not m or not m.get("text"):
            return True, "网易云暂时没连上，稍后再试试"
        lines = [x.strip() for x in m["text"].split("\n") if x.strip()]
        title = lines[0] if lines else m["text"]
        url = lines[1] if len(lines) > 1 else ""
        # 有封面且支持发图：发「歌名 + 封面图」，URL 单独一条（微信对纯链接消息更容易识别为可点击）
        if m.get("image") and self._send_image is not None:
            try:
                self._send_image(sender, m["image"], caption=title)
                if url:
                    self._send(sender, url)
                return True, ""  # 已自行发送，hint 置空避免重复
            except Exception:
                pass  # 发图失败退回纯文本
        return True, m["text"]

    def _news_line(self):
        """晨报附加科技/AI 新闻（抓失败返回 None，晨报照常发），并自动存档可回看。"""
        try:
            from news import fetch_news, save_history
            n = fetch_news(max_items=5)
            if n:
                save_history(datetime.now().strftime("%Y-%m-%d"), n)
            return n
        except Exception:
            return None

    def _try_news_query(self, text):
        """新闻回看路由：说「看看周五的新闻/昨天新闻/今天新闻」→ 返回存档或现抓。"""
        if not re.search(r"新闻", text or ""):
            return False, None
        from news import load_history, fetch_news
        hist = load_history()
        # 「周X/星期X的新闻」→ 查最近一个该周几的存档
        m = re.search(r"(周[一二三四五六日天]|星期[一二三四五六日天])", text)
        if m:
            names = {"周一": 0, "周二": 1, "周三": 2, "周四": 3,
                     "周五": 4, "周六": 5, "周日": 6}
            idx = names.get(m.group(1))
            if idx is not None:
                today = datetime.now().date()
                delta = (today.weekday() - idx) % 7
                key = (today - datetime.timedelta(days=delta)).strftime("%Y-%m-%d")
                if key in hist:
                    return True, hist[key]
                return True, ("%s 的新闻还没有存档（存档从上线后开始），"
                              "今天的最新新闻是：\n%s" % (m.group(1), fetch_news(5) or "暂无"))
        # 「昨天/前天」
        m = re.search(r"(昨天|前天)", text)
        if m:
            days = 1 if m.group(1) == "昨天" else 2
            key = (datetime.now().date() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
            if key in hist:
                return True, hist[key]
            return True, "%s 的新闻还没有存档" % m.group(1)
        # 默认：今天现抓最新新闻
        n = fetch_news(5)
        if n:
            return True, n
        return False, None

    def _music_line(self):
        """定时任务推每日单曲（网易云热歌榜 + 专辑封面）。失败返回 None 退化为提醒文本。"""
        try:
            from music import fetch_daily_song_full
            return fetch_daily_song_full()
        except Exception:
            return None

    def _weather_alert_line(self):
        """天气预警：今天有雨/雪/高温时返回提醒语，否则 None（供 07:30 定时推）。"""
        try:
            from weather import fetch_weather_alert
            return fetch_weather_alert(self.cfg.get("weather_city") or "北京")
        except Exception:
            return None

    def on_text(self, sender, text):
        """收到一条用户私聊文本：记历史、排队回复。"""
        self._last_sender = sender
        self._user_msgs_since_extract += 1
        self.mem.append_history("user", text)
        threading.Thread(target=self._reply_worker, args=(sender, text), daemon=True).start()

    def on_image(self, sender, image_bytes):
        """收到一张图片：下载解密后交给 DeepSeek 识图回复。"""
        self._last_sender = sender
        self.mem.append_history("user", "[图片]")
        threading.Thread(target=self._image_worker, args=(sender, image_bytes),
                         daemon=True).start()

    def _image_worker(self, sender, image_bytes):
        """识图：优先尝试把图片里的待办/备忘清单自动入库，否则普通描述回复。"""
        self._rate_limit()
        if self._try_extract_todo_from_image(sender, image_bytes):
            return  # 已识别为清单并入库
        self._describe_image(sender, image_bytes)

    def _handle_image_confirm(self, sender, text):
        """处理图片识别后的确认/取消。命中返回 True（已回复）。"""
        p = self._pending_image
        if not p or p.get("sender") != sender:
            return False
        if time.time() - p.get("ts", 0) > 600:  # 10 分钟过期，防旧图残留
            self._pending_image = None
            return False
        t = (text or "").strip()
        if re.match(r"^(记下|存|要|好|嗯|确认|是的|对|存下来)", t):
            self._commit_image_pending(sender, p)
            return True
        if re.match(r"^(不用|不要|忽略|算了|不记|取消|删|没)", t):
            self._pending_image = None
            reply = "好的，不记了。"
            self._send(sender, reply)
            self.mem.append_history("assistant", reply)
            return True
        return False

    def _commit_image_pending(self, sender, p):
        """用户确认后，把图片识别出的清单入库。"""
        kind = p.get("kind")
        items = p.get("items") or []
        self._pending_image = None
        if kind == "todo":
            added = []
            for i in items:
                text = str(i.get("text") or "").strip()
                due = str(i.get("due") or "").strip()
                if due and not re.match(r"^\d{4}-\d{2}-\d{2}$", due):
                    due = ""
                self.mgr.data["todos"].append(
                    {"text": text, "due": due, "done": False, "reminded": False})
                added.append("%s（%s前）" % (text, due) if due else text)
            self.mgr.save()
            print("[管家] 图片待办已确认：%s" % "、".join(added))
            reply = "已记下 %d 条待办：%s。完成时说「完成了xx」即可。" % (
                len(added), "；".join(added))
        else:
            added = []
            for i in items:
                text = str(i.get("text") or "").strip()
                if any(m["text"] == text for m in self.mgr.data["memos"]):
                    continue  # 去重
                self.mgr.data["memos"].append({"text": text, "ts": int(time.time())})
                added.append(text)
            self.mgr.save()
            print("[管家] 图片备忘已确认：%s" % "、".join(added))
            reply = ("已记下 %d 条备忘：%s。随时问「那个xx是什么」就能查到。" % (
                len(added), "；".join(added)) if added else "图里的备忘之前都记过了，没有新增。")
        reply = strip_emoji(reply)
        self._send(sender, reply)
        self.mem.append_history("assistant", reply)

    def _try_extract_todo_from_image(self, sender, image_bytes):
        """识别图片里的待办/备忘清单，先确认再入库（防止误判自动存）。命中返回 True。"""
        if not self.cfg.get("dashscope_api_key"):
            return False
        from vision import describe_image
        prompt = (
            "分析这张图片：只有图片主体是待办清单、任务清单或备忘清单（手写/打印/屏幕截图），"
            "才提取每一项；如果是食物/风景/人物/宠物/实物照片等普通图片，一律返回 type=none。"
            "只输出一个 JSON："
            '{"type": "todo" 或 "memo" 或 "none", '
            '"items": [{"text": "事项内容", "due": "YYYY-MM-DD 或空"}]}')
        raw = describe_image(image_bytes, prompt=prompt)
        data = self._parse_json(raw)
        if not data or not isinstance(data, dict):
            return False
        kind = str(data.get("type") or "none").strip().lower()
        items = data.get("items") if isinstance(data.get("items"), list) else []
        items = [i for i in items if isinstance(i, dict) and str(i.get("text") or "").strip()]
        if kind not in ("todo", "memo") or not items:
            return False
        # 先存为待确认，不直接入库
        self._pending_image = {"kind": kind, "items": items,
                               "sender": sender, "ts": time.time()}
        preview = "；".join(str(i.get("text")).strip() for i in items[:5])
        if len(items) > 5:
            preview += " 等%d条" % len(items)
        print("[管家] 图片识别%s待确认：%s" % (kind, preview))
        reply = ("从图里认出 %d 条%s：%s。回复「记下」我就帮你存，不是要记的内容就忽略。"
                 % (len(items), "待办" if kind == "todo" else "备忘", preview))
        reply = strip_emoji(reply)
        self._send(sender, reply)
        self.mem.append_history("assistant", reply)
        return True

    @staticmethod
    def _parse_json(raw):
        """稳健解析模型输出的 JSON（容忍围栏和多余文字）。"""
        text = (raw or "").strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None

    def _describe_image(self, sender, image_bytes):
        """普通识图描述：结合对话历史/记忆/备忘录，用管家口吻回复。"""
        from vision import describe_image
        # 组装上下文：最近对话 + 长期记忆 + 备忘录（让识图回复“像管家”、能结合已有信息）
        ctx = []
        try:
            hist = self.mem.recent_history(6)
            if hist:
                lines = ["%s：%s" % ("用户" if h["role"] == "user" else "管家",
                                     str(h["content"])[:80]) for h in hist]
                ctx.append("最近对话：\n" + "\n".join(lines))
            mem_text = self.mem.text()
            if mem_text:
                ctx.append("你记住的关于用户的事：\n" + mem_text[:300])
            if self.mgr.data.get("memos"):
                ctx.append("用户备忘录有 %d 条（需要时再提，不要主动全列）"
                           % len(self.mgr.data["memos"]))
        except Exception:
            pass
        prompt = (
            "用户在微信发来一张图片，请像他的私人管家一样用中文简短描述图片内容"
            "（两三句以内），不要 emoji，不要说教。如果图片像是要你记住的信息"
            "（地址/账号/配置/待办清单等），结尾自然地问一句要不要记住。\n\n"
            + "\n\n".join(ctx))
        reply = None
        # 1) 百炼视觉模型（配置了才可用）
        if self.cfg.get("dashscope_api_key"):
            try:
                reply = describe_image(image_bytes, prompt=prompt)
            except Exception as e:
                print("[错误] 识图失败(百炼): %s" % e)
        # 2) 回退 DeepSeek 多模态（可能不支持，失败给兑底文案）
        if not reply:
            import base64
            from media import guess_image_mime
            try:
                mime = guess_image_mime(image_bytes)
                b64 = base64.b64encode(image_bytes).decode("ascii")
                msgs = [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": "data:%s;base64,%s" % (mime, b64)}},
                ]}]
                reply = self.ds.chat(msgs)
                if not reply:
                    raise DeepSeekError("模型返回空回复")
            except DeepSeekError as e:
                print("[错误] 识图失败: %s" % e)
            except Exception as e:
                print("[错误] 识图异常: %s" % e)
        if not reply:
            reply = "图我收到了，但暂时没看清，稍后再试试"
        reply = strip_emoji(reply)
        self._send(sender, reply)
        self.mem.append_history("assistant", reply)

    # ---------- 回复工作线程 ----------

    def _reply_worker(self, sender, text):
        self._rate_limit()
        # 图片识别确认（识别到清单后先问，用户回「记下」才入库）
        if self._handle_image_confirm(sender, text):
            return
        # 能力自述（你能做什么/帮助）
        if self._try_capabilities(sender, text):
            return
        handled, hint = self.mgr.handle(text, self.ds)
        if not handled:  # 不是管家命令，试试立即推一首单曲
            handled, hint = self._try_music_now(sender, text)
            if handled:
                if hint:  # 已自行发送（封面+URL）时 hint 为空，不再重复发
                    self._send(sender, hint)
                    self.mem.append_history("assistant", hint)
                return
        if not handled:  # 不是管家命令，试试新闻回看
            handled, hint = self._try_news_query(text)
            if handled:
                self._send(sender, hint)
                self.mem.append_history("assistant", hint)
                return
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
            if handled:
                # 快路径已处理（如备忘/待办），直接生成回复口吻
                reply = self.ds.chat(messages)
            else:
                # Function Calling：模型可自主调用工具（备忘/待办/天气/新闻/记账等）
                from tools import TOOLS_SCHEMA, dispatch, build_ctx
                if self._tool_ctx is None:
                    self._tool_ctx = build_ctx(self.mgr, self.cfg)
                reply = self.ds.run_tool_loop(
                    messages, TOOLS_SCHEMA,
                    lambda n, a: dispatch(n, a, self._tool_ctx))
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
        """天气路由：说「看看今天天气/深圳天气/明天天气/这周天气」→ 查 wttr.in。
        返回 (True, 回复文本) 表示命中并已拿到数据；否则 (False, None)。"""
        if not re.search(r"天气", text or ""):
            return False, None
        from weather import (extract_city, fetch_weather, fetch_weather_day,
                             fetch_weather_week)
        city = extract_city(text, self.cfg.get("weather_city") or "北京")
        # 一周/未来几天
        if re.search(r"(这周|下周|一周|未来.{0,3}天|最近几天)", text):
            w = fetch_weather_week(city)
            if w:
                return True, w
            return True, "天气服务暂时没查到 %s 未来几天的天气，稍后再试试" % city
        # 明天/后天
        m = re.search(r"(后天)", text)
        if m:
            w = fetch_weather_day(city, index=2, label="后天")
            if w:
                return True, w
            return True, "天气服务暂时没查到 %s 后天的天气，稍后再试试" % city
        if re.search(r"(明天|明日)", text):
            w = fetch_weather_day(city, index=1, label="明日")
            if w:
                return True, w
            return True, "天气服务暂时没查到 %s 明天的天气，稍后再试试" % city
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
