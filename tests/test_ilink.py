# -*- coding: utf-8 -*-
"""ilink.py get_updates 单元测试：游标异常（ret=-1）自动清空重试，防 bot 卡死。"""
import json

import pytest

import ilink
from ilink import ILinkClient, ILinkError


class FakePost:
    """模拟 _post：第一次调用返回 ret=-1，之后返回正常消息。"""

    def __init__(self, fail_first=True):
        self.fail_first = fail_first
        self.calls = 0

    def __call__(self, path, payload, timeout=60):
        self.calls += 1
        assert path == "/ilink/bot/getupdates"
        if self.fail_first and self.calls == 1:
            return {"ret": -1}
        return {"ret": 0, "get_updates_buf": "new-cursor",
                "msgs": [{"msg_id": "1", "message_type": 1,
                          "from_user_id": "u1", "context_token": "t1",
                          "item_list": [{"type": 1, "text_item": {"text": "hi"}}]}]}


def _make_client(tmp_path, fake=None):
    c = ILinkClient(cred_file=str(tmp_path / "cred.json"),
                    cursor_file=str(tmp_path / "cursor.txt"),
                    tokens_file=str(tmp_path / "tokens.json"))
    c.token = "test-token"
    if fake is not None:
        c._post = fake
    return c


def test_get_updates_ok(tmp_path):
    c = _make_client(tmp_path, FakePost(fail_first=False))
    msgs = c.get_updates()
    assert len(msgs) == 1
    assert c._load_cursor() == "new-cursor"


def test_ret_minus1_clears_cursor_and_retries(tmp_path, capsys):
    """游标损坏导致 ret=-1 时：清空游标重试一次并恢复，不抛异常。"""
    fake = FakePost(fail_first=True)
    c = _make_client(tmp_path, fake)
    # 预置一个"损坏"游标
    c._save_cursor("broken-cursor-104-chars-xxxxxxxxxxxxxxxxxxxxxxxx")
    msgs = c.get_updates()
    assert fake.calls == 2          # 重试了一次
    assert len(msgs) == 1           # 正常拿到消息
    assert c._load_cursor() == "new-cursor"
    out = capsys.readouterr().out
    assert "清空游标重试" in out


def test_ret_minus1_with_empty_cursor_raises(tmp_path):
    """游标本来就是空还返回 -1（如服务端异常）→ 抛异常交给上层处理。"""
    fake = FakePost(fail_first=True)
    c = _make_client(tmp_path, fake)
    with pytest.raises(ILinkError):
        c.get_updates()


def test_ret_minus14_raises_session_expired(tmp_path):
    class Fake14:
        def __call__(self, path, payload, timeout=60):
            return {"ret": -14}

    c = _make_client(tmp_path, Fake14())
    with pytest.raises(ILinkError, match="SESSION_EXPIRED"):
        c.get_updates()


# ---------- 发送图片（CDN 上传链路） ----------

def test_send_image_success(monkeypatch, tmp_path):
    """完整链路：getUploadUrl → AES 上传 → sendmessage 图片 item，成功返回 True。"""
    c = _make_client(tmp_path)
    c._context_tokens["u1"] = "t1"
    calls = []

    def fake_post(path, payload, timeout=60):
        calls.append(path)
        if path == "/ilink/bot/getuploadurl":
            assert payload["media_type"] == 1 and payload["no_need_thumb"] is True
            assert payload["aeskey"]
            return {"upload_full_url": "https://cdn/upload"}
        if path == "/ilink/bot/getconfig":
            return {"ret": 0, "typing_ticket": "ticket"}
        if path == "/ilink/bot/sendtyping":
            return {"ret": 0}
        if path == "/ilink/bot/sendmessage":
            item = payload["msg"]["item_list"][0]
            media = item.get("image_item") or {}
            if item["type"] == 2:  # 图片消息
                assert media["media"]["encrypt_query_param"] == "download-param"
                # aes_key 必须是 base64(hex 字符串)（44 字符），否则微信客户端解密失败显示图片已过期
                aes = media["media"]["aes_key"]
                assert len(aes) == 44
                assert len(ilink.base64.b64decode(aes).decode("ascii")) == 32
                assert media["media"]["encrypt_type"] == 1
            return {"ret": 0}
        return {}

    c._post = fake_post

    class FakeUploadResp:
        status_code = 200
        headers = {"x-encrypted-param": "download-param"}

    monkeypatch.setattr(ilink.requests, "post", lambda *a, **k: FakeUploadResp())
    ok = c.send_image("u1", b"fake-image-bytes" * 4, caption="今日单曲：测试")
    assert ok is True
    # 链路：先取上传地址，之后至少一次发送（caption 文本 + 图片各一条 sendmessage）
    assert calls[0] == "/ilink/bot/getuploadurl"
    assert calls.count("/ilink/bot/sendmessage") >= 2


def test_send_image_no_token(tmp_path):
    c = _make_client(tmp_path)
    assert c.send_image("u1", b"x" * 32) is False  # 没有会话令牌发不出去


def test_send_image_upload_failure(monkeypatch, tmp_path):
    """CDN 上传响应缺 x-encrypted-param → 失败。"""
    c = _make_client(tmp_path)
    c._context_tokens["u1"] = "t1"

    def fake_post(path, payload, timeout=60):
        if path == "/ilink/bot/getuploadurl":
            return {"upload_full_url": "https://cdn/upload"}
        return {}

    c._post = fake_post

    class BadResp:
        status_code = 200
        headers = {}

    monkeypatch.setattr(ilink.requests, "post", lambda *a, **k: BadResp())
    assert c.send_image("u1", b"x" * 32) is False
