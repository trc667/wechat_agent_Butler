# -*- coding: utf-8 -*-
"""抖音短视频总结：分享链接 → 下载视频 → 提取音频 → 语音转写 → DeepSeek 摘要。

链路：
1. extract_url      从抖音分享口令/链接文本里提取视频 URL
2. download_video   yt-dlp 下载（无水印 mp4，保存到 data/videos/）
3. extract_audio    ffmpeg 提取 16kHz 单声道 wav
4. transcribe       百炼 qwen-audio-3.0-asr-flash（Base64 直传，无需公网 URL）
5. summarize        DeepSeek 生成中文摘要（一句话概括 + 要点）

用法：
    text = summarize_douyin_link("8.88 复制打开抖音… https://v.douyin.com/xxxx/")
    # 返回摘要文本；失败返回 None（调用方降级）
"""

import base64
import os
import re
import subprocess
import tempfile
import time

import requests

# 国内服务走直连，显式禁用系统代理（避免被魔戒/Clash 劫持）
_NO_PROXY = {"http": None, "https": None}

# 视频临时目录（下载/音频中间文件，用完清理）
VIDEO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "data", "videos")

# 百炼 ASR：qwen-audio-3.0-asr-flash（Base64 直传，识别效果好、额度大）
_ASR_URL = ("https://dashscope.aliyuncs.com/api/v1/services/"
            "aigc/multimodal-generation/generation")

# 音频保护：超过 5 分钟的 wav(16k mono) base64 会超百炼 10MB 限制
_MAX_AUDIO_SECONDS = 300

_URL_RE = re.compile(r"https?://[^\s，。！？、\"'<>]+")


def extract_url(text):
    """从抖音分享口令/链接文本里提取视频 URL；找不到返回 None。

    兼容：纯链接（https://v.douyin.com/xxx/）、
    口令文本（8.88 复制打开抖音… https://v.douyin.com/xxx/）、
    网页链接（https://www.douyin.com/video/xxx）。
    """
    text = (text or "").replace("\\", "")
    for m in _URL_RE.findall(text):
        url = m.rstrip("，。！？、;；）)】】】")
        if re.search(r"(v\.douyin\.com|douyin\.com|iesdouyin\.com)", url):
            return url
    return None


def download_video(url, out_dir=VIDEO_DIR, timeout=90):
    """下载视频（优先 Playwright 真实浏览器，失败回退 yt-dlp），
    返回本地 mp4 路径；失败返回 None。

    抖音 2026 年网页需要浏览器 JS 签名（__ac_signature），yt-dlp 无法绕过，
    必须用 Playwright 真实浏览器内核打开页面拦截真实视频地址。"""
    from douyin_dl import download_douyin_video
    path = download_douyin_video(url, out_dir)
    if path:
        return path
    return _download_via_ytdlp(url, out_dir, timeout)


def _download_via_ytdlp(url, out_dir, timeout=90):
    """yt-dlp 下载（通用平台兜底；抖音一般会被反爬拦截）。"""
    try:
        import yt_dlp
    except ImportError:
        return None
    os.makedirs(out_dir, exist_ok=True)
    opts = {
        "outtmpl": os.path.join(out_dir, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "proxy": "",           # 直连，不读系统代理
        "socket_timeout": 20,
        "retries": 2,
        "format": "mp4/best",
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            # 处理扩展名与 format 不匹配的情况（如下载后实际是 .webm/.flv）
            if not os.path.isfile(path):
                base = os.path.splitext(path)[0]
                cands = [f for f in os.listdir(out_dir)
                         if f.startswith(os.path.basename(base))]
                if cands:
                    path = os.path.join(out_dir, cands[0])
            return path if os.path.isfile(path) else None
    except Exception:
        return None


def extract_audio(video_path, out_dir=VIDEO_DIR, max_seconds=_MAX_AUDIO_SECONDS):
    """ffmpeg 提取音频 → 16kHz 单声道 wav；返回 wav 路径；失败返回 None。"""
    try:
        os.makedirs(out_dir, exist_ok=True)
        wav = os.path.join(out_dir, "audio_%d.wav" % os.getpid())
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vn",
               "-ac", "1", "-ar", "16000", "-t", str(max_seconds), wav]
        r = subprocess.run(cmd, capture_output=True, timeout=180)
        if r.returncode != 0 or not os.path.isfile(wav):
            return None
        return wav
    except Exception:
        return None


def transcribe(wav_path, api_key, timeout=60):
    """百炼 qwen-audio-3.0-asr-flash 转写（Base64 直传）。返回文本；失败返回 None。

    兼容非流式两种返回结构：
    - output.output.sentence.text（非流式单句）
    - output.choices[0].message.content（多模态格式兜底）
    """
    try:
        with open(wav_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
    except OSError:
        return None
    payload = {
        "model": "qwen-audio-3.0-asr-flash",
        "input": {"messages": [{"role": "user", "content": [
            {"type": "input_audio",
             "input_audio": {"data": "data:audio/wav;base64," + b64}}]}]},
        "parameters": {"format": "wav", "sample_rate": "16000"},
    }
    try:
        resp = requests.post(_ASR_URL,
                             headers={"Authorization": "Bearer " + api_key,
                                      "Content-Type": "application/json",
                                      "X-DashScope-SSE": "disable"},
                             json=payload, timeout=timeout, proxies=_NO_PROXY)
        resp.raise_for_status()
        d = resp.json()
        out = d.get("output") or {}
        # 非流式：output.output.sentence.text
        inner = out.get("output") or {}
        sentence = inner.get("sentence") or {}
        text = (sentence.get("text") or "").strip()
        if text:
            return text
        # 兜底：choices 结构
        choices = out.get("choices") or []
        if choices:
            content = (choices[0].get("message") or {}).get("content") or ""
            if isinstance(content, list):
                content = "".join(str(c) for c in content)
            content = str(content).strip()
            if content:
                return content
        return None
    except Exception:
        return None


def summarize(text, ds):
    """DeepSeek 生成中文摘要（一句话概括 + 要点）。失败返回 None。"""
    if not text or not text.strip():
        return None
    prompt = (
        "下面是某短视频的口播转写文本，请用中文输出结构化摘要：\n"
        "一句话概括：<一句话说清视频讲了什么>\n"
        "要点：\n1. ...\n2. ...\n"
        "要求：要点不超过 5 条，每条 20 字内；简明准确；不要 emoji；"
        "如果转写内容太碎片化，尽力归纳大意。\n\n"
        "转写文本：\n" + text[:3000]
    )
    # 网络抖动/限流会偶发失败，重试一次再放弃
    for attempt in range(2):
        try:
            reply = ds.chat([{"role": "user", "content": prompt}],
                            temperature=0.3, max_tokens=500)
            if reply and reply.strip():
                return reply.strip()
        except Exception as e:
            print("[video] 摘要失败(第%d次): %s" % (attempt + 1, e))
        if attempt == 0:
            time.sleep(1)
    return None


def _cleanup(files):
    """删除中间文件（失败忽略）。"""
    for f in files:
        try:
            if f and os.path.isfile(f):
                os.remove(f)
        except OSError:
            pass


def summarize_douyin_link(text, ds=None, api_key=None):
    """主入口：抖音链接 → 摘要文本。任一步失败返回 None。

    ds 可注入（测试用）；api_key 可注入，默认读配置。
    """
    from config import load_config
    from deepseek import DeepSeek
    from textfilter import strip_emoji

    url = extract_url(text)
    if not url:
        return None
    if ds is None:
        ds = DeepSeek()
    cfg = load_config()
    if api_key is None:
        api_key = cfg.get("dashscope_api_key") or ""
    if not api_key:
        return None

    video_path = download_video(url)
    if not video_path:
        return None
    wav_path = extract_audio(video_path)
    transcript = transcribe(wav_path, api_key) if wav_path else None
    _cleanup([video_path, wav_path])
    if not transcript:
        return None
    result = summarize(transcript, ds)
    return strip_emoji(result) if result else None


if __name__ == "__main__":
    # 离线自测：链接提取
    samples = [
        "8.88 复制打开抖音，看看【AI科普】的视频 https://v.douyin.com/iAbCdEf/ 复制此链接",
        "https://www.douyin.com/video/1234567890",
        "今天天气不错",
    ]
    for s in samples:
        print(repr(s[:20]), "->", extract_url(s))
