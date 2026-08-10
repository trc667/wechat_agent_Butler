# -*- coding: utf-8 -*-
"""vision.py 单元测试：未配置 key 降级、请求构造、成功/失败路径、图片压缩。"""
import io

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


# ---------- 图片压缩 ----------

def _make_image(size, fmt="JPEG", mode="RGB"):
    from PIL import Image
    buf = io.BytesIO()
    Image.new(mode, size, (200, 100, 50)).save(buf, format=fmt)
    return buf.getvalue()


def test_prepare_small_image_unchanged():
    data = _make_image((100, 100))
    out, forced = vision._prepare_image(data)
    assert forced is None
    assert out == data


def test_prepare_oversize_scaled_down():
    data = _make_image((6000, 200))  # 超 4096 宽
    out, forced = vision._prepare_image(data)
    assert forced == "image/jpeg"
    from PIL import Image
    img = Image.open(io.BytesIO(out))
    assert max(img.size) <= vision._MAX_SIDE


def test_prepare_transparent_png_converted():
    # 超限的 RGBA PNG → 缩放 + 转 RGB JPEG
    data = _make_image((5000, 5000), fmt="PNG", mode="RGBA")
    out, forced = vision._prepare_image(data)
    assert forced == "image/jpeg"
    from PIL import Image
    img = Image.open(io.BytesIO(out))
    assert img.mode == "RGB"
    assert max(img.size) <= vision._MAX_SIDE


def test_prepare_oversize_bytes_compressed():
    # 超字节上限（max_bytes=1 强制）→ 重编码为 JPEG（纯色 PNG 转 JPEG 可能更大，不比较大小）
    data = _make_image((2000, 2000), fmt="PNG")
    out, forced = vision._prepare_image(data, max_bytes=1)
    assert forced == "image/jpeg"
    from PIL import Image
    Image.open(io.BytesIO(out)).load()  # 可正常解码


def test_prepare_invalid_bytes_unchanged():
    out, forced = vision._prepare_image(b"not-an-image")
    assert forced is None
    assert out == b"not-an-image"
