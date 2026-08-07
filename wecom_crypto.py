"""企业微信回调加解密（安全模式）。

算法来自企业微信官方文档（WXBizMsgCrypt）：
- 签名：msg_signature = SHA1(sort(token, timestamp, nonce, encrypt))
- 加密：AES-256-CBC，iv = key 前 16 字节，PKCS7 填充
- 报文：随机16字节 | 4字节消息长度 | 明文 | receiveid(=corpid)
"""

import base64
import hashlib
import random
import struct

from Crypto.Cipher import AES


class WeComCryptoError(Exception):
    pass


class WeComCrypto:
    def __init__(self, token, encoding_aes_key, corpid):
        self.token = token
        self.corpid = corpid
        if len(encoding_aes_key) != 43:
            raise WeComCryptoError("EncodingAESKey 长度必须为 43 个字符")
        self.key = base64.b64decode(encoding_aes_key + "=")  # 43字符 -> 32 字节
        if len(self.key) != 32:
            raise WeComCryptoError("EncodingAESKey 不是合法的 Base64 密钥")

    # ---------- 签名 ----------

    def signature(self, timestamp, nonce, encrypt=None):
        """sha1(sort(token, timestamp, nonce[, encrypt]))"""
        parts = [self.token, timestamp, nonce]
        if encrypt:
            parts.append(encrypt)
        parts.sort()
        return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()

    def verify(self, timestamp, nonce, encrypt, msg_signature):
        if msg_signature != self.signature(timestamp, nonce, encrypt):
            raise WeComCryptoError("消息签名校验失败")

    # ---------- 加解密 ----------

    def decrypt(self, encrypted):
        """解密企微推送的密文，返回明文（内部 XML）。"""
        cipher = AES.new(self.key, AES.MODE_CBC, self.key[:16])
        raw = cipher.decrypt(base64.b64decode(encrypted))
        pad = raw[-1]
        if pad < 1 or pad > 32:
            raise WeComCryptoError("PKCS7 填充无效")
        raw = raw[:-pad]
        msg_len = struct.unpack(">I", raw[16:20])[0]
        msg = raw[20:20 + msg_len].decode("utf-8")
        receiveid = raw[20 + msg_len:].decode("utf-8")
        if receiveid != self.corpid:
            raise WeComCryptoError("receiveid(%s) 与 corpid 不匹配" % receiveid)
        return msg

    def encrypt(self, plaintext):
        """加密明文（用于 URL 验证时返回加密的 echostr）。"""
        # 注意：长度字段按 UTF-8 字节数算，中文是 3 字节/字
        payload = plaintext.encode("utf-8")
        text = bytes(random.randint(0, 255) for _ in range(16)) \
            + struct.pack(">I", len(payload)) \
            + payload + self.corpid.encode("utf-8")
        pad = 32 - len(text) % 32
        text += bytes([pad]) * pad
        cipher = AES.new(self.key, AES.MODE_CBC, self.key[:16])
        return base64.b64encode(cipher.encrypt(text)).decode("utf-8")
