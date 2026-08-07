# -*- coding: utf-8 -*-
"""文本过滤公共模块：emoji/符号/波浪号物理过滤 + 多余空格整理。

从 bot.py 抽出，供聊天回复（bot.py）和主动提醒（reminder.py）共用，
避免 reminder 反向 import bot 造成循环依赖。
"""
import re

# 物理过滤 emoji/符号（用户要求：一个都不要）
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # 表情符号与象形文字扩展
    "\U0001F000-\U0001F02F"  # 麻将牌
    "\U0001F0A0-\U0001F0FF"  # 扑克牌
    "\U00002600-\U000027BF"  # 杂项符号（含 ☀✈⚡ 等）
    "\U0000FE0F"             # 变体选择符
    "\U0000200D"             # 零宽连接符
    "～~"           # 波浪号 ～ ~
    "]+",
    flags=re.UNICODE,
)


def strip_emoji(text):
    """去掉所有 emoji 和波浪号（保留正常文字），顺带整理多余空格。"""
    text = _EMOJI_RE.sub("", text or "")
    return re.sub(r" {2,}", " ", text)
