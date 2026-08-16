# -*- coding: utf-8 -*-
"""科技/AI 新闻摘要：抓取免费 RSS 源标题，供每日晨报使用。

用法：
    text = fetch_news()   # 返回文本（每条新闻一行）；全部源失败返回 None
"""

import os
import json
import xml.etree.ElementTree as ET

import requests

# 国内服务走直连，显式禁用系统代理
_NO_PROXY = {"http": None, "https": None}

# 新闻存档：每天推送的内容存这里，随时可回看历史
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data", "news_history.json")


def save_history(date_str, text):
    """把某天的新闻文本存档（按日期 YYYY-MM-DD 存）。"""
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            hist = json.load(f)
    except (OSError, ValueError):
        hist = {}
    hist[date_str] = text
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)


def load_history():
    """读全部新闻存档，返回 {日期: 文本}。"""
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

# 部分源反爬，带浏览器 UA
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

# RSS 源（名称, URL）。实测可用：量子位（AI 垂直）/InfoQ/IT之家/少数派。抓取失败自动跳过。
RSS_SOURCES = [
    ("量子位", "https://www.qbitai.com/feed"),   # AI 垂直源，重磅 AI 新闻时效好
    ("InfoQ", "https://www.infoq.cn/feed"),
    ("IT之家", "https://www.ithome.com/rss/"),
    ("少数派", "https://sspai.com/feed"),
]

# AI 相关关键词评分：标题命中就加权，保证重磅 AI 新闻排在前面
_AI_STRONG = ("deepseek", "openai", "gpt", "claude", "gemini", "大模型",
              "人工智能", "qwen", "通义", "kimi", "豆包", "文心", "智谱",
              "glm", "llama", "英伟达", "ai 芯片", "ai芯片")
_AI_MEDIUM = ("ai", "模型", "算力", "芯片", "机器人", "自动驾驶", "开源",
              "发布会", "正式发布", "上线")


def _ai_score(title):
    """标题的 AI 相关度评分（强词 2 分，中词 1 分），0 表示无关。"""
    t = (title or "").lower()
    score = 0
    for w in _AI_STRONG:
        if w in t:
            score += 2
    for w in _AI_MEDIUM:
        if w in t:
            score += 1
    return score


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


def fetch_news(max_items=5, timeout=8):
    """抓科技/AI 新闻标题（最多 max_items 条）。AI 相关优先（评分排序），
    保证 DeepSeek 发布等重磅 AI 新闻不被普通科技新闻挤掉。失败返回 None。"""
    all_items = []  # (score, title, source)
    for name, url in RSS_SOURCES:
        try:
            r = requests.get(url, timeout=timeout, proxies=_NO_PROXY, headers=_HEADERS)
            r.raise_for_status()
            for title, _ in parse_rss(r.content, name):
                all_items.append((_ai_score(title), title, name))
        except Exception:
            continue
    if not all_items:
        return None
    # AI 评分高的排前面；同分保持源顺序（源列表里 AI 垂直源在前）
    all_items.sort(key=lambda x: -x[0])
    lines, seen = [], set()
    for score, title, name in all_items:
        if title in seen:
            continue
        seen.add(title)
        lines.append("%s（%s）" % (title, name))
        if len(lines) >= max_items:
            break
    return "今日科技/AI 新闻：\n" + "\n".join(lines)
