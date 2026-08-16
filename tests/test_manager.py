# -*- coding: utf-8 -*-
"""manager.py 单元测试：关键词路由、备忘录/待办增删查、JSON 容错、降级路径。"""
import json
import os

from manager import LifeManager


class FakeDS:
    """可参数化的假模型：默认返回"交房租"待办，也可指定其他内容。"""

    def __init__(self, text="交房租", due="2026-08-05"):
        self.text = text
        self.due = due

    def chat(self, messages, **kw):
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
