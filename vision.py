# -*- coding: utf-8 -*-
"""多模态识图：阿里云百炼（DashScope）OpenAI 兼容接口（qwen-vl-plus 等视觉模型）。

配置（.env，密钥不入库）：
    DASHSCOPE_API_KEY=sk-xxx
    DASHSCOPE_MODEL=qwen-vl-plus
    DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

用法：
    text = describe_image(image_bytes)      # 图片字节 -> 中文描述；失败返回 None
"""

import base64
import io

import requests

from config import load_config
from media import guess_image_mime

# 国内服务走直连，显式禁用系统代理
_NO_PROXY = {"http": None, "https": None}

_DEFAULT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# 百炼视觉模型限制：宽高 <= 4096、文件 <= 8MB
_MAX_SIDE = 4096
_MAX_BYTES = 8 * 1024 * 1024


def _prepare_image(image_bytes, max_side=_MAX_SIDE, max_bytes=_MAX_BYTES, quality=85):
    """把图片压到视觉模型限制内；超限缩放+转 JPEG，否则原样返回。
    返回 (bytes, forced_mime)；forced_mime 非 None 表示已重新编码。"""
    if not image_bytes or len(image_bytes) <= max_bytes:
        try:
            from PIL import Image
            probe = Image.open(io.BytesIO(image_bytes))
            if max(probe.size) <= max_side:
                return image_bytes, None
        except Exception:
            return image_bytes, None
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
    except Exception:
        return image_bytes, None
    w, h = img.size
    if max(w, h) > max_side:
        ratio = max_side / float(max(w, h))
        img = img.resize((max(1, int(w * ratio)), max(1, int(h * ratio))),
                         Image.LANCZOS)
    if img.mode in ("RGBA", "P", "LA", "CMYK"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue(), "image/jpeg"


def describe_image(image_bytes, prompt=None, timeout=60):
    """把图片字节发给视觉模型，返回中文描述文本；失败返回 None。
    自动压缩超大图片（百炼限制 4096px/8MB）。"""
    cfg = load_config()
    api_key = cfg.get("dashscope_api_key") or ""
    if not api_key:
        return None
    if not image_bytes:
        return None
    try:
        image_bytes, forced_mime = _prepare_image(image_bytes)
        mime = forced_mime or guess_image_mime(image_bytes)
        b64 = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "model": cfg.get("dashscope_model") or "qwen-vl-plus",
            "messages": [{"role": "user", "content": [
                {"type": "text",
                 "text": prompt or "请用中文简要描述这张图片的内容，两三句以内，像聊天口吻。"},
                {"type": "image_url",
                 "image_url": {"url": "data:%s;base64,%s" % (mime, b64)}},
            ]}],
            "stream": False,
        }
        base = (cfg.get("dashscope_base_url") or _DEFAULT_BASE).rstrip("/")
        resp = requests.post(base + "/chat/completions",
                             headers={"Authorization": "Bearer " + api_key,
                                      "Content-Type": "application/json"},
                             json=payload, timeout=timeout, proxies=_NO_PROXY)
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        return text or None
    except Exception as e:
        print("[识图] 调用失败: %s" % e)
        return None
