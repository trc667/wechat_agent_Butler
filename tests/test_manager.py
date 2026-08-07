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


def test_route_plain_chat_passthrough(tmp_path):
    mgr = _make_mgr(tmp_path)
    handled, hint = mgr.handle("今天天气怎么样", FakeDS())
    assert handled is False
    assert hint is None


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
    mgr.handle("记住测试环境地址A", FakeDS())
    mgr.handle("记住测试环境地址B", FakeDS())
    handled, hint = mgr.handle("忘掉测试环境地址", FakeDS())
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
