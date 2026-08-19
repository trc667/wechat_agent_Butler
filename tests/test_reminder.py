# -*- coding: utf-8 -*-
"""reminder.py 单元测试：可用性判定、到期提醒防重复、晨报组装、推送注入。"""
import datetime
import os

from reminder import ReminderManager
from manager import LifeManager


class FakePush:
    def __init__(self):
        self.sent = []
        self.images = []

    def __call__(self, text, image=None):
        self.sent.append(text)
        if image:
            self.images.append(image)
        return True


def _make_rm(tmp_path, admin="wang", push=None, due=[]):
    mgr = LifeManager(os.path.join(str(tmp_path), "manager.json"))
    mgr.data["todos"] = [dict(t) for t in due]
    cfg = {"admin_userid": admin,
           "daily_greeting": {"time": "09:00", "text": "早安"}}
    rm = ReminderManager(mgr, cfg, push=push)
    return rm, mgr


# ---------- 可用性 ----------

def test_available_with_push(tmp_path):
    rm, _ = _make_rm(tmp_path, push=FakePush())
    assert rm.available() is True


def test_unavailable_without_target_and_push(tmp_path):
    rm, _ = _make_rm(tmp_path, admin="")
    assert rm.available() is False


def test_unavailable_without_wecom_secret(tmp_path):
    # 无注入 push、config 里企微密钥为空 -> 不可用（优雅降级）
    mgr = LifeManager(os.path.join(str(tmp_path), "manager.json"))
    cfg = {"admin_userid": "wang", "daily_greeting": {"time": "09:00", "text": "早安"},
           "wecom": {"corpid": "", "agentid": "", "secret": ""}}
    rm = ReminderManager(mgr, cfg, push=None)
    assert rm.available() is False


# ---------- 到期提醒 ----------

def test_due_todo_reminded_once(tmp_path):
    due = [{"text": "交房租", "due": "2026-08-05", "done": False, "reminded": False}]
    rm, mgr = _make_rm(tmp_path, push=FakePush(), due=due)
    now = datetime.datetime(2026, 8, 6)  # 已过期
    assert rm.check_due_todos(now) == 1
    assert rm.check_due_todos(now) == 0  # reminded 防重复
    assert mgr.data["todos"][0]["reminded"] is True
    assert len(rm._push_fn.sent) == 1
    assert "已于 2026-08-05 到期" in rm._push_fn.sent[0]


def test_due_today_text(tmp_path):
    due = [{"text": "交周报", "due": "2026-08-06", "done": False, "reminded": False}]
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=due)
    assert rm.check_due_todos(datetime.datetime(2026, 8, 6)) == 1
    assert "今天到期" in rm._push_fn.sent[0]


def test_future_todo_not_reminded(tmp_path):
    due = [{"text": "交房租", "due": "2026-09-01", "done": False, "reminded": False}]
    rm, mgr = _make_rm(tmp_path, push=FakePush(), due=due)
    assert rm.check_due_todos(datetime.datetime(2026, 8, 6)) == 0
    assert mgr.data["todos"][0]["reminded"] is False


def test_done_or_no_due_skipped(tmp_path):
    due = [{"text": "已完成", "due": "2000-01-01", "done": True, "reminded": False},
           {"text": "没日期", "due": "", "done": False, "reminded": False}]
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=due)
    assert rm.check_due_todos(datetime.datetime(2026, 8, 6)) == 0


def test_multiple_due_todos_merged(tmp_path):
    """多条待办同时到期 → 合并成一条提醒，防刷屏。"""
    due = [
        {"text": "交房租", "due": "2026-08-05", "done": False, "reminded": False},
        {"text": "交周报", "due": "2026-08-06", "done": False, "reminded": False},
        {"text": "买牛奶", "due": "2026-08-06", "done": False, "reminded": False},
    ]
    rm, mgr = _make_rm(tmp_path, push=FakePush(), due=due)
    assert rm.check_due_todos(datetime.datetime(2026, 8, 6)) == 3
    assert len(rm._push_fn.sent) == 1  # 只发一条
    text = rm._push_fn.sent[0]
    assert "3 条待办到期" in text
    assert "交房租（2026-08-05）" in text
    assert "交周报（今天）" in text
    assert "买牛奶（今天）" in text
    assert all(t["reminded"] for t in mgr.data["todos"])
    # 再次检查不重复提醒
    assert rm.check_due_todos(datetime.datetime(2026, 8, 6)) == 0


# ---------- 晨报 ----------

def test_digest_with_due_today(tmp_path):
    due = [{"text": "交房租", "due": "2026-08-06", "done": False, "reminded": False},
           {"text": "买牛奶", "due": "", "done": False, "reminded": False}]
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=due)
    text = rm.build_digest(datetime.datetime(2026, 8, 6, 9, 0))
    assert "早安" in text and "今日待办：交房租" in text


def test_digest_without_todo(tmp_path):
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=[])
    text = rm.build_digest(datetime.datetime(2026, 8, 6, 9, 0))
    assert "没有待办" in text


def test_digest_includes_memo_count(tmp_path):
    rm, mgr = _make_rm(tmp_path, push=FakePush(), due=[])
    mgr.data["memos"] = [{"text": "测试环境地址 http://x", "ts": 1}]
    text = rm.build_digest(datetime.datetime(2026, 8, 6, 9, 0))
    assert "备忘 1 条" in text
    assert "http://x" not in text  # 只报条数，不塞原文


def test_digest_no_memo_line_when_empty(tmp_path):
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=[])
    text = rm.build_digest(datetime.datetime(2026, 8, 6, 9, 0))
    assert "备忘" not in text


def test_digest_strips_emoji(tmp_path):
    # 问候语里的 emoji/波浪号会被过滤
    mgr = LifeManager(os.path.join(str(tmp_path), "manager.json"))
    cfg = {"admin_userid": "wang",
           "daily_greeting": {"time": "08:00", "text": "早安宝贝～今天也要元气满满哦 ☀️"}}
    rm = ReminderManager(mgr, cfg, push=FakePush())
    text = rm.build_digest(datetime.datetime(2026, 8, 6, 9, 0))
    assert "☀" not in text and "～" not in text
    assert "早安宝贝" in text


def test_digest_friday_tip(tmp_path):
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=[])
    text = rm.build_digest(datetime.datetime(2026, 8, 7, 8, 0))  # 周五
    assert "明天就是周末啦" in text


def test_digest_weekend_tip(tmp_path):
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=[])
    sat = rm.build_digest(datetime.datetime(2026, 8, 8, 8, 0))  # 周六
    sun = rm.build_digest(datetime.datetime(2026, 8, 9, 8, 0))  # 周日
    assert "周末愉快" in sat
    assert "明天又要上班啦" in sun


def test_add_memo_dedup(tmp_path):
    # 同一内容重复「记住」不再新增（_add_memo 不调模型，ds 传 None 即可）
    mgr = LifeManager(os.path.join(str(tmp_path), "manager.json"))
    mgr.handle("记住测试环境地址 http://x", None)
    handled, hint = mgr.handle("记住测试环境地址 http://x", None)
    assert handled is True
    assert "记过" in hint
    assert len(mgr.data["memos"]) == 1


def test_digest_sent_once_per_day(tmp_path):
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=[])
    assert rm.maybe_send_digest(datetime.datetime(2026, 8, 6, 9, 0)) is True
    assert rm.maybe_send_digest(datetime.datetime(2026, 8, 6, 9, 0)) is False  # 当天不重复
    assert rm.maybe_send_digest(datetime.datetime(2026, 8, 7, 9, 0)) is True   # 次日可再发
    assert len(rm._push_fn.sent) == 2


def test_digest_wrong_time_skipped(tmp_path):
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=[])
    assert rm.maybe_send_digest(datetime.datetime(2026, 8, 6, 12, 0)) is False


def test_digest_appends_weather(tmp_path):
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=[])
    rm._weather_fn = lambda: "北京 当前 29 度（晴）"
    assert rm.maybe_send_digest(datetime.datetime(2026, 8, 6, 9, 0)) is True
    assert "北京 当前 29 度（晴）" in rm._push_fn.sent[0]


def test_digest_weather_failure_keeps_digest(tmp_path):
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=[])
    rm._weather_fn = lambda: None  # 天气查不到
    assert rm.maybe_send_digest(datetime.datetime(2026, 8, 6, 9, 0)) is True  # 晨报照常发
    assert "早安" in rm._push_fn.sent[0]


def test_digest_appends_news(tmp_path):
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=[])
    rm._news_fn = lambda: "今日科技/AI 新闻：\nDeepSeek 发布 V4 Flash（InfoQ）"
    assert rm.maybe_send_digest(datetime.datetime(2026, 8, 6, 9, 0)) is True
    assert "DeepSeek 发布 V4 Flash" in rm._push_fn.sent[0]


def test_digest_news_failure_keeps_digest(tmp_path):
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=[])
    rm._news_fn = lambda: None  # 新闻抓不到
    assert rm.maybe_send_digest(datetime.datetime(2026, 8, 6, 9, 0)) is True  # 晨报照常发
    assert "早安" in rm._push_fn.sent[0]


# ---------- 通用定时任务（每天/每周/一次） ----------

def test_task_daily_fires_once_per_day(tmp_path):
    """每天任务到点执行一次，同一天不重复。"""
    push = FakePush()
    rm, mgr = _make_rm(tmp_path, push=push)
    mgr.data["tasks"] = [{"type": "daily", "time": "09:00", "text": "查天气",
                           "action": "remind", "fired": False, "last_fired": ""}]
    assert rm.check_due_tasks(datetime.datetime(2026, 8, 6, 9, 0)) == 1
    assert "查天气" in push.sent[0]
    assert rm.check_due_tasks(datetime.datetime(2026, 8, 6, 9, 1)) == 0  # 同天不重复
    assert rm.check_due_tasks(datetime.datetime(2026, 8, 7, 9, 0)) == 1  # 第二天再触发
    assert len(push.sent) == 2


def test_task_weekly_matches_weekday_only(tmp_path):
    """每周五的任务只在周五触发。"""
    push = FakePush()
    rm, mgr = _make_rm(tmp_path, push=push)
    mgr.data["tasks"] = [{"type": "weekly", "time": "17:00", "weekday": 4,
                           "text": "写周报", "action": "remind",
                           "fired": False, "last_fired": ""}]
    # 周四 17:00 不触发
    assert rm.check_due_tasks(datetime.datetime(2026, 8, 6, 17, 0)) == 0  # 周四
    # 周五 17:00 触发
    assert rm.check_due_tasks(datetime.datetime(2026, 8, 7, 17, 0)) == 1  # 周五
    assert "写周报" in push.sent[0]
    # 周五其他时间不触发
    assert rm.check_due_tasks(datetime.datetime(2026, 8, 7, 18, 0)) == 0


def test_task_once_fires_and_expires(tmp_path):
    """一次性任务触发后标记 fired，不再重复。"""
    push = FakePush()
    rm, mgr = _make_rm(tmp_path, push=push)
    mgr.data["tasks"] = [{"type": "once", "at": "2026-08-06 10:00",
                           "text": "开会", "action": "remind",
                           "fired": False, "last_fired": ""}]
    assert rm.check_due_tasks(datetime.datetime(2026, 8, 6, 9, 59)) == 0  # 还没到
    assert rm.check_due_tasks(datetime.datetime(2026, 8, 6, 10, 0)) == 1
    assert "开会" in push.sent[0]
    assert mgr.data["tasks"][0]["fired"] is True
    assert rm.check_due_tasks(datetime.datetime(2026, 8, 6, 10, 1)) == 0  # 已触发


def test_task_weather_action_pushes_weather(tmp_path):
    """action=weather 且 weather_fn 有数据 → 推天气，不推提醒文本。"""
    push = FakePush()
    rm, mgr = _make_rm(tmp_path, push=push)
    mgr.data["tasks"] = [{"type": "daily", "time": "09:00", "text": "查天气",
                           "action": "weather", "fired": False, "last_fired": ""}]
    rm._weather_fn = lambda: "深圳 当前 29 度（晴）"
    assert rm.check_due_tasks(datetime.datetime(2026, 8, 6, 9, 0)) == 1
    assert "深圳 当前 29 度" in push.sent[0]
    assert "查天气" not in push.sent[0]


def test_task_weather_action_fallback_to_text(tmp_path):
    """action=weather 但天气查不到 → 退化为提醒文本。"""
    push = FakePush()
    rm, mgr = _make_rm(tmp_path, push=push)
    mgr.data["tasks"] = [{"type": "daily", "time": "09:00", "text": "查天气",
                           "action": "weather", "fired": False, "last_fired": ""}]
    rm._weather_fn = lambda: None
    assert rm.check_due_tasks(datetime.datetime(2026, 8, 6, 9, 0)) == 1
    assert "查天气" in push.sent[0]


def test_task_music_action_pushes_song(tmp_path):
    """action=music 且 music_fn 有数据 → 推单曲（含封面图），不推提醒文本。"""
    push = FakePush()
    rm, mgr = _make_rm(tmp_path, push=push)
    mgr.data["tasks"] = [{"type": "daily", "time": "07:30", "text": "每日单曲",
                           "action": "music", "fired": False, "last_fired": ""}]
    rm._music_fn = lambda: {
        "text": "今日单曲：海屿你 - 马也_Crabbit\n热评：你走后，我一直失眠",
        "image": b"cover-bytes", "qr": b"qr-bytes"}
    assert rm.check_due_tasks(datetime.datetime(2026, 8, 6, 7, 30)) == 1
    assert push.sent[0] == ""                        # 封面图无文字
    assert "海屿你" in push.sent[1] and "热评" in push.sent[1]  # 歌名+热评
    assert "识别" in push.sent[2]                    # 二维码提示
    assert push.images == [b"cover-bytes", b"qr-bytes"]  # 封面 + 二维码
    assert "music.163.com" not in "".join(push.sent)  # 链接已移除


def test_task_music_action_fallback_to_text(tmp_path):
    """action=music 但单曲抓不到 → 退化为提醒文本。"""
    push = FakePush()
    rm, mgr = _make_rm(tmp_path, push=push)
    mgr.data["tasks"] = [{"type": "daily", "time": "07:30", "text": "每日单曲",
                           "action": "music", "fired": False, "last_fired": ""}]
    rm._music_fn = lambda: None
    assert rm.check_due_tasks(datetime.datetime(2026, 8, 6, 7, 30)) == 1
    assert "每日单曲" in push.sent[0]


# ---------- 节日倒计时 / 睡前总结 / 数据清理 ----------

def test_digest_has_countdown(tmp_path):
    """晨报包含未来节日倒计时（用真实农历：2026-08-06 之后的最近节日）。"""
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=[])
    text = rm.build_digest(datetime.datetime(2026, 8, 6, 8, 0))
    assert "还有" in text and "天" in text


def test_next_festival_finds_recent(tmp_path):
    """倒计时能算出未来 90 天内最近的节日及天数。"""
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=[])
    f = rm._next_festival(datetime.datetime(2026, 8, 6, 8, 0))
    assert f is not None
    name, date, delta = f
    assert 0 < delta <= 90
    assert date > datetime.date(2026, 8, 6)


def test_next_lunar_festival_mid_autumn(tmp_path):
    """2026 年 8 月 6 日之后最近的农历节日是七夕（8/19）。"""
    try:
        from zhdate import ZhDate  # noqa: F401
    except ImportError:
        import pytest
        pytest.skip("需要 zhdate")
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=[])
    f = rm._next_lunar_festival(datetime.datetime(2026, 8, 6, 8, 0))
    assert f is not None and f[0] == "七夕"


def test_build_summary_empty(tmp_path):
    rm, mgr = _make_rm(tmp_path, push=FakePush(), due=[])
    text = rm.build_summary(datetime.datetime(2026, 8, 6, 22, 0))
    assert "没有留下记录" in text


def test_build_summary_counts_today(tmp_path):
    rm, mgr = _make_rm(tmp_path, push=FakePush(), due=[])
    mgr.data["todos"] = [
        {"text": "交房租", "done": True, "done_ts": "2026-08-06 10:00"},
        {"text": "买牛奶", "done": True, "done_ts": "2026-08-05 10:00"},  # 昨天的不算
        {"text": "没做完", "done": False},
    ]
    mgr.data["memos"] = [
        {"text": "地址", "ts": int(datetime.datetime(2026, 8, 6, 12, 0).timestamp())},
        {"text": "旧备忘", "ts": int(datetime.datetime(2026, 7, 1, 12, 0).timestamp())},
    ]
    text = rm.build_summary(datetime.datetime(2026, 8, 6, 22, 0))
    assert "交房租" in text and "买牛奶" not in text
    assert "记了 1 条备忘" in text


def test_summary_includes_expenses(tmp_path, monkeypatch):
    """今天有记账 → 总结里带支出。"""
    import tools
    monkeypatch.setattr(tools, "EXPENSES_PATH", str(tmp_path / "expenses.json"))
    tools._save_expenses({"items": [
        {"amount": 35, "category": "吃饭", "note": "午餐", "date": "2026-08-06"},
        {"amount": 12, "category": "交通", "note": "地铁", "date": "2026-08-06"},
        {"amount": 99, "category": "购物", "note": "昨天", "date": "2026-08-05"},
    ]})
    rm, mgr = _make_rm(tmp_path, push=FakePush(), due=[])
    text = rm.build_summary(datetime.datetime(2026, 8, 6, 22, 0))
    assert "今天花了 47 元" in text and "吃饭" in text


def test_maybe_send_summary_once_per_day(tmp_path):
    push = FakePush()
    rm, _ = _make_rm(tmp_path, push=push)
    assert rm.maybe_send_summary(datetime.datetime(2026, 8, 6, 22, 0)) is True
    assert rm.maybe_send_summary(datetime.datetime(2026, 8, 6, 22, 1)) is False  # 当天不重复
    assert rm.maybe_send_summary(datetime.datetime(2026, 8, 6, 21, 59)) is False  # 没到点


def test_maybe_cleanup_removes_stale(tmp_path):
    rm, mgr = _make_rm(tmp_path, push=FakePush(), due=[])
    mgr.data["tasks"] = [
        {"type": "once", "at": "2026-08-01 10:00", "fired": True, "last_fired": ""},  # 超 3 天
        {"type": "daily", "time": "09:00", "fired": False, "last_fired": "2026-08-06"},  # 保留
    ]
    mgr.data["timers"] = [
        {"at": "2026-08-01 10:00", "fired": True},   # 超 3 天
        {"at": "2026-08-06 15:00", "fired": False},  # 保留
    ]
    mgr.data["todos"] = [
        {"text": "老完成", "done": True, "done_ts": "2026-07-01 10:00"},   # 超 7 天
        {"text": "新完成", "done": True, "done_ts": "2026-08-05 10:00"},   # 保留
        {"text": "没完成", "done": False},
    ]
    assert mgr.cleanup_old_data(datetime.datetime(2026, 8, 6, 12, 0)) == 3
    assert len(mgr.data["tasks"]) == 1 and len(mgr.data["timers"]) == 1
    assert len(mgr.data["todos"]) == 2
    assert all(not (t.get("done") and t.get("done_ts") == "2026-07-01 10:00")
               for t in mgr.data["todos"])


# ---------- 定时提醒（上下班打卡） ----------

def _cfg_with_clock(extra=None):
    cfg = {"admin_userid": "wang",
           "daily_greeting": {"time": "08:00", "text": "早上好"},
           "clock_reminders": {"enabled": True, "times": [
               {"time": "08:25", "text": "上班打卡时间到"},
               {"time": "18:00", "text": "下班时间到"},
           ]}}
    if extra:
        cfg["clock_reminders"].update(extra)
    return cfg


def _make_rm_clock(tmp_path, push, cfg=None):
    mgr = LifeManager(os.path.join(str(tmp_path), "manager.json"))
    return ReminderManager(mgr, cfg or _cfg_with_clock(), push=push), mgr


def test_clock_sends_at_time(tmp_path):
    push = FakePush()
    rm, _ = _make_rm_clock(tmp_path, push)
    assert rm.maybe_send_clock_reminders(datetime.datetime(2026, 8, 6, 8, 25)) == 1
    assert push.sent == ["上班打卡时间到"]


def test_clock_no_duplicate_same_day(tmp_path):
    push = FakePush()
    rm, _ = _make_rm_clock(tmp_path, push)
    t = datetime.datetime(2026, 8, 6, 18, 0)
    assert rm.maybe_send_clock_reminders(t) == 1
    assert rm.maybe_send_clock_reminders(t) == 0  # 同一天同一时间点不重复
    assert len(push.sent) == 1


def test_clock_multiple_times_same_day(tmp_path):
    push = FakePush()
    rm, _ = _make_rm_clock(tmp_path, push)
    assert rm.maybe_send_clock_reminders(datetime.datetime(2026, 8, 6, 8, 25)) == 1
    assert rm.maybe_send_clock_reminders(datetime.datetime(2026, 8, 6, 18, 0)) == 1
    assert len(push.sent) == 2  # 两个时间点各推一次


def test_clock_disabled(tmp_path):
    push = FakePush()
    rm, _ = _make_rm_clock(tmp_path, push, _cfg_with_clock({"enabled": False}))
    assert rm.maybe_send_clock_reminders(datetime.datetime(2026, 8, 6, 8, 25)) == 0
    assert push.sent == []


def test_clock_wrong_time_no_send(tmp_path):
    push = FakePush()
    rm, _ = _make_rm_clock(tmp_path, push)
    assert rm.maybe_send_clock_reminders(datetime.datetime(2026, 8, 6, 9, 0)) == 0
    assert push.sent == []


# ---------- 定时提醒（任意时间点） ----------

def test_timer_due_push_once(tmp_path):
    push = FakePush()
    rm, mgr = _make_rm(tmp_path, push=push)
    mgr.data["timers"] = [{"at": "2026-08-06 09:00", "text": "开会", "fired": False}]
    assert rm.check_due_timers(datetime.datetime(2026, 8, 6, 9, 1)) == 1
    assert "开会" in push.sent[0] and "时间到" in push.sent[0]
    assert rm.check_due_timers(datetime.datetime(2026, 8, 6, 9, 2)) == 0  # 不重复


def test_timer_not_due_skipped(tmp_path):
    push = FakePush()
    rm, mgr = _make_rm(tmp_path, push=push)
    mgr.data["timers"] = [{"at": "2026-08-06 15:00", "text": "开会", "fired": False}]
    assert rm.check_due_timers(datetime.datetime(2026, 8, 6, 9, 0)) == 0
    assert push.sent == []


def test_timer_bad_at_skipped(tmp_path):
    push = FakePush()
    rm, mgr = _make_rm(tmp_path, push=push)
    mgr.data["timers"] = [{"at": "乱写", "text": "x", "fired": False}]
    assert rm.check_due_timers(datetime.datetime(2026, 8, 6, 9, 0)) == 0


# ---------- 节气推送 ----------

def test_season_note_on_term_day(tmp_path):
    push = FakePush()
    rm, _ = _make_rm(tmp_path, push=push)
    assert rm.maybe_send_season_note(datetime.datetime(2026, 8, 7, 8, 0)) is True  # 立秋
    assert "立秋" in push.sent[0]
    assert rm.maybe_send_season_note(datetime.datetime(2026, 8, 7, 9, 0)) is False  # 当天不重复


def test_season_note_normal_day(tmp_path):
    push = FakePush()
    rm, _ = _make_rm(tmp_path, push=push)
    assert rm.maybe_send_season_note(datetime.datetime(2026, 8, 6, 8, 0)) is False
    assert push.sent == []


def test_day_note_includes_solar_term(tmp_path):
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=[])
    assert rm._day_note(datetime.datetime(2026, 8, 7)) == "今天是立秋"


# ---------- 天气预警 ----------

def test_weather_alert_sends_on_rain(tmp_path):
    push = FakePush()
    rm, _ = _make_rm(tmp_path, push=push)
    rm._alert_time = "07:30"
    rm._weather_alert_fn = lambda: "今天深圳天气小雨，出门记得带伞"
    assert rm.maybe_send_weather_alert(datetime.datetime(2026, 8, 6, 7, 30)) is True
    assert "带伞" in push.sent[0]
    assert rm.maybe_send_weather_alert(datetime.datetime(2026, 8, 6, 7, 31)) is False


def test_weather_alert_wrong_time(tmp_path):
    push = FakePush()
    rm, _ = _make_rm(tmp_path, push=push)
    rm._alert_time = "07:30"
    rm._weather_alert_fn = lambda: "带伞"
    assert rm.maybe_send_weather_alert(datetime.datetime(2026, 8, 6, 8, 0)) is False
    assert push.sent == []


def test_weather_alert_no_alert_skipped(tmp_path):
    push = FakePush()
    rm, _ = _make_rm(tmp_path, push=push)
    rm._alert_time = "07:30"
    rm._weather_alert_fn = lambda: None  # 今天没雨没高温
    assert rm.maybe_send_weather_alert(datetime.datetime(2026, 8, 6, 7, 30)) is False
    assert push.sent == []


# ---------- 节日 / 重要日子 ----------

def test_day_note_fixed_holiday(tmp_path):
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=[])
    assert rm._day_note(datetime.datetime(2026, 10, 1)) == "今天是国庆节"
    assert rm._day_note(datetime.datetime(2026, 9, 30), delta=1) == "明天是国庆节"


def test_day_note_important_date(tmp_path):
    class FakeMemory:
        data = {"important_dates": [{"date": "08-15", "event": "宝贝生日"}]}

    rm, _ = _make_rm(tmp_path, push=FakePush(), due=[])
    rm.memory = FakeMemory()
    assert rm._day_note(datetime.datetime(2026, 8, 15)) == "今天是宝贝生日"
    assert rm._day_note(datetime.datetime(2026, 8, 14), delta=1) == "明天是宝贝生日"


def test_day_note_empty(tmp_path):
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=[])
    assert rm._day_note(datetime.datetime(2026, 8, 10)) == ""  # 普通日子
    assert rm._day_note(datetime.datetime(2026, 8, 10), delta=1) == ""


def test_digest_includes_holiday(tmp_path):
    rm, _ = _make_rm(tmp_path, push=FakePush(), due=[])
    text = rm.build_digest(datetime.datetime(2026, 10, 1, 8, 0))
    assert "国庆节" in text


def test_digest_includes_important_date(tmp_path):
    class FakeMemory:
        data = {"important_dates": [{"date": "08-15", "event": "宝贝生日"}]}

    rm, _ = _make_rm(tmp_path, push=FakePush(), due=[])
    rm.memory = FakeMemory()
    text = rm.build_digest(datetime.datetime(2026, 8, 15, 8, 0))
    assert "今天是宝贝生日" in text
