# -*- coding: utf-8 -*-
"""微信媒体下载与解密：图片等媒体走 CDN（AES-128-ECB 加密传输）。

协议要点（来自 iLink 官方 openclaw-weixin 实现）：
- image_item 含 encrypt_query_param（CDN 下载 URL）+ aes_key（base64 编码的 AES-128 key）
- 下载加密数据后用 AES-128-ECB + PKCS7 解密得到原始图片

用法：
    img_bytes = download_image(item)   # item 是 image_item 字典，失败返回 None
    mime = guess_image_mime(img_bytes) # 返回 'image/jpeg' 等
"""

import base64
import json
import urllib.parse

import requests

# 微信 CDN 走直连，显式禁用系统代理（避免被本地代理软件劫持）
_NO_PROXY = {"http": None, "https": None}

# CDN 域名（协议文章 + 腾讯官方 openclaw-weixin 源码确认）
CDN_BASE = "https://novac2c.cdn.weixin.qq.com/c2c"

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    HAVE_AES = True
except ImportError:  # 没装 pycryptodome 时降级（解密不可用）
    HAVE_AES = False

CDN_DOWNLOAD_TIMEOUT = 20


def _parse_aes_key(key):
    """把 AES key 解析成 16 字节。兼容三种格式（官方 parseAesKey）：
    1. 32 位 hex 字符串（image_item.aeskey 常见）
    2. base64(16 字节原文)
    3. base64(32 位 hex 字符串)
    解析失败返回 None。"""
    if not key:
        return None
    key = str(key).strip()
    if len(key) == 32 and all(c in "0123456789abcdefABCDEF" for c in key):
        return bytes.fromhex(key)
    try:
        decoded = base64.b64decode(key)
    except Exception:
        return None
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        s = decoded.decode("ascii", errors="ignore")
        if all(c in "0123456789abcdefABCDEF" for c in s):
            return bytes.fromhex(s)
    return None


def _build_download_url(encrypt_query_param):
    """构造 CDN 下载 URL（对齐官方 buildCdnDownloadUrl）。"""
    return "%s/download?encrypted_query_param=%s" % (
        CDN_BASE, urllib.parse.quote(encrypt_query_param, safe=""))


def decrypt_media(data, key):
    """AES-128-ECB 解密 CDN 下载的媒体数据。key 可为 16 字节 bytes、hex 串或 base64 串。"""
    if not HAVE_AES:
        raise RuntimeError("未安装 pycryptodome，无法解密媒体")
    if isinstance(key, str):
        key = _parse_aes_key(key)
    if key is None or len(key) != 16:
        raise ValueError("AES key 解析失败")
    cipher = AES.new(key, AES.MODE_ECB)
    plain = cipher.decrypt(data)
    try:
        return unpad(plain, AES.block_size)
    except ValueError:  # 数据本身可能正好是块整数倍且无填充
        return plain


def download_image(item, timeout=CDN_DOWNLOAD_TIMEOUT):
    """从 image_item 下载并解密图片，返回图片字节；失败返回 None。

    对齐腾讯官方 openclaw-weixin：
    - URL 优先 image_item.media.full_url；否则用 media.encrypt_query_param 构造 CDN 下载 URL
    - AES key：image_item.aeskey（hex）优先，其次 media.aes_key（base64）
    """
    if not item or not isinstance(item, dict):
        return None
    print("[media] image_item 结构: %s" % json.dumps(item, ensure_ascii=False)[:400])
    media = item.get("media")
    if not isinstance(media, dict):
        media = {}

    full_url = str(media.get("full_url") or "").strip()
    enc = media.get("encrypt_query_param") or item.get("encrypt_query_param") or ""
    if not isinstance(enc, str):
        enc = ""
    url = full_url or (_build_download_url(enc) if enc else "")
    if not url:
        return None

    aes_key = item.get("aeskey") or media.get("aes_key") or item.get("aes_key") or ""
    if isinstance(aes_key, dict):
        aes_key = aes_key.get("value") or ""
    key = _parse_aes_key(aes_key)
    try:
        resp = requests.get(url, timeout=timeout, proxies=_NO_PROXY)
        resp.raise_for_status()
        data = resp.content
        if key and HAVE_AES:
            data = decrypt_media(data, key)
        return data if data else None
    except Exception as e:
        print("[media] 图片下载/解密失败: %s" % e)
        return None


def guess_image_mime(data):
    """根据文件头判断图片 MIME（JPEG/PNG/WEBP/GIF，未知返回 image/jpeg 兜底）。"""
    if data and len(data) >= 4:
        if data[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        if data[:4] == b"GIF8":
            return "image/gif"
    return "image/jpeg"
