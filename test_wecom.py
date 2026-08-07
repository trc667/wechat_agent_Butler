"""企微接入自测：加解密算法 + URL 验证 + 明文/安全模式消息回调全链路。

运行：python test_wecom.py   （不连网、不需要任何凭据）
"""

import urllib.parse
import urllib.request

from wecom import start_callback_server
from wecom_crypto import WeComCrypto, WeComCryptoError

TOKEN = "xiaoqi_test_token"
AES_KEY = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"  # 43位
CORPID = "ww1234567890"

INNER_XML = ("<xml><ToUserName><![CDATA[ww123]]></ToUserName>"
             "<FromUserName><![CDATA[WangXiaoMing]]></FromUserName>"
             "<CreateTime>1700000000</CreateTime>"
             "<MsgType><![CDATA[text]]></MsgType>"
             "<Content><![CDATA[你好呀小淇]]></Content>"
             "<MsgId>10001</MsgId><AgentID><![CDATA[1000002]]></AgentID></xml>")

TS, NONCE = "1700000000", "123456"
PASSED = []


def ok(name):
    PASSED.append(name)
    print("  [OK] %s" % name)


class FakeBot:
    def __init__(self):
        self.calls = []

    def on_text(self, sender, content):
        self.calls.append((sender, content))


def http_get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.read().decode("utf-8")


def http_post(url, body):
    req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.read().decode("utf-8")


def test_crypto():
    print("[1] 加解密算法")
    c = WeComCrypto(TOKEN, AES_KEY, CORPID)
    # 加密 -> 解密 roundtrip
    enc = c.encrypt(INNER_XML)
    assert c.decrypt(enc) == INNER_XML
    ok("encrypt/decrypt roundtrip")
    # 签名
    sig = c.signature(TS, NONCE, enc)
    assert c.signature(TS, NONCE, enc) == sig
    ok("签名稳定")
    # 篡改检测：换个签名必须抛错
    try:
        c.verify(TS, NONCE, enc, "deadbeef" * 10)
        raise AssertionError("篡改签名未被拦截")
    except WeComCryptoError:
        ok("篡改签名被拦截")
    # 错误 corpid 必须解密失败（receiveid 校验）
    c2 = WeComCrypto(TOKEN, AES_KEY, "other_corpid")
    try:
        c2.decrypt(enc)
        raise AssertionError("corpid 不匹配未被拦截")
    except WeComCryptoError:
        ok("corpid 不匹配被拦截")


def run_server(mode):
    bot = FakeBot()
    crypto = WeComCrypto(TOKEN, AES_KEY, CORPID)
    bundle = {"crypto": crypto, "bot": bot, "callback_path": "/wechat/callback", "mode": mode}
    httpd = start_callback_server(bundle, 0, "/wechat/callback")
    port = httpd.server_address[1]
    base = "http://127.0.0.1:%d/wechat/callback" % port
    return bot, crypto, base


def test_callback_plain():
    print("[2] 明文模式回调")
    bot, crypto, base = run_server("plain")
    # URL 验证（GET）
    sig = crypto.signature(TS, NONCE)
    code, body = http_get("%s?timestamp=%s&nonce=%s&msg_signature=%s&echostr=hello"
                          % (base, TS, NONCE, sig))
    assert (code, body) == (200, "hello")
    ok("URL 验证（明文）返回 echostr")
    # 消息推送（POST 明文 XML）
    code, body = http_post(base + "?timestamp=%s&nonce=%s" % (TS, NONCE), INNER_XML)
    assert (code, body) == (200, "success")
    assert bot.calls == [("WangXiaoMing", "你好呀小淇")]
    ok("消息回调解析正确（明文）")


def test_callback_safe():
    print("[3] 安全模式回调")
    bot, crypto, base = run_server("safe")
    # URL 验证（GET）：echostr 是密文，需返回解密后的明文
    echostr = crypto.encrypt("url_verify_ok")
    sig = crypto.signature(TS, NONCE, echostr)
    code, body = http_get("%s?timestamp=%s&nonce=%s&msg_signature=%s&echostr=%s"
                          % (base, TS, NONCE, sig, urllib.parse.quote(echostr)))
    assert (code, body) == (200, "url_verify_ok")
    ok("URL 验证（安全）解密返回 echostr")
    # 消息推送（POST 外层密文）
    enc = crypto.encrypt(INNER_XML)
    outer = ("<xml><ToUserName><![CDATA[ww123]]></ToUserName>"
             "<Encrypt><![CDATA[%s]]></Encrypt>"
             "<AgentID><![CDATA[1000002]]></AgentID></xml>" % enc)
    sig = crypto.signature(TS, NONCE, enc)
    code, body = http_post("%s?timestamp=%s&nonce=%s&msg_signature=%s"
                           % (base, TS, NONCE, sig), outer)
    assert (code, body) == (200, "success")
    assert bot.calls == [("WangXiaoMing", "你好呀小淇")]
    ok("消息回调解析正确（安全）")
    # 篡改签名必须被拒
    code, _ = http_post("%s?timestamp=%s&nonce=%s&msg_signature=bad" % (base, TS, NONCE), outer)
    assert code == 200 and bot.calls == [("WangXiaoMing", "你好呀小淇")]  # 丢弃消息，不报错
    ok("篡改签名的消息被丢弃")


if __name__ == "__main__":
    test_crypto()
    test_callback_plain()
    test_callback_safe()
    print("\n全部通过：%d 项" % len(PASSED))
