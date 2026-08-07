"""企业微信（WeCom）接入：access_token 管理、主动发消息、回调服务器。

消息流程：
  用户在企微 App 给应用发消息 -> 企微服务器 POST 回调到我们的公网 URL
  -> 内网穿透转发到本地 callback_port -> 本模块解析 -> bot.on_text() 排队回复
  -> 回复用「主动发送」接口 push 给用户（不走 5 秒被动回复时限）。
"""

import http.server
import logging
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET

import requests

from wecom_crypto import WeComCrypto, WeComCryptoError

log = logging.getLogger("wecom")


class WeComError(Exception):
    pass


class WeComClient:
    """企微开放接口客户端：token 缓存 + 主动发消息。"""

    def __init__(self, corpid, agentid, secret, api_base="https://qyapi.weixin.qq.com/cgi-bin"):
        self.corpid = corpid
        self.agentid = agentid
        self.secret = secret
        self.api_base = api_base
        self._token = None
        self._token_expire = 0.0
        self._lock = threading.Lock()

    def access_token(self):
        """获取 access_token（缓存到过期前 60 秒，线程安全）。"""
        with self._lock:
            if self._token and time.time() < self._token_expire - 60:
                return self._token
            resp = requests.get(self.api_base + "/gettoken", timeout=30, params={
                "corpid": self.corpid, "corpsecret": self.secret}).json()
            if resp.get("errcode") != 0:
                raise WeComError("获取 access_token 失败: %s" % resp)
            self._token = resp["access_token"]
            self._token_expire = time.time() + resp["expires_in"]
            return self._token

    def send_text(self, touser, content):
        """主动给成员发文本消息。"""
        token = self.access_token()
        body = {
            "touser": touser,
            "msgtype": "text",
            "agentid": self.agentid,
            "text": {"content": content},
        }
        resp = requests.post(self.api_base + "/message/send", timeout=30,
                             params={"access_token": token}, json=body).json()
        if resp.get("errcode") != 0:
            raise WeComError("发送消息失败: %s" % resp)
        return resp


# ---------- 回调服务器 ----------

class WeComCallbackHandler(http.server.BaseHTTPRequestHandler):
    """企微回调：GET = 配置时的 URL 验证；POST = 消息推送。

    bundle 字段在 start_callback_server() 里注入：
    {crypto, bot, callback_path, mode}，mode 为 "safe" 或 "plain"。
    """

    bundle = None

    def log_message(self, fmt, *args):
        log.info("callback %s" % (fmt % args))

    def _respond(self, code, body=b""):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _query(self):
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    def _check_path(self):
        if urllib.parse.urlparse(self.path).path != self.bundle["callback_path"]:
            self._respond(404)
            return False
        return True

    # ---------- URL 验证（后台配置回调时调用） ----------

    def do_GET(self):
        if not self._check_path():
            return
        q = self._query()
        timestamp, nonce, echostr = (q.get(k, [""])[0] for k in ("timestamp", "nonce", "echostr"))
        msg_signature = q.get("msg_signature", [""])[0]
        crypto = self.bundle["crypto"]

        if self.bundle["mode"] == "plain":
            # 明文模式：校验 token 签名后原样返回 echostr
            if msg_signature != crypto.signature(timestamp, nonce):
                log.warning("URL 验证签名不匹配")
                return self._respond(403)
            return self._respond(200, echostr.encode("utf-8"))

        # 安全模式：签名校验 + 解密后返回明文 echostr
        try:
            crypto.verify(timestamp, nonce, echostr, msg_signature)
            plain = crypto.decrypt(echostr)
        except WeComCryptoError as e:
            log.warning("URL 验证失败: %s" % e)
            return self._respond(403)
        return self._respond(200, plain.encode("utf-8"))

    # ---------- 消息推送 ----------

    def do_POST(self):
        if not self._check_path():
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            self._handle_message(body)
        except Exception as e:
            log.error("回调处理异常: %s" % e)
            self._respond(500)
            return
        # 企微要求 5 秒内响应；回复走主动发送接口，这里空 200 即可
        self._respond(200, b"success")

    def _handle_message(self, body):
        bundle = self.bundle
        if bundle["mode"] == "safe":
            # 外层 <Encrypt> 密文：先验签再解密出内部 XML
            root = ET.fromstring(body)
            encrypt = root.findtext("Encrypt") or ""
            q = self._query()
            crypto = bundle["crypto"]
            try:
                crypto.verify(q.get("timestamp", [""])[0], q.get("nonce", [""])[0],
                              encrypt, q.get("msg_signature", [""])[0])
                body = crypto.decrypt(encrypt)
            except WeComCryptoError as e:
                log.warning("安全模式验签/解密失败: %s" % e)
                return
        try:
            msg = self._parse_msg_xml(body)
        except ET.ParseError as e:
            log.warning("消息 XML 解析失败: %s" % e)
            return
        if msg["type"] != "text":
            log.info("忽略非文本消息: type=%s" % msg["type"])
            return
        log.info("[收到] %s: %s" % (msg["sender"], msg["content"][:60]))
        bundle["bot"].on_text(msg["sender"], msg["content"])

    @staticmethod
    def _parse_msg_xml(xml_text):
        root = ET.fromstring(xml_text)
        return {
            "type": root.findtext("MsgType") or "",
            "sender": root.findtext("FromUserName") or "",
            "content": root.findtext("Content") or "",
        }


def start_callback_server(bundle, port, path):
    """启动本地回调服务器（后台线程）。返回 httpd 实例。

    绑定 0.0.0.0 以便内网穿透/端口映射把外网请求转发进来；
    安全性靠回调的 msg_signature 校验兜底（非法请求会被拒绝）。
    """
    WeComCallbackHandler.bundle = bundle
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), WeComCallbackHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
