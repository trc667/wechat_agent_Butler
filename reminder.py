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
                 memory=None, news_fn=None, weather_alert_fn=None):
        self.mgr = mgr                    # LifeManager：读待办/备忘录
        self.cfg = cfg
        self._push_fn = push              # callable(text) -> bool，微信直推用
        self._weather_fn = weather_fn     # callable() -> str/None，晨报附天气
        self._news_fn = news_fn           # callable() -> str/None，晨报附新闻
        self._weather_alert_fn = weather_alert_fn  # callable() -> str/None，雨/高温预警
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

    # ---------- 可用性 ----------

    def available(self):
        """有注入的推送函数（微信直推），或企微配置齐全 + 有收件人。"""
        if self._push_fn is not None:
            return True
        if not self.target:
            return False
        return _wecom_client(self.cfg) is not None

    def _send(self, text):
        """推一条消息（统一去 emoji/波浪号）；失败打日志不抛异常。"""
        try:
            text = strip_emoji(text)
            if self._push_fn is not None:
                return self._push_fn(text)
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
            if self._send(text):
                self._clock_sent.add(key)
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
        if self._send("今日%s：%s" % (term[0], term[1])):
            self._season_last_day = now.date()
            return True
        return False

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
        if self._send(alert):
            self._alert_last_day = now.date()
            return True
        return False

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
        return False

    # ---------- 线程 ----------

    def _loop(self):
        while True:
            try:
                self.check_due_todos()
                self.check_due_timers()
                self.maybe_send_clock_reminders()
                self.maybe_send_season_note()
                self.maybe_send_weather_alert()
                self.maybe_send_digest()
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

        def __call__(self, text):
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
