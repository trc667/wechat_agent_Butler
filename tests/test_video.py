# -*- coding: utf-8 -*-
"""video.py 单元测试：链接提取、下载、音频提取、ASR 转写、摘要、主流程。"""
import os

import video


# ---------- 链接提取 ----------

def test_extract_url_plain():
    assert video.extract_url("https://v.douyin.com/iAbCdEf/") == \
        "https://v.douyin.com/iAbCdEf/"


def test_extract_url_from_share_text():
    text = ("8.88 复制打开抖音，看看【AI科普】的视频 "
            "https://v.douyin.com/iAbCdEf/ 复制此链接，打开Dou音搜索")
    assert video.extract_url(text) == "https://v.douyin.com/iAbCdEf/"


def test_extract_url_web():
    assert video.extract_url("https://www.douyin.com/video/123456") == \
        "https://www.douyin.com/video/123456"


def test_extract_url_no_link():
    assert video.extract_url("今天天气不错") is None
    assert video.extract_url("") is None
    assert video.extract_url(None) is None


def test_extract_url_picks_douyin_not_other():
    text = "看这个 https://example.com/x https://v.douyin.com/abc/"
    assert video.extract_url(text) == "https://v.douyin.com/abc/"


# ---------- 下载 ----------

def test_download_video_playwright_first(monkeypatch, tmp_path):
    """优先 Playwright 下载（抖音需要浏览器签名）。"""
    target = tmp_path / "pw.mp4"
    target.write_bytes(b"fake-video")
    monkeypatch.setattr("douyin_dl.download_douyin_video",
                        lambda url, out_dir: str(target))
    assert video.download_video("https://v.douyin.com/abc/",
                                out_dir=str(tmp_path)) == str(target)


def test_download_video_fallback_ytdlp(monkeypatch, tmp_path):
    """Playwright 失败 → 回退 yt-dlp。"""
    target = tmp_path / "yt.mp4"
    target.write_bytes(b"fake-video")
    monkeypatch.setattr("douyin_dl.download_douyin_video",
                        lambda url, out_dir: None)

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            return {"id": "vid"}

        def prepare_filename(self, info):
            return str(target)

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
    assert video.download_video("https://example.com/v",
                                out_dir=str(tmp_path)) == str(target)


def test_download_video_all_fail(monkeypatch, tmp_path):
    """两条路都失败 → None。"""
    monkeypatch.setattr("douyin_dl.download_douyin_video",
                        lambda url, out_dir: None)
    monkeypatch.setattr("yt_dlp.YoutubeDL",
                        lambda opts: (_ for _ in ()).throw(RuntimeError("反爬")))
    assert video.download_video("https://v.douyin.com/abc/",
                                out_dir=str(tmp_path)) is None


# ---------- 音频提取 ----------

def test_extract_audio_success(monkeypatch, tmp_path):
    monkeypatch.setattr(video.os, "getpid", lambda: 999)
    wav = tmp_path / "audio_999.wav"

    def fake_run(cmd, capture_output=True, timeout=180):
        wav.write_bytes(b"RIFF")
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(video.subprocess, "run", fake_run)
    out = video.extract_audio("in.mp4", out_dir=str(tmp_path))
    assert out == str(wav)


def test_extract_audio_failure(monkeypatch, tmp_path):
    def fake_run(cmd, capture_output=True, timeout=180):
        return type("R", (), {"returncode": 1})()

    monkeypatch.setattr(video.subprocess, "run", fake_run)
    assert video.extract_audio("in.mp4", out_dir=str(tmp_path)) is None


# ---------- ASR 转写 ----------

def _asr_resp(sentence_text=None, choices_text=None):
    out = {"output": {}}
    if sentence_text is not None:
        out["output"]["output"] = {"sentence": {"text": sentence_text}}
    if choices_text is not None:
        out["output"]["choices"] = [{"message": {"content": choices_text}}]
    return out


def test_transcribe_sentence_structure(monkeypatch, tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"\x00\x01")

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return _asr_resp(sentence_text="这是科普视频内容")

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=60, proxies=None):
        captured["body"] = json
        captured["headers"] = headers
        return FakeResp()

    monkeypatch.setattr(video.requests, "post", fake_post)
    text = video.transcribe(str(wav), "sk-test")
    assert text == "这是科普视频内容"
    assert captured["body"]["model"] == "qwen-audio-3.0-asr-flash"
    audio = captured["body"]["input"]["messages"][0]["content"][0]
    assert audio["type"] == "input_audio"
    assert audio["input_audio"]["data"].startswith("data:audio/wav;base64,")
    assert "Bearer sk-test" in captured["headers"]["Authorization"]


def test_transcribe_choices_fallback(monkeypatch, tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"\x00\x01")

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return _asr_resp(choices_text="兜底结构识别文本")

    monkeypatch.setattr(video.requests, "post",
                        lambda *a, **k: FakeResp())
    assert video.transcribe(str(wav), "sk-test") == "兜底结构识别文本"


def test_transcribe_failure(monkeypatch, tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"\x00\x01")

    def boom(*a, **k):
        raise OSError("网络错误")

    monkeypatch.setattr(video.requests, "post", boom)
    assert video.transcribe(str(wav), "sk-test") is None


# ---------- 摘要 ----------

def test_summarize(monkeypatch):
    class FakeDS:
        def __init__(self):
            self.seen = None

        def chat(self, messages, **kw):
            self.seen = messages
            return "一句话概括：讲了视频内容\n要点：\n1. 第一点"

    ds = FakeDS()
    out = video.summarize("这是很长的转写文本内容", ds)
    assert out and "一句话概括" in out
    assert "这是很长的转写文本内容" in ds.seen[0]["content"]


def test_summarize_empty_text():
    class DS:
        def chat(self, messages, **kw):
            return "x"

    assert video.summarize("", DS()) is None
    assert video.summarize("   ", DS()) is None
