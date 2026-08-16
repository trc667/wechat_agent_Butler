# -*- coding: utf-8 -*-
"""bot.py 单元测试：emoji/符号/波浪号物理过滤、多余空格整理。"""
from bot import strip_emoji


def test_strip_basic_emoji():
    assert strip_emoji("你好😊世界") == "你好世界"


def test_strip_wave_dashes():
    assert strip_emoji("好的～收到~") == "好的收到"


def test_strip_symbols_with_variation_selector():
    # ☀️ = 杂项符号 + 变体选择符
    assert strip_emoji("温度☀️很高") == "温度很高"


def test_strip_heart_with_zwj():
    # ❤️ + 零宽连接符
    assert strip_emoji("爱❤️你") == "爱你"


def test_strip_multi_emoji():
    assert strip_emoji("哈哈😂🤣😄") == "哈哈"


def test_collapse_multi_spaces():
    # 2+ 连续空格压成 1 个（设计行为：整理多余空格）
    assert strip_emoji("我  是  管家") == "我 是 管家"


def test_strip_empty_and_none():
    assert strip_emoji("") == ""
    assert strip_emoji(None) == ""


def test_keep_plain_chinese_and_punct():
    text = "先说结论：去 c:\\temp\\run.py 看下。"
    assert strip_emoji(text) == text


class FakeMem:
    """假记忆：只测回复调度，不碰真实数据。"""

    def text(self):
        return ""

    def recent_history(self, n):
        return []

    def append_history(self, role, content):
        pass


def test_weather_reply_direct_no_model(monkeypatch):
    """问天气必须直接回天气数据：不走 DeepSeek，不夹带备忘录。"""
    from bot import XiaoQiBot

    sent = []
    monkeypatch.setattr("weather.fetch_weather",
                        lambda city="北京": "北京 当前 29 度（晴，体感 30 度）")
    b = XiaoQiBot(None, FakeMem(), {"min_reply_interval": 0},
                  send=lambda s, t: sent.append((s, t)))
    b._reply_worker("wxid_demo", "看看今天天气怎么样")
    assert len(sent) == 1  # 只回一条
    assert "晴" in sent[0][1]


def test_weather_route_not_triggered_without_keyword(monkeypatch):
    from bot import XiaoQiBot

    sent = []
    b = XiaoQiBot(None, FakeMem(), {"min_reply_interval": 0},
                  send=lambda s, t: sent.append((s, t)))
    handled, _ = b._try_weather("你好呀")
    assert handled is False


def test_news_query_friday(monkeypatch):
    """说「看看周五的新闻」→ 返回最近周五的存档。"""
    from datetime import datetime, timedelta
    import bot as bot_mod

    class FakeDT:
        @staticmethod
        def now():
            return datetime(2026, 8, 16, 10, 0)  # 周日

        @staticmethod
        def timedelta(days=0):
            return timedelta(days=days)

    monkeypatch.setattr(bot_mod, "datetime", FakeDT)
    monkeypatch.setattr("news.load_history",
                        lambda: {"2026-08-14": "周五新闻存档内容"})
    monkeypatch.setattr("news.fetch_news", lambda max_items=5: "今日新闻")
    b = bot_mod.XiaoQiBot(None, FakeMem(), {"min_reply_interval": 0},
                          send=lambda s, t: None)
    handled, hint = b._try_news_query("看看周五的新闻")
    assert handled is True
    assert "周五新闻存档内容" in hint


def test_news_query_not_triggered(monkeypatch):
    from bot import XiaoQiBot

    b = XiaoQiBot(None, FakeMem(), {"min_reply_interval": 0},
                  send=lambda s, t: None)
    handled, _ = b._try_news_query("你好呀")
    assert handled is False


def test_image_worker_uses_context(monkeypatch):
    """识图 prompt 应带上最近对话/记忆/备忘录（与已有能力联动）。"""
    from bot import XiaoQiBot

    class FakeMemCtx:
        def text(self):
            return "用户是程序员"

        def recent_history(self, n):
            return [{"role": "user", "content": "这是测试环境地址"}]

        def append_history(self, role, content):
            pass

    sent = []
    captured = {}

    def fake_describe(image_bytes, prompt=None):
        captured["prompt"] = prompt
        return "这是一张截图。"

    monkeypatch.setattr("vision.describe_image", fake_describe)
    b = XiaoQiBot(None, FakeMemCtx(),
                  {"min_reply_interval": 0, "dashscope_api_key": "sk-x"},
                  send=lambda s, t: sent.append((s, t)))
    b._image_worker("wxid", b"\xff\xd8\xff" + b"x" * 50)
    assert len(sent) == 1 and "截图" in sent[0][1]
    assert "最近对话" in captured["prompt"]
    assert "用户是程序员" in captured["prompt"]
    assert "备忘录" in captured["prompt"]


def test_image_todo_requires_confirm(monkeypatch, tmp_path):
    """清单照片：先问确认，用户回「记下」才入库（防误判）。"""
    import os
    from bot import XiaoQiBot
    from manager import LifeManager

    class FakeMem:
        def text(self):
            return ""

        def recent_history(self, n):
            return []

        def append_history(self, role, content):
            pass

    def fake_describe(image_bytes, prompt=None):
        return '{"type": "todo", "items": [{"text": "交房租", "due": "2026-08-20"}]}'

    monkeypatch.setattr("vision.describe_image", fake_describe)
    sent = []
    mgr = LifeManager(os.path.join(str(tmp_path), "manager.json"))
    b = XiaoQiBot(None, FakeMem(),
                  {"min_reply_interval": 0, "dashscope_api_key": "sk-x"},
                  send=lambda s, t: sent.append((s, t)))
    b.mgr = mgr
    b._image_worker("wxid", b"\xff\xd8\xff" + b"x" * 50)
    # 识别到但没入库，先问确认
    assert len(mgr.data["todos"]) == 0
    assert "记下" in sent[-1][1]
    assert b._pending_image is not None
    # 用户回复「记下」→ 入库
    b._reply_worker("wxid", "记下")
    assert len(mgr.data["todos"]) == 1
    assert mgr.data["todos"][0]["text"] == "交房租"
    assert mgr.data["todos"][0]["due"] == "2026-08-20"
    assert b._pending_image is None


def test_image_todo_cancel(monkeypatch, tmp_path):
    """用户说「不用」→ 不入库。"""
    import os
    from bot import XiaoQiBot
    from manager import LifeManager

    class FakeMem:
        def text(self):
            return ""

        def recent_history(self, n):
            return []

        def append_history(self, role, content):
            pass

    def fake_describe(image_bytes, prompt=None):
        return '{"type": "todo", "items": [{"text": "交房租"}]}'

    monkeypatch.setattr("vision.describe_image", fake_describe)
    sent = []
    mgr = LifeManager(os.path.join(str(tmp_path), "manager.json"))
    b = XiaoQiBot(None, FakeMem(),
                  {"min_reply_interval": 0, "dashscope_api_key": "sk-x"},
                  send=lambda s, t: sent.append((s, t)))
    b.mgr = mgr
    b._image_worker("wxid", b"\xff\xd8\xff" + b"x" * 50)
    b._reply_worker("wxid", "不用了")
    assert len(mgr.data["todos"]) == 0
    assert b._pending_image is None


def test_image_memo_confirm_dedup(monkeypatch, tmp_path):
    """备忘照片确认后入库且去重。"""
    import os
    from bot import XiaoQiBot
    from manager import LifeManager

    class FakeMem:
        def text(self):
            return ""

        def recent_history(self, n):
            return []

        def append_history(self, role, content):
            pass

    def fake_describe(image_bytes, prompt=None):
        return '{"type": "memo", "items": [{"text": "测试环境地址 http://x"}, {"text": "已存在"}]}'

    monkeypatch.setattr("vision.describe_image", fake_describe)
    sent = []
    mgr = LifeManager(os.path.join(str(tmp_path), "manager.json"))
    mgr.data["memos"] = [{"text": "已存在", "ts": 1}]
    b = XiaoQiBot(None, FakeMem(),
                  {"min_reply_interval": 0, "dashscope_api_key": "sk-x"},
                  send=lambda s, t: sent.append((s, t)))
    b.mgr = mgr
    b._image_worker("wxid", b"\xff\xd8\xff" + b"x" * 50)
    assert len(mgr.data["memos"]) == 1  # 未确认前不入库
    b._reply_worker("wxid", "记下")
    assert len(mgr.data["memos"]) == 2  # 已存在的没重复加
    assert "http://x" in mgr.data["memos"][1]["text"]


def test_image_none_falls_back_to_describe(monkeypatch):
    """普通图片（非清单）→ 走普通描述流程，不打扰。"""
    from bot import XiaoQiBot

    class FakeMem:
        def text(self):
            return ""

        def recent_history(self, n):
            return []

        def append_history(self, role, content):
            pass

    calls = []

    def fake_describe(image_bytes, prompt=None):
        calls.append(prompt)
        # 第一次结构化请求返回 none，第二次（描述）返回描述
        return '{"type": "none"}' if len(calls) == 1 else "这是一张风景照。"

    monkeypatch.setattr("vision.describe_image", fake_describe)
    sent = []
    b = XiaoQiBot(None, FakeMem(),
                  {"min_reply_interval": 0, "dashscope_api_key": "sk-x"},
                  send=lambda s, t: sent.append((s, t)))
    b._image_worker("wxid", b"\xff\xd8\xff" + b"x" * 50)
    assert len(sent) == 1 and "风景照" in sent[0][1]
    assert len(calls) == 2  # 结构化 + 描述各一次
    assert b._pending_image is None  # 普通图片不会产生待确认
