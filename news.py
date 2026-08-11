# -*- coding: utf-8 -*-
"""科技/AI 新闻摘要：抓取免费 RSS 源标题，供每日晨报使用。

用法：
    text = fetch_news()   # 返回文本（每条新闻一行）；全部源失败返回 None
"""

import xml.etree.ElementTree as ET

import requests

# 国内服务走直连，显式禁用系统代理
_NO_PROXY = {"http": None, "https": None}

# 部分源反爬，带浏览器 UA
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

# RSS 源（名称, URL）。实测可用：InfoQ/IT之家/少数派。抓取失败自动跳过。
RSS_SOURCES = [
    ("InfoQ", "https://www.infoq.cn/feed"),
    ("IT之家", "https://www.ithome.com/rss/"),
    ("少数派", "https://sspai.com/feed"),
]


def parse_rss(data, source_name):
    """解析 RSS 2.0 / Atom 的标题列表，返回 [(title, source_name), ...]。
    用元素本地名匹配，兼容 Atom 命名空间。"""
    items = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return items
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]  # 去掉命名空间前缀
        if tag not in ("item", "entry"):
            continue
        for child in node:
            child_tag = child.tag.rsplit("}", 1)[-1]
            if child_tag == "title" and child.text and child.text.strip():
                items.append((child.text.strip(), source_name))
                break
    return items


def fetch_news(max_items=3, timeout=8):
    """抓科技/AI 新闻标题（最多 max_items 条，多源轮流取保证混合）。失败返回 None。"""
    per_source = []
    for name, url in RSS_SOURCES:
        try:
            r = requests.get(url, timeout=timeout, proxies=_NO_PROXY, headers=_HEADERS)
            r.raise_for_status()
            items = parse_rss(r.content, name)
            if items:
                per_source.append((name, items))
        except Exception:
            continue
    if not per_source:
        return None
    lines, seen = [], set()
    idx = 0
    while len(lines) < max_items and any(len(items) > idx for _, items in per_source):
        for name, items in per_source:
            if len(items) > idx and items[idx][0] not in seen:
                seen.add(items[idx][0])
                lines.append("%s（%s）" % (items[idx][0], name))
                if len(lines) >= max_items:
                    break
        idx += 1
    return "今日科技/AI 新闻：\n" + "\n".join(lines)
