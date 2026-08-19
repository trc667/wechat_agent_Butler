# -*- coding: utf-8 -*-
"""主动提醒：待办到期 + 每日晨报，微信直推（iLink 主动发送）。

背景：实测确认 iLink 官方通道可以用最近收到的 context_token 主动发消息，
所以提醒可以直接推到用户微信（不需要企业微信、邮箱等外部通道）。

推送通道（二选一）：
1. 微信直推（推荐）：注入 push 函数，用 ILinkClient.send_text 遍历有
   context_token 的用户发送（由 ilink_bot.py 提供）
2. 企微兑底：配置 corpid/agentid/secret（.env 的 WECOM_SECRET）+
   admin_userid，走企微 App 推送（已弃用，保留兼容）

用法（由 bot.py 创建并 start()，内部 daemon 线程）：
    rm = ReminderManager(mgr, cfg, push=push_ilink)
    if rm.available():
        rm.start()
"""
import datetime
import os
import sys
import threading
import time

from textfilter import strip_emoji

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

# 固定公历节日表（MM-DD -> 名称；农历节日如春节/中秋未内置）
FIXED_HOLIDAYS = {
    "01-01": "元旦", "02-14": "情人节", "03-08": "妇女节",
    "04-01": "愚人节", "05-01": "劳动节", "06-01": "儿童节",
    "09-10": "教师节", "10-01": "国庆节", "11-11": "双十一",
    "12-24": "平安夜", "12-25": "圣诞节",
}

# 24 节气表（MM-DD -> (名称, 提醒语)；公历日期每年 ±1 天，内置常用值）
SOLAR_TERMS = {
    "01-05": ("小寒", "小寒到，注意保暖"),
    "01-20": ("大寒", "大寒是一年最冷的时候，注意添衣"),
    "02-04": ("立春", "立春了，万物复苏"),
    "02-19": ("雨水", "雨水节气，注意防潮"),
    "03-05": ("惊蛰", "惊蛰到，春雷始鸣"),
    "03-20": ("春分", "春分昼夜平分"),
    "04-05": ("清明", "清明节，踏青扫墓"),
    "04-20": ("谷雨", "谷雨时节，雨生百谷"),
    "05-05": ("立夏", "立夏了，夏天开始"),
    "05-21": ("小满", "小满节气，麦粒渐满"),
    "06-05": ("芒种", "芒种忙种，别误农时"),
    "06-21": ("夏至", "夏至到了，一年中最长的白天"),
    "07-07": ("小暑", "小暑到，注意防暑"),
    "07-22": ("大暑", "大暑是一年最热的时候，注意防暑降温"),
    "08-07": ("立秋", "立秋了，早晚开始转凉"),
    "08-23": ("处暑", "处暑出暑，暑气渐消"),
    "09-07": ("白露", "白露节气，早晚温差大，记得添衣"),
    "09-23": ("秋分", "秋分昼夜平分"),
    "10-08": ("寒露", "寒露到，天气转凉，注意添衣"),
    "10-23": ("霜降", "霜降节气，天气渐冷"),
    "11-07": ("立冬", "立冬了，冬天开始"),
    "11-22": ("小雪", "小雪节气，注意保暖"),
    "12-07": ("大雪", "大雪节气，注意保暖"),
    "12-21": ("冬至", "冬至到，记得吃饺子"),
}

# 贴心话（无 emoji、管家口吻）：周一/周五/周末有针对性提示，其余随机挑一条
_TIPS = [
    "今天想好先做什么了吗？",
    "记得按时吃饭。",
    "今天也按计划来，效率更高。",
    "有什么想让我帮你查的，随时说。",
    "深呼吸，慢慢来，事情一件件做。",
]


def _wecom_client(cfg):
    """按配置构造企微客户端；配置不全返回 None（主动发送只需 corpid/agentid/secret）。"""
    wc = cfg.get("wecom") or {}
    if not (wc.get("corpid") and wc.get("agentid") and wc.get("secret")):
        return None
    from wecom import WeComClient  # 延迟导入：未启用时避免拖入 pycryptodome
    return WeComClient(wc["corpid"], wc["agentid"], wc["secret"], wc.get("api_base"))


class ReminderManager:
    """待办到期提醒 + 每日晨报。push 可注入（测试/微信直推用），默认走企微客户端。"""

    def __init__(self, mgr, cfg, push=None, interval=30, weather_fn=None,
                 memory=None, news_fn=None, weather_alert_fn=None, music_fn=None):
        self.mgr = mgr                    # LifeManager：读待办/备忘录
        self.cfg = cfg
        self._push_fn = push              # callable(text) -> bool，微信直推用
        self._weather_fn = weather_fn     # callable() -> str/None，晨报附天气
        self._news_fn = news_fn           # callable() -> str/None，晨报附新闻
        self._weather_alert_fn = weather_alert_fn  # callable() -> str/None，雨/高温预警
        self._music_fn = music_fn         # callable() -> str/None，定时任务推每日单曲
        self.memory = memory              # Memory：读重要日子（可选）
        self._client = None               # 企微客户端（兜底）
        self.interval = int(interval)
        self.target = (cfg.get("admin_userid") or "").strip()
        self._lock = threading.Lock()
        self._digest_last_day = None
        self._digest_time = str((cfg.get("daily_greeting") or {}).get("time", "09:00"))
        self._clock_sent = set()   # (日期, HH:MM) 已发送过的定时提醒，防同一天重复
        self._season_last_day = None  # 节气推送防重复（每天最多一次）
        self._alert_last_day = None   # 天气预警防重复（每天最多一次）
        self._alert_time = str(cfg.get("weather_alert_time") or "07:30")
        self._summary_last_day = None  # 睡前总结防重复（每天最多一次）
        self._summary_time = str((cfg.get("daily_summary") or {}).get("time", "22:00"))
        self._cleanup_last_day = None  # 过期数据清理（每天最多一次）

    # ---------- 可用性 ----------

    def available(self):
        """有注入的推送函数（微信直推），或企微配置齐全 + 有收件人。"""
        if self._push_fn is not None:
            return True
        if not self.target:
            return False
        return _wecom_client(self.cfg) is not None

    def _send(self, text, image=None):
        """推一条消息（统一去 emoji/波浪号）；失败打日志不抛异常。
        image 为 bytes 时由 push 函数决定是否发图（微信直推支持，企微忽略）。"""
        try:
            text = strip_emoji(text)
            if self._push_fn is not None:
                return self._push_fn(text, image=image)
            if self._client is None:
                self._client = _wecom_client(self.cfg)
            if self._client is None:
                print("[提醒] 推送通道不可用，无法发送: %s" % text[:40])
                return False
            ok = self._client.send_text(self.target, text)
            if not ok:
                print("[提醒] 企微推送失败: %s" % text[:40])
            return ok
        except Exception as e:
            print("[提醒] 推送异常: %s" % e)
            return False

    # ---------- 待办到期提醒 ----------

    def check_due_todos(self, now=None):
        """到期的待办推一次提醒（reminded 防重复）。多条合并成一条防刷屏。返回提醒条数。"""
        now = now or datetime.datetime.now()
        today = now.strftime("%Y-%m-%d")
        with self._lock:
            due_now = []
            for t in self.mgr.data["todos"]:
                due = t.get("due") or ""
                if t.get("done") or t.get("reminded") or not due:
                    continue
                if due <= today:  # 到期或已过期，提醒一次
                    t["reminded"] = True
                    due_now.append(t)
            if not due_now:
                return 0
            self.mgr.save()
        # 合并发送：一条消息提醒所有到期待办
        if len(due_now) == 1:
            t = due_now[0]
            text = "小管家提醒：待办「%s」%s，记得处理哦" % (
                t["text"], "今天到期" if t.get("due") == today
                else "已于 %s 到期" % t["due"])
        else:
            parts = ["%s（今天）" % t["text"] if t.get("due") == today
                     else "%s（%s）" % (t["text"], t["due"]) for t in due_now]
            text = "小管家提醒：你有 %d 条待办到期了：%s。记得处理哦" % (
                len(due_now), "；".join(parts))
        if self._send(text):
            return len(due_now)
        return 0

    # ---------- 每日晨报 ----------

    def _friendly_tip(self, now):
        """按日期生成一句贴心话；没有针对性提示就随机挑一条。"""
        wd = now.weekday()  # 0=周一
        if wd == 4:          # 周五
            return "明天就是周末啦，今天把要紧事处理完。"
        if wd == 5:          # 周六
            return "周末愉快，好好放松一下。"
        if wd == 6:          # 周日
            return "明天又要上班啦，今天好好休息。"
        if wd == 0:          # 周一
            return "新的一周，加油！"
        import random
        return random.choice(_TIPS)

    def _day_note(self, now, delta=0):
        """今天/明天是什么日子（固定节日 + 节气 + 记忆里的重要日子）。无则返回空。"""
        day = now + datetime.timedelta(days=delta)
        mmdd = day.strftime("%m-%d")
        names = []
        name = FIXED_HOLIDAYS.get(mmdd)
        if name:
            names.append(name)
        term = SOLAR_TERMS.get(mmdd)
        if term:
            names.append(term[0])  # 节气名（如「立秋」）
        if self.memory is not None:
            for d in (self.memory.data.get("important_dates") or []):
                if d.get("date") == mmdd and d.get("event"):
                    names.append(d["event"])
        if not names:
            return ""
        return "%s是%s" % ("今天" if delta == 0 else "明天", "、".join(names))

    def build_digest(self, now=None):
        """组装晨报文本：问候 + 待办 + 备忘条数（清爽版，不塞备忘录原文，无 emoji）。"""
        now = now or datetime.datetime.now()
        lines = []
        greeting = (self.cfg.get("daily_greeting") or {}).get("text") or "早上好"
        lines.append("%s（%s %s）" % (greeting, now.strftime("%m月%d日"),
                                      WEEKDAYS[now.weekday()]))
        # 今日到期的待办
        today = now.strftime("%Y-%m-%d")
        due_today = [t for t in self.mgr.data["todos"]
                     if not t.get("done") and t.get("due") == today]
        active = [t for t in self.mgr.data["todos"] if not t.get("done")]
        if due_today:
            lines.append("今日待办：" + "、".join(t["text"] for t in due_today))
        elif active:
            lines.append("未完成待办：" + "、".join(
                "%s（%s）" % (t["text"], t["due"]) if t.get("due") else t["text"]
                for t in active))
        else:
            lines.append("今日没有待办，可以安心安排自己的事。")
        # 今天/明天是什么日子（固定节日 + 记忆里的重要日子）
        today_note = self._day_note(now, 0)
        if today_note:
            lines.append(today_note)
        tomorrow_note = self._day_note(now, 1)
        if tomorrow_note:
            lines.append(tomorrow_note)
        # 节日/节气倒计时（未来 90 天内的最近一个）
        countdown = self._next_festival(now)
        if countdown:
            lines.append("距离%s还有 %d 天" % (countdown[0], countdown[2]))
        # 备忘录只报条数，不塞原文（避免敏感凭据/超长内容每天推送到微信）
        n = len(self.mgr.data["memos"])
        if n:
            lines.append("备忘 %d 条，需要时问我要" % n)
        # 贴心话（周五/周末/周一有针对性，其他随机）
        tip = self._friendly_tip(now)
        if tip:
            lines.append(tip)
        return strip_emoji("\n".join(lines))

    # ---------- 定时提醒（如上下班打卡） ----------

    def maybe_send_clock_reminders(self, now=None):
        """到点推定时提醒（clock_reminders 配置），每个时间点每天只推一次。返回发送条数。"""
        now = now or datetime.datetime.now()
        conf = self.cfg.get("clock_reminders") or {}
        if not conf.get("enabled", True):
            return 0
        hhmm = now.strftime("%H:%M")
        sent = 0
        for item in conf.get("times") or []:
            t = str(item.get("time") or "")
            if t != hhmm:
                continue
            key = (now.date(), t)
            if key in self._clock_sent:
                continue
            text = item.get("text") or "到时间啦，记得打卡"
            # 无论成败都标记：token 过期等失败原因短时间内重试无意义，避免 30 秒刷屏
            self._clock_sent.add(key)
            if self._send(text):
                sent += 1
        return sent

    # ---------- 定时提醒（任意时间点，用户说「下午3点提醒我开会」） ----------

    def check_due_timers(self, now=None):
        """到点的定时提醒推一次（fired 防重复）。返回推送条数。"""
        now = now or datetime.datetime.now()
        timers = self.mgr.data.get("timers") or []
        due_now = []
        for t in timers:
            at = t.get("at") or ""
            if t.get("fired") or not at:
                continue
            try:
                at_dt = datetime.datetime.strptime(at, "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            if at_dt <= now:
                t["fired"] = True
                due_now.append(t)
        if not due_now:
            return 0
        self.mgr.save()
        for t in due_now:
            self._send("小管家提醒：%s 时间到" % t["text"])
        return len(due_now)

    # ---------- 节气/换季推送 ----------

    def maybe_send_season_note(self, now=None):
        """节气当天推一条节气提醒（每天最多一次，防重复）。返回是否发送。"""
        now = now or datetime.datetime.now()
        if self._season_last_day == now.date():
            return False
        term = SOLAR_TERMS.get(now.strftime("%m-%d"))
        if not term:
            return False
        self._season_last_day = now.date()  # 失败也标记，防刷屏
        return self._send("今日%s：%s" % (term[0], term[1]))

    # ---------- 天气预警（雨/雪/高温，早上推带伞/防暑） ----------

    def maybe_send_weather_alert(self, now=None):
        """到点（默认 07:30）查今天天气：有雨/雪/高温才推一条，每天最多一次。"""
        now = now or datetime.datetime.now()
        if now.strftime("%H:%M") != self._alert_time:
            return False
        if self._alert_last_day == now.date():
            return False
        if self._weather_alert_fn is None:
            return False
        try:
            alert = self._weather_alert_fn()
        except Exception:
            alert = None
        if not alert:
            return False
        self._alert_last_day = now.date()  # 失败也标记，防刷屏
        return self._send(alert)

    # ---------- 通用定时任务（每天/每周/一次，用户说「每天早上9点查天气」） ----------

    def check_due_tasks(self, now=None):
        """到点的定时任务执行一次（daily/weekly 按天防重，once 触发即失效）。
        返回执行条数。action=weather 推天气数据，否则推提醒文本。"""
        now = now or datetime.datetime.now()
        tasks = self.mgr.data.get("tasks") or []
        due_now = []
        today = now.strftime("%Y-%m-%d")
        hhmm = now.strftime("%H:%M")
        with self._lock:
            for tk in tasks:
                typ = tk.get("type")
                if typ == "once":
                    if tk.get("fired") or not tk.get("at"):
                        continue
                    if tk["at"] <= now.strftime("%Y-%m-%d %H:%M"):
                        tk["fired"] = True
                        due_now.append(tk)
                else:  # daily / weekly
                    if tk.get("last_fired") == today:
                        continue
                    if tk.get("time") != hhmm:
                        continue
                    if typ == "weekly" and tk.get("weekday") != now.weekday():
                        continue
                    tk["last_fired"] = today
                    due_now.append(tk)
            if not due_now:
                return 0
            self.mgr.save()
        for tk in due_now:
            action = tk.get("action")
            if action == "weather" and self._weather_fn is not None:
                try:
                    w = self._weather_fn()
                except Exception:
                    w = None
                if w:
                    self._send(w)
                    continue
            if action == "music" and self._music_fn is not None:
                try:
                    m = self._music_fn()
                except Exception:
                    m = None
                if m and m.get("text"):
                    # 歌名+封面图一条；二维码一条（长按识别打开播放）；URL 单独一条可复制
                    lines = [x.strip() for x in m["text"].split("\n") if x.strip()]
                    title = lines[0] if lines else m["text"]
                    self._send(title, image=m.get("image"))
                    if m.get("qr"):
                        self._send("长按识别二维码打开播放", image=m["qr"])
                    if len(lines) > 1:
                        self._send(lines[1])
                    continue
            text = tk.get("text") or "定时任务"
            self._send("小管家提醒：%s 时间到" % text)
        return len(due_now)

    # ---------- 节日倒计时（公历 + 节气 + 农历） ----------

    def _next_lunar_festival(self, now):
        """未来最近的一个农历节日（春节/元宵/端午/七夕/中秋/重阳/腊八），
        返回 (名称, 公历日期) 或 None。用 zhdate 库（纯 Python 无依赖）。"""
        fests = {"1-1": "春节", "1-15": "元宵节", "5-5": "端午节",
                 "7-7": "七夕", "8-15": "中秋节", "9-9": "重阳节",
                 "12-8": "腊八节"}
        try:
            from zhdate import ZhDate
        except ImportError:
            return None  # 没装 zhdate 就跳过农历倒计时
        for i in range(1, 370):
            d = now.date() + datetime.timedelta(days=i)
            try:
                lunar = ZhDate.from_datetime(
                    datetime.datetime(d.year, d.month, d.day))
            except Exception:
                continue
            if getattr(lunar, "leap_month", False):
                continue  # 闰月不算节日
            key = "%d-%d" % (lunar.lunar_month, lunar.lunar_day)
            if key in fests:
                return fests[key], d
        return None

    def _next_festival(self, now):
        """未来 90 天内最近的节日/节气（公历节日 + 24 节气 + 农历节日），
        返回 (名称, 公历日期, 天数差) 或 None。"""
        today = now.date()
        candidates = []
        # 公历节日 + 节气：今年和明年
        for mmdd, name in (list(FIXED_HOLIDAYS.items())
                           + [(k, v[0]) for k, v in SOLAR_TERMS.items()]):
            for year in (today.year, today.year + 1):
                d = datetime.date(year, int(mmdd[:2]), int(mmdd[3:]))
                delta = (d - today).days
                if 0 < delta <= 90:
                    candidates.append((delta, name, d))
        # 农历节日
        lf = self._next_lunar_festival(now)
        if lf:
            delta = (lf[1] - today).days
            if 0 < delta <= 90:
                candidates.append((delta, lf[0], lf[1]))
        if not candidates:
            return None
        delta, name, d = min(candidates, key=lambda x: x[0])
        return name, d, delta

    # ---------- 每日睡前总结（今天完成了什么/记了什么/花了多少） ----------

    def build_summary(self, now=None):
        """组装睡前总结文本：今日完成待办 + 新增备忘 + 支出。全部为空也给一句收尾。"""
        now = now or datetime.datetime.now()
        today = now.strftime("%Y-%m-%d")
        lines = []
        # 今日完成的待办（需要完成时间戳；旧数据没有就不统计）
        done_today = [t for t in self.mgr.data["todos"]
                      if t.get("done") and (t.get("done_ts") or "").startswith(today)]
        if done_today:
            lines.append("今天完成了 %d 件事：%s" % (
                len(done_today), "、".join(t["text"] for t in done_today)))
        # 今日新增的备忘（ts 是时间戳）
        memos_today = [m for m in self.mgr.data["memos"]
                       if str(m.get("ts") or "").isdigit()
                       and datetime.datetime.fromtimestamp(int(m["ts"])).strftime("%Y-%m-%d") == today]
        if memos_today:
            lines.append("记了 %d 条备忘，需要时问我要" % len(memos_today))
        # 今日支出（expenses.json）
        try:
            from tools import _load_expenses
            spent = [it for it in _load_expenses().get("items", [])
                     if (it.get("date") or "") == today]
            if spent:
                total = sum(float(it.get("amount", 0)) for it in spent)
                by_cat = {}
                for it in spent:
                    c = it.get("category") or "其他"
                    by_cat[c] = by_cat.get(c, 0) + float(it.get("amount", 0))
                parts = "、".join("%s %s" % (c, format(v, ".0f")) for c, v in by_cat.items())
                lines.append("今天花了 %s 元（%s）" % (format(total, ".0f"), parts))
        except Exception:
            pass
        if not lines:
            return "今天没有留下记录，早点休息，明天继续加油。"
        return strip_emoji("\n".join(lines))

    def maybe_send_summary(self, now=None):
        """到点（默认 22:00）推睡前总结，每天最多一次。返回是否发送。"""
        now = now or datetime.datetime.now()
        if now.strftime("%H:%M") != self._summary_time:
            return False
        if self._summary_last_day == now.date():
            return False
        text = self.build_summary(now)
        self._summary_last_day = now.date()  # 失败也标记，防刷屏
        return self._send(text)

    def maybe_cleanup(self, now=None):
        """每天清理一次过期数据（once 任务/已完成待办/已触发提醒）。"""
        now = now or datetime.datetime.now()
        if self._cleanup_last_day == now.date():
            return
        self._cleanup_last_day = now.date()
        try:
            self.mgr.cleanup_old_data(now)
        except Exception as e:
            print("[提醒] 数据清理异常: %s" % e)

    def maybe_send_digest(self, now=None):
        """每天到点发一次晨报（当天不重复，可附天气）。返回是否发送。"""
        now = now or datetime.datetime.now()
        if now.strftime("%H:%M") != self._digest_time:
            return False
        if self._digest_last_day == now.date():
            return False
        text = self.build_digest(now)
        if self._weather_fn is not None:
            try:
                w = self._weather_fn()
                if w:
                    text += "\n" + w
            except Exception:
                pass
        if self._news_fn is not None:
            try:
                n = self._news_fn()
                if n:
                    text += "\n\n" + n
            except Exception:
                pass
        if self._send(text):
            self._digest_last_day = now.date()
            return True
        self._digest_last_day = now.date()  # 失败也标记：token 过期重试无意义，防刷屏
        return False

    # ---------- 线程 ----------

    def _loop(self):
        while True:
            try:
                self.check_due_todos()
                self.check_due_timers()
                self.check_due_tasks()
                self.maybe_send_clock_reminders()
                self.maybe_send_season_note()
                self.maybe_send_weather_alert()
                self.maybe_send_digest()
                self.maybe_send_summary()
                self.maybe_cleanup()
            except Exception as e:
                print("[提醒] 循环异常: %s" % e)
            time.sleep(self.interval)

    def start(self):
        """启动提醒循环（daemon 线程，不阻塞主流程）。"""
        threading.Thread(target=self._loop, daemon=True).start()


if __name__ == "__main__":
    # 离线自测：注入假 push，验证到期提醒和晨报逻辑（不联网不花钱）
    import tempfile
    from manager import LifeManager

    class _FakePush:
        def __init__(self):
            self.sent = []

        def __call__(self, text, image=None):
            self.sent.append(text)
            return True

    tmp = tempfile.mkdtemp(prefix="xiaoqi_rm_")
    mgr = LifeManager(os.path.join(tmp, "manager.json"))
    cfg = {"admin_userid": "wang", "daily_greeting": {"time": "09:00", "text": "早安"}}
    push = _FakePush()
    rm = ReminderManager(mgr, cfg, push=push)

    # 到期提醒（手工造数据，绕开待办提取）
    mgr.data["todos"] = [
        {"text": "交房租", "due": "2000-01-01", "done": False, "reminded": False},
        {"text": "买牛奶", "due": "", "done": False, "reminded": False},
    ]
    assert rm.check_due_todos(datetime.datetime(2026, 8, 6)) == 1
    assert rm.check_due_todos(datetime.datetime(2026, 8, 6)) == 0  # 不重复
    assert any("交房租" in t for t in push.sent)

    # 晨报
    text = rm.build_digest(datetime.datetime(2026, 8, 6, 9, 0))
    assert "交房租" in text and "早安" in text
    assert rm.maybe_send_digest(datetime.datetime(2026, 8, 6, 9, 0)) is True
    assert rm.maybe_send_digest(datetime.datetime(2026, 8, 6, 9, 0)) is False  # 当天不重复
    print("自测通过 ✅（未联网；真实推送走微信直推，见模块注释）")
