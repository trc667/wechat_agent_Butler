# -*- coding: utf-8 -*-
"""music.py 单元测试：歌单 JSON 解析、按日期轮换、网络失败降级。"""
import music


SAMPLE = {"result": {"tracks": [
    {"name": "海屿你", "artists": [{"name": "马也_Crabbit"}], "id": 1973665667,
     "album": {"picUrl": "http://cover/1.jpg"}},
    {"name": "失眠", "artists": [{"name": "Suki刘舒妤"}, {"name": "伴唱"}], "id": 273114,
     "album": {"picUrl": "http://cover/2.jpg"}},
    {"name": "无歌手", "artists": [], "id": 999,
     "album": {"picUrl": "http://cover/3.jpg"}},
]}}


def _text(data, idx=0):
    parsed = music.parse_song(data, idx)
    return parsed[0] if parsed else None


def test_parse_song_first():
    text, cover = music.parse_song(SAMPLE, 0)
    assert text is not None
    assert "海屿你" in text and "马也_Crabbit" in text
    assert "https://music.163.com/song?id=1973665667" in text
    assert cover == "http://cover/1.jpg"


def test_parse_song_multi_artists():
    text = _text(SAMPLE, 1)
    assert "Suki刘舒妤" in text and "伴唱" in text


def test_parse_song_no_artist():
    text = _text(SAMPLE, 2)
    assert text is not None and "无歌手" in text
    assert " - " not in text  # 无歌手不加分隔符


def test_parse_song_index_out_of_range():
    # 越界自动取模回绕：5 % 3 = 2 → 取第三首「无歌手」
    text = _text(SAMPLE, 5)
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


def test_fetch_full_includes_image(monkeypatch):
    class CoverResp:
        status_code = 200

        def raise_for_status(self):
            pass

        @property
        def content(self):
            return b"fake-cover-bytes"

    class ListResp:
        def raise_for_status(self):
            pass

        def json(self):
            return SAMPLE

    def fake_get(url, *a, **k):
        return ListResp() if "playlist" in url else CoverResp()

    monkeypatch.setattr(music.requests, "get", fake_get)
    full = music.fetch_daily_song_full()
    assert full is not None and full["text"]
    assert full["image"] == b"fake-cover-bytes"
    assert full["qr"] is not None and full["qr"][:8] == b"\x89PNG\r\n\x1a\n"  # 二维码 PNG


def test_make_qrcode():
    qr = music.make_qrcode("https://music.163.com/song?id=1")
    assert qr is not None and qr[:8] == b"\x89PNG\r\n\x1a\n"
    assert music.make_qrcode("") is None
    assert music.make_qrcode(None) is None


def test_fetch_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("网络错误")

    monkeypatch.setattr(music.requests, "get", boom)
    assert music.fetch_daily_song() is None
    assert music.fetch_daily_song_full() is None
