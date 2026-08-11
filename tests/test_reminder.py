# -*- coding: utf-8 -*-
"""reminder.py 单元测试：可用性判定、到期提醒防重复、晨报组装、推送注入。"""
import datetime
import os

from reminder import ReminderManager
from manager import LifeManager


class FakePush:
    def __init__(self):
        self.sent = []

    def __call__(self, text):
        self.sent.append(text)
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
