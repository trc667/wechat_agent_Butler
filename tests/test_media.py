# -*- coding: utf-8 -*-
"""media.py 单元测试：AES key 解析、解密回环、图片格式识别、CDN URL 构造、下载。"""
import base64

import pytest

import media

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    HAVE_AES = True
except ImportError:
    HAVE_AES = False


def _aes_encrypt(data, key):
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.encrypt(pad(data, AES.block_size))


# ---------- AES key 解析 ----------

def test_parse_key_hex():
    # 真实场景：image_item.aeskey 是 32 位 hex
    key = media._parse_aes_key("61f34dfe1930a13c1d5e24da4bda893f")
    assert key == bytes.fromhex("61f34dfe1930a13c1d5e24da4bda893f")


def test_parse_key_base64_raw():
    raw = b"0123456789abcdef"
    key = media._parse_aes_key(base64.b64encode(raw).decode("ascii"))
    assert key == raw


def test_parse_key_base64_hex():
    hex_str = "61f34dfe1930a13c1d5e24da4bda893f"
    key = media._parse_aes_key(base64.b64encode(hex_str.encode("ascii")).decode("ascii"))
    assert key == bytes.fromhex(hex_str)


def test_parse_key_invalid():
    assert media._parse_aes_key("") is None
    assert media._parse_aes_key(None) is None
    assert media._parse_aes_key("not-a-key!!") is None


# ---------- 解密回环 ----------

@pytest.mark.skipif(not HAVE_AES, reason="需要 pycryptodome")
def test_decrypt_media_with_hex_key():
    key_hex = "61f34dfe1930a13c1d5e24da4bda893f"
    plain = b"hello wechat image " * 5
    enc = _aes_encrypt(plain, bytes.fromhex(key_hex))
    assert media.decrypt_media(enc, key_hex) == plain


@pytest.mark.skipif(not HAVE_AES, reason="需要 pycryptodome")
def test_decrypt_media_with_bytes_key():
    raw = b"0123456789abcdef"
    plain = b"secret " * 8
    enc = _aes_encrypt(plain, raw)
    assert media.decrypt_media(enc, raw) == plain


@pytest.mark.skipif(not HAVE_AES, reason="需要 pycryptodome")
def test_decrypt_media_bad_key():
    with pytest.raises(ValueError):
        media.decrypt_media(b"x" * 16, "bad-key-value!!")


# ---------- URL 构造 ----------

def test_build_download_url():
    url = media._build_download_url("abc=def&ghi")
    assert url.startswith("https://novac2c.cdn.weixin.qq.com/c2c/download?encrypted_query_param=")
    assert "abc%3Ddef%26ghi" in url  # 参数被 URL 编码


# ---------- 图片格式识别 ----------

def test_guess_image_mime():
    jpeg = b"\xff\xd8\xff\xe0" + b"x" * 100
    png = b"\x89PNG\r\n\x1a\n" + b"y" * 100
    webp = b"RIFF" + b"0" * 4 + b"WEBP" + b"z" * 100
    gif = b"GIF89a" + b"w" * 100
    assert media.guess_image_mime(jpeg) == "image/jpeg"
    assert media.guess_image_mime(png) == "image/png"
    assert media.guess_image_mime(webp) == "image/webp"
    assert media.guess_image_mime(gif) == "image/gif"
    assert media.guess_image_mime(b"unknown") == "image/jpeg"


def test_guess_image_mime_empty():
    assert media.guess_image_mime(b"") == "image/jpeg"
    assert media.guess_image_mime(None) == "image/jpeg"


# ---------- 下载 ----------

def test_download_image_missing_fields():
    assert media.download_image(None) is None
    assert media.download_image({}) is None
    assert media.download_image({"media": {}}) is None  # 无 URL


def test_download_image_success_plain(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        @property
        def content(self):
            return b"fake-image-bytes"

    monkeypatch.setattr(media.requests, "get", lambda *a, **k: FakeResp())
    item = {"media": {"encrypt_query_param": "abc123"}}  # 无 aeskey：原样返回
    img = media.download_image(item)
    assert img == b"fake-image-bytes"


def test_download_image_decrypt(monkeypatch):
    if not HAVE_AES:
        return
    key_hex = "61f34dfe1930a13c1d5e24da4bda893f"
    plain = b"secret image content " * 3
    enc = _aes_encrypt(plain, bytes.fromhex(key_hex))

    class FakeResp:
        def raise_for_status(self):
            pass

        @property
        def content(self):
            return enc

    monkeypatch.setattr(media.requests, "get", lambda *a, **k: FakeResp())
    item = {"aeskey": key_hex, "media": {"encrypt_query_param": "xyz"}}
    img = media.download_image(item)
    assert img == plain


def test_download_image_full_url_preferred(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        @property
        def content(self):
            return b"direct-bytes"

    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        return FakeResp()

    monkeypatch.setattr(media.requests, "get", fake_get)
    item = {"media": {"full_url": "https://cdn.example.com/direct",
                      "encrypt_query_param": "ignored"}}
    img = media.download_image(item)
    assert img == b"direct-bytes"
    assert seen["url"] == "https://cdn.example.com/direct"


def test_download_image_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("网络错误")

    monkeypatch.setattr(media.requests, "get", boom)
    assert media.download_image({"media": {"encrypt_query_param": "x"}}) is None
