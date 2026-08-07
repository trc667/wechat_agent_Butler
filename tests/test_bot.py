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
