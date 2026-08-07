# -*- coding: utf-8 -*-
"""vision.py 单元测试：未配置 key 降级、请求构造、成功/失败路径。"""
import vision


def _cfg(**kw):
    base = {"dashscope_api_key": "sk-test-key",
            "dashscope_model": "qwen-vl-plus",
            "dashscope_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"}
    base.update(kw)
    return base


def test_no_api_key_returns_none(monkeypatch):
    monkeypatch.setattr(vision, "load_config",
                        lambda: _cfg(dashscope_api_key=""))
    assert vision.describe_image(b"fake") is None


def test_empty_bytes_returns_none(monkeypatch):
    monkeypatch.setattr(vision, "load_config", lambda: _cfg())
    assert vision.describe_image(b"") is None


def test_describe_image_success(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "这是一只猫。"}}]}

    captured = {}

    def fake_post(url, headers=None, json=None, **kw):
        captured["url"] = url
        captured["body"] = json
        return FakeResp()

    monkeypatch.setattr(vision, "load_config", lambda: _cfg())
    monkeypatch.setattr(vision.requests, "post", fake_post)
    text = vision.describe_image(b"\xff\xd8\xff" + b"x" * 50)
    assert text == "这是一只猫。"
    assert captured["url"].endswith("/chat/completions")
    assert captured["body"]["model"] == "qwen-vl-plus"
    content = captured["body"]["messages"][0]["content"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_describe_image_failure(monkeypatch):
    def boom(*a, **kw):
        raise OSError("网络错误")

    monkeypatch.setattr(vision, "load_config", lambda: _cfg())
    monkeypatch.setattr(vision.requests, "post", boom)
    assert vision.describe_image(b"\xff\xd8\xff" + b"y" * 50) is None
