# -*- coding: utf-8 -*-
"""tools.py 单元测试：工具分发、备忘/待办/定时、天气/新闻 mock、记账。"""
import json
import os

import tools
from manager import LifeManager


def _make_ctx(tmp_path, cfg=None):
    mgr = LifeManager(os.path.join(str(tmp_path), "manager.json"))
    return tools.build_ctx(mgr, cfg or {"weather_city": "深圳"}), mgr


# ---------- 备忘 / 待办 / 定时 ----------

def test_memo_add_and_list(tmp_path):
    ctx, mgr = _make_ctx(tmp_path)
    assert "已存进备忘录" in tools.dispatch("memo_add", {"text": "测试环境地址 http://x"}, ctx)
    out = tools.dispatch("memo_list", {}, ctx)
    assert "测试环境地址" in out
    # 相同内容去重
    assert "已经记过" in tools.dispatch("memo_add", {"text": "测试环境地址 http://x"}, ctx)


def test_memo_delete(tmp_path):
    ctx, _ = _make_ctx(tmp_path)
    tools.dispatch("memo_add", {"text": "测试环境地址 http://x"}, ctx)
    assert "已删掉" in tools.dispatch("memo_delete", {"keyword": "测试环境"}, ctx)


def test_todo_add_list_done(tmp_path):
    ctx, mgr = _make_ctx(tmp_path)
    out = tools.dispatch("todo_add", {"text": "交房租", "due": "2026-08-20"}, ctx)
    assert "交房租" in out
    out = tools.dispatch("todo_list", {}, ctx)
    assert "交房租" in out
    tools.dispatch("todo_done", {"keyword": "交房租"}, ctx)
    out = tools.dispatch("todo_list", {}, ctx)
    assert "交房租" not in out


def test_timer_add_list_cancel(tmp_path):
    ctx, mgr = _make_ctx(tmp_path)
    out = tools.dispatch("timer_add", {"at": "2026-08-20 15:00", "text": "开会"}, ctx)
    assert "15:00" in out
    out = tools.dispatch("timer_list", {}, ctx)
    assert "开会" in out
    out = tools.dispatch("timer_cancel", {"keyword": "开会"}, ctx)
    assert "取消" in out


def test_timer_add_bad_time(tmp_path):
    ctx, _ = _make_ctx(tmp_path)
    out = tools.dispatch("timer_add", {"at": "乱写", "text": "开会"}, ctx)
    assert "时间格式" in out


# ---------- 天气 / 新闻（mock 网络） ----------

def test_weather_query_today(monkeypatch, tmp_path):
    ctx, _ = _make_ctx(tmp_path)
    monkeypatch.setattr("weather.fetch_weather", lambda city="北京": "%s 当前 29 度" % city)
    out = tools.dispatch("weather_query", {"city": "深圳"}, ctx)
    assert "深圳 当前 29 度" in out


def test_weather_query_default_city(monkeypatch, tmp_path):
    ctx, _ = _make_ctx(tmp_path)  # cfg weather_city=深圳
    monkeypatch.setattr("weather.fetch_weather", lambda city="北京": "%s 当前 29 度" % city)
    out = tools.dispatch("weather_query", {}, ctx)
    assert "深圳" in out


def test_news_query_today(monkeypatch, tmp_path):
    ctx, _ = _make_ctx(tmp_path)
    monkeypatch.setattr("news.fetch_news", lambda max_items=5: "今日科技/AI 新闻：\nDeepSeek 发布新模型")
    out = tools.dispatch("news_query", {}, ctx)
    assert "DeepSeek" in out


def test_news_query_history_day(monkeypatch, tmp_path):
    ctx, _ = _make_ctx(tmp_path)
    monkeypatch.setattr("news.load_history", lambda: {"2026-08-14": "周五新闻存档"})
    out = tools.dispatch("news_query", {"day": "周五"}, ctx)
    assert "周五新闻存档" in out


# ---------- 记账（演示新工具） ----------

def test_expense_add_categorizes(monkeypatch, tmp_path):
    ctx, _ = _make_ctx(tmp_path)
    monkeypatch.setattr(tools, "EXPENSES_PATH",
                        os.path.join(str(tmp_path), "expenses.json"))
    out = tools.dispatch("expense_add", {"amount": 35, "note": "中午吃饭"}, ctx)
    assert "吃饭" in out and "35" in out


def test_expense_summary_month(monkeypatch, tmp_path):
    ctx, _ = _make_ctx(tmp_path)
    monkeypatch.setattr(tools, "EXPENSES_PATH",
                        os.path.join(str(tmp_path), "expenses.json"))
    tools.dispatch("expense_add", {"amount": 35, "note": "中午吃饭"}, ctx)
    tools.dispatch("expense_add", {"amount": 20, "note": "打车"}, ctx)
    out = tools.dispatch("expense_summary", {}, ctx)
    assert "合计 55.00" in out
    assert "吃饭" in out and "交通" in out


def test_expense_summary_empty(monkeypatch, tmp_path):
    ctx, _ = _make_ctx(tmp_path)
    monkeypatch.setattr(tools, "EXPENSES_PATH",
                        os.path.join(str(tmp_path), "expenses.json"))
    out = tools.dispatch("expense_summary", {}, ctx)
    assert "还没有记账" in out


def test_expense_add_bad_amount(monkeypatch, tmp_path):
    ctx, _ = _make_ctx(tmp_path)
    monkeypatch.setattr(tools, "EXPENSES_PATH",
                        os.path.join(str(tmp_path), "expenses.json"))
    assert "金额" in tools.dispatch("expense_add", {"amount": "abc", "note": "x"}, ctx)


# ---------- 兜底 ----------

def test_dispatch_unknown_tool(tmp_path):
    ctx, _ = _make_ctx(tmp_path)
    assert "未知工具" in tools.dispatch("no_such_tool", {}, ctx)


def test_dispatch_handler_exception(tmp_path):
    ctx, _ = _make_ctx(tmp_path)
    out = tools.dispatch("memo_add", {"text": 123}, ctx)  # int 触发 strip 异常
    assert "执行失败" in out


def test_tools_schema_valid():
    """工具 schema 符合 OpenAI 格式：required 不在 properties 内。"""
    for t in tools.TOOLS:
        params = t["function"]["parameters"]
        assert "type" in params and params["type"] == "object"
        assert "properties" in params
        for k, v in params["properties"].items():
            assert "required" not in v  # required 必须在顶层
        assert all(r in params["properties"] for r in params.get("required", []))
