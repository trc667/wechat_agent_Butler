# -*- coding: utf-8 -*-
"""manager.py 单元测试：关键词路由、备忘录/待办增删查、JSON 容错、降级路径。"""
import json
import os

from manager import LifeManager


class FakeDS:
    """可参数化的假模型：默认返回"交房租"待办，也可指定其他内容。"""

    def __init__(self, text="交房租", due="2026-08-05", raw=None):
        self.text = text
        self.due = due
        self.raw = raw

    def chat(self, messages, **kw):
        if self.raw:
            return self.raw
        return json.dumps({"text": self.text, "due": self.due})


class BadDS:
    """假模型：输出非 JSON 的废话，验证降级路径。"""

    def chat(self, messages, **kw):
        return "模型没输出 json，说了一堆废话"


def _make_mgr(tmp_path):
    return LifeManager(os.path.join(str(tmp_path), "manager.json"))


# ---------- 路由识别 ----------

def test_route_memo_query_empty(tmp_path):
    mgr = _make_mgr(tmp_path)
    handled, hint = mgr.handle("备忘录", FakeDS())
    assert handled is True
    assert "没有记任何东西" in hint


# ---------- 备忘录更新（用户说"不正确"时更新而非去重） ----------

def test_add_memo_updates_similar(tmp_path):
    """同主题但内容不同（如地址变了）：重新记住 = 更新替换，不新增。"""
    mgr = _make_mgr(tmp_path)
    mgr.handle("记住线上测试环境地址 http://10.10.0.8:8080", None)
    handled, hint = mgr.handle("记住线上测试环境地址 http://10.10.0.9:9090", None)
    assert handled is True
    assert len(mgr.data["memos"]) == 1  # 不新增
    assert mgr.data["memos"][0]["text"] == "线上测试环境地址 http://10.10.0.9:9090"
    assert "更新" in hint


def test_add_memo_exact_still_dedup(tmp_path):
    """完全相同的内容仍然去重（防重复堆叠）。"""
    mgr = _make_mgr(tmp_path)
    mgr.handle("记住测试环境地址 http://x", None)
    handled, hint = mgr.handle("记住测试环境地址 http://x", None)
    assert handled is True
    assert "记过" in hint
    assert len(mgr.data["memos"]) == 1


def test_update_memo_replace(tmp_path):
    """「改一下xxx为yyy」→ 找到旧条目替换。"""
    mgr = _make_mgr(tmp_path)
    mgr.handle("记住测试环境地址 http://10.10.0.8:8080", None)
    handled, hint = mgr.handle("改一下测试环境地址为 http://192.168.1.1:9000", None)
    assert handled is True
    assert len(mgr.data["memos"]) == 1
    assert "192.168.1.1:9000" in mgr.data["memos"][0]["text"]
    assert "更新" in hint


def test_update_memo_no_match_appends(tmp_path):
    """更新不存在的条目：作为新备忘存下。"""
    mgr = _make_mgr(tmp_path)
    handled, hint = mgr.handle("改一下公司门禁密码为 123456", None)
    assert handled is True
    assert len(mgr.data["memos"]) == 1
    assert "门禁密码" in mgr.data["memos"][0]["text"]


def test_route_plain_chat_passthrough(tmp_path):
    mgr = _make_mgr(tmp_path)
    handled, hint = mgr.handle("今天天气怎么样", FakeDS())
    assert handled is False
    assert hint is None


# ---------- 定时提醒 ----------

class TimerDS:
    """假模型：返回定时提醒 JSON。"""

    def __init__(self, at="2026-08-06 15:00", text="开会"):
        self.at = at
        self.text = text

    def chat(self, messages, **kw):
        return json.dumps({"at": self.at, "text": self.text})


def test_add_timer_route(tmp_path):
    mgr = _make_mgr(tmp_path)
    handled, hint = mgr.handle("下午3点提醒我开会", TimerDS())
    assert handled is True
    assert len(mgr.data["timers"]) == 1
    assert mgr.data["timers"][0]["at"] == "2026-08-06 15:00"
    assert mgr.data["timers"][0]["text"] == "开会"
    assert mgr.data["timers"][0]["fired"] is False
    assert "15:00" in hint


def test_add_timer_no_time(tmp_path):
    mgr = _make_mgr(tmp_path)
    handled, hint = mgr.handle("提醒我开会", TimerDS(at=""))
    assert handled is True
    assert mgr.data["timers"] == []
    assert "时间" in hint  # 提示要说明时间


def test_timer_not_triggered_by_normal_chat(tmp_path):
    mgr = _make_mgr(tmp_path)
    handled, _ = mgr.handle("今天的天气怎么样", FakeDS())
    assert handled is False


# ---------- 定时提醒管理（查/取消） ----------

def _seed_timers(mgr):
    mgr.data["timers"] = [
        {"at": "2026-08-06 15:00", "text": "开会", "fired": False},
        {"at": "2026-08-07 09:00", "text": "交周报", "fired": False},
        {"at": "2026-08-05 10:00", "text": "已触发", "fired": True},
    ]


def test_hint_timers_lists_pending(tmp_path):
    mgr = _make_mgr(tmp_path)
    _seed_timers(mgr)
    handled, hint = mgr.handle("我有哪些提醒", None)
    assert handled is True
    assert "开会" in hint and "交周报" in hint
    assert "已触发" not in hint  # 已触发的列出


def test_hint_timers_empty(tmp_path):
    mgr = _make_mgr(tmp_path)
    handled, hint = mgr.handle("看看提醒", None)
    assert handled is True
    assert "没有" in hint


def test_cancel_timer_route(tmp_path):
    """「取消3点的提醒」→ 取消提醒（不是完成待办）。"""
    mgr = _make_mgr(tmp_path)
    _seed_timers(mgr)
    handled, hint = mgr.handle("取消15点的提醒", None)
    assert handled is True
    assert "开会" in hint
    assert len(mgr.data["timers"]) == 2  # 删掉一条
    assert not any(t["text"] == "开会" for t in mgr.data["timers"])


def test_cancel_timer_by_text(tmp_path):
    mgr = _make_mgr(tmp_path)
    _seed_timers(mgr)
    handled, _ = mgr.handle("忘掉交周报的提醒", None)
    assert handled is True
    assert not any(t["text"] == "交周报" for t in mgr.data["timers"])


def test_cancel_timer_not_found(tmp_path):
    mgr = _make_mgr(tmp_path)
    _seed_timers(mgr)
    handled, hint = mgr.handle("取消不存在的提醒", None)
    assert handled is True
    assert "没找到" in hint
    assert len(mgr.data["timers"]) == 3  # 没删


def test_cancel_todo_still_works(tmp_path):
    """「取消交房租」仍走待办完成，不受提醒取消影响。"""
    mgr = _make_mgr(tmp_path)
    mgr.data["todos"] = [{"text": "交房租", "due": "", "done": False,
                           "reminded": False}]
    handled, hint = mgr.handle("取消交房租", None)
    assert handled is True
    assert mgr.data["todos"][0]["done"] is True


# ---------- 通用定时任务（每天/每周/一次） ----------

def test_task_daily_route_and_store(tmp_path):
    """「每天早上9点查天气」→ 模型提取 daily 规则存入 tasks。"""
    mgr = _make_mgr(tmp_path)
    ds = FakeDS(raw=json.dumps({"type": "daily", "time": "09:00",
                                "text": "查天气", "action": "weather"}))
    handled, hint = mgr.handle("每天早上9点查天气推给我", ds)
    assert handled is True
    assert len(mgr.data["tasks"]) == 1
    t = mgr.data["tasks"][0]
    assert t["type"] == "daily" and t["time"] == "09:00"
    assert t["action"] == "weather"


def test_task_daily_music_route_and_store(tmp_path):
    """「每天早上7点半推每日单曲」→ daily 规则 + action=music。"""
    mgr = _make_mgr(tmp_path)
    ds = FakeDS(raw=json.dumps({"type": "daily", "time": "07:30",
                                "text": "每日单曲", "action": "music"}))
    handled, hint = mgr.handle("每天早上7点半推每日单曲给我", ds)
    assert handled is True
    t = mgr.data["tasks"][0]
    assert t["type"] == "daily" and t["time"] == "07:30"
    assert t["action"] == "music"


def test_task_weekly_route_and_store(tmp_path):
    """「每周五下午5点提醒我写周报」→ weekly 规则，weekday=4。"""
    mgr = _make_mgr(tmp_path)
    ds = FakeDS(raw=json.dumps({"type": "weekly", "time": "17:00",
                                "weekday": 4, "text": "写周报",
                                "action": "remind"}))
    handled, hint = mgr.handle("每周五下午5点提醒我写周报", ds)
    assert handled is True
    t = mgr.data["tasks"][0]
    assert t["type"] == "weekly" and t["weekday"] == 4 and t["time"] == "17:00"


def test_task_once_route_and_store(tmp_path):
    """「设个定时任务明天上午10点提醒我开会」→ once 规则。"""
    mgr = _make_mgr(tmp_path)
    ds = FakeDS(raw=json.dumps({"type": "once", "at": "2026-08-20 10:00",
                                "text": "开会", "action": "remind"}))
    handled, hint = mgr.handle("设个定时任务明天上午10点提醒我开会", ds)
    assert handled is True
    t = mgr.data["tasks"][0]
    assert t["type"] == "once" and t["at"] == "2026-08-20 10:00"
    assert t["fired"] is False


def test_task_bad_extract_falls_back(tmp_path):
    """模型完全没输出 JSON → 提示没听清，不落库。"""
    mgr = _make_mgr(tmp_path)
    handled, hint = mgr.handle("每天早上9点查天气", BadDS())
    assert handled is True
    assert "没听清" in hint
    assert mgr.data["tasks"] == []


def test_task_bad_type_extract_falls_back(tmp_path):
    """模型输出了内容但类型非法 → 提示说清重复规律，不落库。"""
    mgr = _make_mgr(tmp_path)
    ds = FakeDS(raw=json.dumps({"type": "hourly", "time": "09:00",
                                "text": "查天气", "action": "remind"}))
    handled, hint = mgr.handle("每天早上9点查天气", ds)
    assert handled is True
    assert "规律" in hint
    assert mgr.data["tasks"] == []


def test_task_add_direct(tmp_path):
    mgr = _make_mgr(tmp_path)
    out = mgr.add_task_direct("weekly", "写周报", time="17:00", weekday=4)
    assert "已设置" in out and "每周五" in out
    assert mgr.data["tasks"][0]["weekday"] == 4


def test_task_hint_and_cancel(tmp_path):
    mgr = _make_mgr(tmp_path)
    mgr.add_task_direct("daily", "查天气", time="09:00", action="weather")
    mgr.add_task_direct("weekly", "写周报", time="17:00", weekday=4)
    handled, hint = mgr.handle("我有哪些定时任务", None)
    assert handled is True
    assert "每天 09:00" in hint and "每周五 17:00" in hint
    assert "查天气" in hint and "推天气" in hint   # 带具体内容和动作
    assert "写周报" in hint
    # 取消「每天早上9点的天气推送」
    handled, hint = mgr.handle("取消每天早上9点的天气推送", None)
    assert handled is True
    assert len(mgr.data["tasks"]) == 1
    assert mgr.data["tasks"][0]["text"] == "写周报"


def test_task_hint_which_variant(tmp_path):
    """「定时任务是哪些」「我有什么定时任务」也能查到。"""
    mgr = _make_mgr(tmp_path)
    mgr.add_task_direct("daily", "查天气", time="09:00")
    for q in ["定时任务是哪些", "定时任务是什么", "我有什么定时任务", "定时任务列表"]:
        handled, hint = mgr.handle(q, None)
        assert handled is True, q
        assert "每天 09:00" in hint and "查天气" in hint, q  # 带具体内容


def test_task_del_not_found(tmp_path):
    mgr = _make_mgr(tmp_path)
    mgr.add_task_direct("daily", "查天气", time="09:00")
    handled, hint = mgr.handle("取消每周五的提醒", None)
    assert handled is True
    assert "没找到" in hint
    assert len(mgr.data["tasks"]) == 1


def test_hint_tasks_includes_system_items(tmp_path):
    """带 cfg 时，打卡提醒/晨报/天气预警/睡前总结也会列出来。"""
    cfg = {
        "clock_reminders": {"enabled": True, "times": [
            {"time": "08:25", "text": "上班打卡"},
            {"time": "18:00", "text": "下班打卡"}]},
        "daily_greeting": {"enabled": True, "time": "08:00", "text": "早上好"},
        "daily_summary": {"enabled": True, "time": "22:00"},
        "weather_alert_time": "07:30",
    }
    mgr = LifeManager(os.path.join(str(tmp_path), "manager.json"), cfg=cfg)
    handled, hint = mgr.handle("定时任务是哪些", None)
    assert handled is True
    assert "每天 08:25：打卡提醒" in hint
    assert "每天 18:00：打卡提醒" in hint
    assert "每日晨报" in hint and "睡前总结" in hint
    assert "天气预警" in hint


def test_route_empty_text(tmp_path):
    mgr = _make_mgr(tmp_path)
    handled, hint = mgr.handle("   ", FakeDS())
    assert handled is False
    assert hint is None


def test_route_memo_query_keywords(tmp_path):
    mgr = _make_mgr(tmp_path)
    for q in ["备忘录", "我记过什么", "记住了什么", "记了什么"]:
        handled, _ = mgr.handle(q, FakeDS())
        assert handled is True, q


# ---------- 备忘录 ----------

def test_add_memo_strips_prefix(tmp_path):
    mgr = _make_mgr(tmp_path)
    for cmd, expect in [("记住ABC", "ABC"), ("记下DEF", "DEF"),
                        ("帮我记住GHI", "GHI"), ("备忘JKL", "JKL")]:
        mgr.handle(cmd, FakeDS())
    assert [m["text"] for m in mgr.data["memos"]] == ["ABC", "DEF", "GHI", "JKL"]


def test_add_memo_empty_content(tmp_path):
    mgr = _make_mgr(tmp_path)
    handled, hint = mgr.handle("记住", FakeDS())
    assert handled is True
    assert "问他要记什么" in hint
    assert mgr.data["memos"] == []


def test_memo_cap_30(tmp_path):
    mgr = _make_mgr(tmp_path)
    for i in range(35):
        mgr.handle("记住内容%d" % i, FakeDS())
    assert len(mgr.data["memos"]) == 30
    assert mgr.data["memos"][0]["text"] == "内容5"


def test_memo_prompt_empty_then_renders(tmp_path):
    mgr = _make_mgr(tmp_path)
    assert mgr.memo_prompt() == ""
    mgr.handle("记住线上地址 http://x", FakeDS())
    prompt = mgr.memo_prompt()
    assert "备忘录" in prompt and "http://x" in prompt


def test_del_memo_exact_hit(tmp_path):
    mgr = _make_mgr(tmp_path)
    mgr.handle("记住测试环境地址 http://10.10.0.8:8080", FakeDS())
    handled, hint = mgr.handle("忘掉测试环境地址", FakeDS())
    assert handled is True
    assert mgr.data["memos"] == []
    assert "已删掉" in hint


def test_del_memo_multiple_hits_asks(tmp_path):
    mgr = _make_mgr(tmp_path)
    # 两条都含关键词但内容不同（不会被自动合并），删除时需用户确认是哪条
    mgr.handle("记住线上测试环境地址 http://10.10.0.8:8080", FakeDS())
    mgr.handle("记住测试环境管理员账号 admin/123", FakeDS())
    handled, hint = mgr.handle("忘掉测试环境", FakeDS())
    assert handled is True
    assert "好几条" in hint
    assert len(mgr.data["memos"]) == 2  # 没删，等用户确认


def test_del_memo_not_found(tmp_path):
    mgr = _make_mgr(tmp_path)
    handled, hint = mgr.handle("忘掉不存在的", FakeDS())
    assert handled is True
    assert "没找到" in hint


# ---------- 待办 ----------

def test_add_todo_with_due(tmp_path):
    mgr = _make_mgr(tmp_path)
    handled, hint = mgr.handle("记一下周三交房租", FakeDS())
    assert handled is True
    todo = mgr.data["todos"][0]
    assert todo["text"] == "交房租"
    assert todo["due"] == "2026-08-05"
    assert todo["done"] is False
    assert "2026-08-05" in hint


def test_add_todo_bad_json_degrades(tmp_path):
    mgr = _make_mgr(tmp_path)
    handled, hint = mgr.handle("记一下周三交房租", BadDS())
    assert handled is True
    assert "问他要记啥" in hint
    assert mgr.data["todos"] == []


def test_add_todo_invalid_due_cleared(tmp_path):
    class WeirdDS(FakeDS):
        def chat(self, messages, **kw):
            return '{"text": "买牛奶", "due": "下周三"}'

    mgr = _make_mgr(tmp_path)
    mgr.handle("记一下买牛奶", WeirdDS())
    todo = mgr.data["todos"][0]
    assert todo["text"] == "买牛奶"
    assert todo["due"] == ""  # 抽歪的日期宁可当天不设


def test_hint_todos_empty(tmp_path):
    mgr = _make_mgr(tmp_path)
    handled, hint = mgr.handle("我有哪些待办", FakeDS())
    assert handled is True
    assert "一条都没有" in hint


def test_done_todo(tmp_path):
    mgr = _make_mgr(tmp_path)
    mgr.handle("记一下交房租", FakeDS())
    handled, hint = mgr.handle("完成了交房租", FakeDS())
    assert handled is True
    assert mgr.data["todos"][0]["done"] is True
    assert "划掉" in hint


def test_done_todo_not_found(tmp_path):
    mgr = _make_mgr(tmp_path)
    handled, hint = mgr.handle("完成了不存在的", FakeDS())
    assert handled is True
    assert "没找到" in hint


def test_done_todo_hint_excludes_done(tmp_path):
    mgr = _make_mgr(tmp_path)
    mgr.handle("记一下交房租", FakeDS("交房租"))
    mgr.handle("记一下买牛奶", FakeDS("买牛奶", ""))
    mgr.handle("完成了交房租", FakeDS())
    handled, hint = mgr.handle("我有哪些待办", FakeDS())
    assert handled is True
    assert "买牛奶" in hint
    assert "交房租" not in hint
