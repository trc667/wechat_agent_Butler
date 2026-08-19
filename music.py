# -*- coding: utf-8 -*-
"""每日单曲：从网易云热歌榜取一首歌（含播放链接），供定时任务/晨报使用。

用法：
    text = fetch_daily_song()   # 「今日单曲：海屿你 - 马也_Crabbit\nhttps://music.163.com/song?id=xxx」
    # 失败返回 None（调用方自行降级）
"""

import datetime
import io

import requests

# 国内服务走直连，显式禁用系统代理
_NO_PROXY = {"http": None, "https": None}

# 浏览器 UA：网易云接口不带 UA 会拒绝
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

# 网易云热歌榜歌单 ID（旧版 detail 接口免登录可用，实测 2026-08 有效）
HOT_PLAYLIST_ID = 3778678

# 每天取榜单第 N 首（按日期轮换，避免连续几天同一首；榜单本身也天天变）
_OFFSET_DAYS = 10


def parse_song(data, index=0):
    """从网易云歌单 detail 接口 JSON 里取第 index 首歌，
    返回 {"text", "cover_url", "song_id"}；失败返回 None。纯函数，便于测试。"""
    try:
        tracks = data["result"]["tracks"]
        if not tracks:
            return None
        if index >= len(tracks):
            index = index % len(tracks)
        t = tracks[index]
        name = t.get("name", "")
        artists = "、".join(a.get("name", "") for a in t.get("artists") or [])
        song_id = t.get("id")
        album = t.get("album") or {}
        cover_url = album.get("picUrl") or album.get("picUrl500") or ""
    except (KeyError, IndexError, TypeError):
        return None
    if not name or not song_id:
        return None
    line = "%s - %s" % (name, artists) if artists else name
    return {"text": "今日单曲：%s" % line, "cover_url": cover_url,
            "song_id": song_id}


def fetch_hot_comment(song_id, timeout=8):
    """拿歌曲第一条热评（网易云评论接口，免登录）。失败返回 None。"""
    if not song_id:
        return None
    try:
        r = requests.get("https://music.163.com/api/v1/resource/comments/"
                         "R_SO_4_%d?limit=10&offset=0" % int(song_id),
                         timeout=timeout, proxies=_NO_PROXY, headers=_HEADERS)
        r.raise_for_status()
        hot = (r.json().get("hotComments") or [])
        if not hot:
            return None
        content = (hot[0].get("content") or "").strip()
        return content or None
    except Exception:
        return None


def fetch_daily_song(timeout=8):
    """抓网易云热歌榜，按日期轮换取一首。返回单曲文本；全部失败返回 None。"""
    full = fetch_daily_song_full(timeout=timeout)
    return full["text"] if full else None


def make_qrcode(url, size=6):
    """把链接生成二维码 PNG bytes；失败返回 None。
    微信里长按二维码图 → 识别图中二维码 → 打开播放页（链接文本不可点击的曲线方案）。"""
    if not url:
        return None
    try:
        import qrcode
        qr = qrcode.QRCode(box_size=size, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        buf = io.BytesIO()
        qr.make_image().save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def fetch_daily_song_full(timeout=8):
    """抓网易云热歌榜，返回 {"text": "今日单曲+热评", "image": 封面 bytes 或 None,
    "qr": 播放链接二维码 PNG bytes 或 None}；失败返回 None。"""
    try:
        r = requests.get("https://music.163.com/api/playlist/detail?id=%d"
                         % HOT_PLAYLIST_ID, timeout=timeout,
                         proxies=_NO_PROXY, headers=_HEADERS)
        r.raise_for_status()
        index = datetime.date.today().toordinal() % _OFFSET_DAYS
        parsed = parse_song(r.json(), index)
        if not parsed:
            return None
        text, cover_url, song_id = parsed["text"], parsed["cover_url"], parsed["song_id"]
        # 热评（拿不到不影响推送）
        comment = fetch_hot_comment(song_id, timeout=timeout)
        if comment:
            text += "\n热评：%s" % comment
        image = None
        if cover_url:
            try:
                cr = requests.get(cover_url, timeout=timeout,
                                  proxies=_NO_PROXY, headers=_HEADERS)
                cr.raise_for_status()
                image = cr.content
            except Exception:
                image = None  # 封面下载失败不影响文本推送
        play_url = "https://music.163.com/song?id=%s" % song_id
        return {"text": text, "image": image, "qr": make_qrcode(play_url)}
    except Exception:
        return None


if __name__ == "__main__":
    print(fetch_daily_song() or "抓取失败")
