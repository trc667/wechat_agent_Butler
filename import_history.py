# -*- coding: utf-8 -*-
"""把微信聊天记录一次性导入小淇的人设（背景档案）。

一次性操作：聊天记录 -> DeepSeek 浓缩成一段「宝贝的背景档案」
-> 存为 data/imported_background.txt -> persona.py 每次对话自动带上。

用法：
    1. 把聊天记录存到 data/chat_import.txt（UTF-8），两种格式都支持：
       - 每行「昵称：内容」，如：老王：今晚打球吗
       - 直接从微信复制粘贴的聊天记录（时间/昵称/内容 三段式，自动识别）
       你自己说的话用「我：」开头（或用 --me 指定你的昵称）。
    2. 先关掉小淇的窗口，再运行：
       python import_history.py [--me 你的微信昵称]
    3. 运行结果会生成人设背景；不满意可以直接编辑
       data/imported_background.txt（纯文本，随时可改）。
    4. 重启小淇生效。

注意：文件只保留最近 300 条消息（更早的太零碎，浓缩意义不大）。
"""
import argparse
import os
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from deepseek import DeepSeek, DeepSeekError

IMPORT_FILE = "data/chat_import.txt"
OUTPUT_FILE = "data/imported_background.txt"
MAX_MSGS = 300

# 微信粘贴的记录里夹着时间行，比如：21:35 / 下午 9:35 / 2026-07-30 21:35 / 昨天 21:35
TS_RE = re.compile(
    r"^(?:"
    r"\d{1,2}[:：]\d{2}(?:[:：]\d{2})?"
    r"|(?:上午|下午|晚上|早上)\s*\d{1,2}[:：]\d{2}"
    r"|\d{4}[-年/]\d{1,2}[-月/]\d{1,2}.*"
    r"|昨天|今天|前天)"
)

SUMMARY_PROMPT = """你是文字编辑。下面是一段「宝贝」过去的微信聊天记录（宝贝=用户本人，其他名字是他的朋友/家人）。

请把它浓缩成一段第三人称的「宝贝的背景档案」，给小淇（宝贝的 AI 女友）做背景知识用。

要求：
- 150-300 字，一段或几段连贯中文，纯陈述事实，不要评价、不要文学修辞
- 覆盖：宝贝的生活与工作、兴趣爱好、家人朋友（保留人名）、重要日子（如生日）、宠物等
- 写成小淇本来就该知道的事，不要出现「聊天记录」「根据对话」之类的字样
- 信息太多时优先保留最近、最常出现的内容

聊天记录：
{conversation}"""


def is_ts(line):
    return bool(TS_RE.match(line))


def split_name(line):
    """按「昵称：内容」切分（中文冒号或英文冒号）。不是这种格式返回 (None, None)。"""
    for sep in ("：", ":"):
        if sep in line:
            name, content = line.split(sep, 1)
            name = name.strip()
            if name and not is_ts(name):
                return name, content.strip()
    return None, None


def parse_chat(text):
    """解析聊天文本 -> [{"speaker": 名字, "text": 内容}]"""
    msgs = []
    cur = None
    prev_ts = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("以下为聊天记录"):
            continue
        if is_ts(line):                      # 时间行：结束上一条消息
            if cur:
                msgs.append(cur)
                cur = None
            prev_ts = True
            continue
        name, content = split_name(line)
        if name is not None:                 # 昵称：内容
            if cur:
                msgs.append(cur)
            cur = {"speaker": name, "text": content}
            prev_ts = False
        elif prev_ts:                        # 时间行之后 = 昵称行（微信三段式）
            if cur:
                msgs.append(cur)
            cur = {"speaker": line, "text": ""}
            prev_ts = False
        else:                                # 普通行：接在上一句后面（长消息换行）
            if cur is None:
                cur = {"speaker": "朋友", "text": line}
            else:
                cur["text"] = (cur["text"] + " " + line).strip()
    if cur:
        msgs.append(cur)
    return msgs


def main():
    parser = argparse.ArgumentParser(description="把微信聊天记录一次性导入小淇的人设")
    parser.add_argument("--me", default="我",
                        help="你自己的昵称（聊天里用这个称呼说的话算你说的，默认「我」）")
    args = parser.parse_args()

    try:
        with open(IMPORT_FILE, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        print("[-] 没找到 %s，请先创建它并把聊天记录粘贴进去（UTF-8）" % IMPORT_FILE)
        sys.exit(1)

    msgs = parse_chat(text)
    if not msgs:
        print("[-] 没解析出任何消息，检查一下文件内容格式")
        sys.exit(1)

    if len(msgs) > MAX_MSGS:
        print("[*] 记录共 %d 条，只取最近 %d 条浓缩" % (len(msgs), MAX_MSGS))
        msgs = msgs[-MAX_MSGS:]

    conversation = "\n".join("%s：%s" % (m["speaker"], m["text"]) for m in msgs if m["text"])
    print("[+] 已读取 %d 条消息，正在浓缩成背景档案…（需要十几秒，别关窗口）")

    deepseek = DeepSeek()
    prompt = SUMMARY_PROMPT.replace("{conversation}", conversation)
    try:
        background = deepseek.chat(
            [{"role": "system", "content": "你是一个严谨的文字编辑，只输出档案内容本身。"},
             {"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=800)
    except DeepSeekError as e:
        print("[-] 浓缩失败: %s" % e)
        sys.exit(1)
    if not background or not background.strip():
        print("[-] 浓缩结果为空，再试一次")
        sys.exit(1)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(background.strip() + "\n")
    print("[+] 已写入 %s，小淇的背景档案如下：" % OUTPUT_FILE)
    print("=" * 44)
    print(background.strip())
    print("=" * 44)
    print("不满意可以直接编辑 %s 这个文件，然后重启小淇" % OUTPUT_FILE)


if __name__ == "__main__":
    main()
