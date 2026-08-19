# -*- coding: utf-8 -*-
"""music.py 单元测试：歌单 JSON 解析、按日期轮换、网络失败降级。"""
import music


SAMPLE = {"result": {"tracks": [
    {"name": "海屿你", "artists": [{"name": "马也_Crabbit"}], "id": 1973665667},
    {"name": "失眠", "artists": [{"name": "Suki刘舒妤"}, {"name": "伴唱"}], "id": 273114},
    {"name": "无歌手", "artists": [], "id": 999},
]}}


def test_parse_song_first():
    text = music.parse_song(SAMPLE, 0)
    assert text is not None
    assert "海屿你" in text and "马也_Crabbit" in text
    assert "https://music.163.com/song?id=1973665667" in text


def test_parse_song_multi_artists():
    text = music.parse_song(SAMPLE, 1)
    assert "Suki刘舒妤" in text and "伴唱" in text


def test_parse_song_no_artist():
    text = music.parse_song(SAMPLE, 2)
    assert text is not None and "无歌手" in text
    assert " - " not in text  # 无歌手不加分隔符


def test_parse_song_index_out_of_range():
    # 越界自动取模回绕：5 % 3 = 2 → 取第三首「无歌手」
    text = music.parse_song(SAMPLE, 5)
    assert text is not None and "无歌手" in text


def test_parse_song_bad_data():
    assert music.parse_song({}, 0) is None
    assert music.parse_song({"result": {"tracks": []}}, 0) is None
    assert music.parse_song(None, 0) is None


def test_fetch_success(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return SAMPLE

    monkeypatch.setattr(music.requests, "get", lambda *a, **k: FakeResp())
    text = music.fetch_daily_song()
    assert text is not None and "今日单曲" in text


def test_fetch_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("网络错误")

    monkeypatch.setattr(music.requests, "get", boom)
    assert music.fetch_daily_song() is None
