# -*- coding: utf-8 -*-
"""每日单曲：从网易云热歌榜取一首歌（含播放链接），供定时任务/晨报使用。

用法：
    text = fetch_daily_song()   # 「今日单曲：海屿你 - 马也_Crabbit\nhttps://music.163.com/song?id=xxx」
    # 失败返回 None（调用方自行降级）
"""

import datetime

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
    """从网易云歌单 detail 接口 JSON 里取第 index 首歌，返回文本；失败返回 None。
    纯函数，便于测试。"""
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
    except (KeyError, IndexError, TypeError):
        return None
    if not name or not song_id:
        return None
    line = "%s - %s" % (name, artists) if artists else name
    return "今日单曲：%s\nhttps://music.163.com/song?id=%s" % (line, song_id)


def fetch_daily_song(timeout=8):
    """抓网易云热歌榜，按日期轮换取一首。返回单曲文本；全部失败返回 None。"""
    try:
        r = requests.get("https://music.163.com/api/playlist/detail?id=%d"
                         % HOT_PLAYLIST_ID, timeout=timeout,
                         proxies=_NO_PROXY, headers=_HEADERS)
        r.raise_for_status()
        index = datetime.date.today().toordinal() % _OFFSET_DAYS
        return parse_song(r.json(), index)
    except Exception:
        return None


if __name__ == "__main__":
    print(fetch_daily_song() or "抓取失败")
