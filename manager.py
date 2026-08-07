# -*- coding: utf-8 -*-
"""小管家（智能管家）的备忘录 + 待办。数据存 data/manager.json（纯本地，可手工改）。

命令（大白话，说错了也不报错，最多当普通聊天）：
- 备忘     「记住测试环境地址 http://10.10.0.8:8080」「记下这个命令」
- 查备忘   「备忘录」「我记过什么」——另外备忘录永远挂在 system prompt 里，
           直接问「那个测试环境的地址是多少」它也能答上来
- 忘掉备忘 「忘掉测试环境地址」「删掉这个命令」
- 记待办   「记一下周三交房租」（日期可说：明天/周三/8月5号/月底）
- 查待办   「我有哪些待办」「清单」
- 完成待办 「完成了交房租」「搞定取快递」

流程：关键词路由（不用模型判断是不是管家消息）-> 只有待办要理解日期时才
用小调用让 DeepSeek 提取 -> 落盘 -> 返回「内部消息」给 bot，让管家用人设口吻回复。
"""

import copy
import datetime
import json
import os
import re
import sys
import time

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
MANAGER_PATH = os.path.join(DATA_DIR, "manager.json")

EMPTY = {"todos": [], "memos": []}

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

# 待办提取提示词：只有记待办时用小调用，日期换算靠模型（喂了今天日期）
EXTRACT_TODO = """你是待办提取助手。今天日期：{date}（{weekday}）。
从下面这句话里提取「要做的事」和「截止日期」，只输出一个 JSON：
{{"text": "要做的事，简短", "due": "YYYY-MM-DD 或空字符串"}}
规则：
- 提炼真正要办的事，去掉语气词和称呼
- 说到日期（明天/后天/周三/下周五/月底/8月5号/下个月…）换算成具体 YYYY-MM-DD；没说日期就输出空字符串
- 不要编造原话里没有的内容
对话：{text}"""


def _parse_json(raw):
    """稳健解析模型输出的 JSON（容忍 ```json 围栏和多余文字）。"""
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


class LifeManager:
    """备忘录/待办的存储与路由。handle() 返回 (handled, hint)。"""

    def __init__(self, path=MANAGER_PATH):
        self.path = path
        self.data = copy.deepcopy(EMPTY)  # 深拷贝：避免多实例共享同一份列表
        self._load()

    # ---------- 存储 ----------

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                user = json.load(f)
            for k in EMPTY:
                if k in user:
                    self.data[k] = user[k]
        except (OSError, ValueError):
            pass

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    # ---------- 路由（不用模型，写死关键词） ----------

    def handle(self, text, deepseek):
        """返回 (handled, hint)。handled=False 走正常聊天；hint 是给 bot 的内部消息。"""
        t = (text or "").strip()
        if not t:
            return False, None
        if re.search(r"(备忘录|记过什么|记住了什么|记了什么)", t):
            return True, self._hint_memos()
        if t.startswith(("记住", "记下", "备忘", "帮我记住")):
            return True, self._add_memo(t)
        if re.search(r"(待办|清单|有什么要(做|干的))", t):
            return True, self._hint_todos()
        if t.startswith(("记一下", "帮我记", "记着")):
            return True, self._add_todo(t, deepseek)
        m = re.match(r"^(忘掉|删掉|删除)\s*(.+)$", t)
        if m:
            return True, self._del_memo(m.group(2))
        m = re.match(r"^(完成了|办完了|搞定|做完了|取消)\s*(.+)$", t)
        if m:
            return True, self._done_todo(m.group(2))
        return False, None

    # ---------- 备忘录 ----------

    def memo_prompt(self):
        """备忘录片段：永远拼在 system prompt 末尾，让管家查得到。"""
        memos = self.data["memos"]
        if not memos:
            return ""
        lines = "\n".join("%d. %s" % (i, m["text"]) for i, m in enumerate(memos, 1))
        return ("\n\n## 备忘录（用户让你记住的；他问「那个XX是什么/在哪/多少」时，"
                "先在这里找，找到就直接答）\n%s" % lines)

    def _add_memo(self, t):
        for p in ("帮我记住", "记住", "记下", "备忘"):
            if t.startswith(p):
                text = t[len(p):].strip("，。:：,; \t")
                break
        else:
            text = t
        if not text:
            return ("（内部消息：用户说让你记住点什么，但没说内容。简短问他要记什么。）")
        # 去重：内容相同的备忘录不再重复存（避免晨报/查询里一堆重复）
        if any(m["text"] == text for m in self.data["memos"]):
            return ("（内部消息：用户让你记住：%s。这条备忘已经记过了，不用再重复记。"
                    "简短告诉他这条之前已记下。）" % text)
        self.data["memos"].append({"text": text, "ts": int(time.time())})
        if len(self.data["memos"]) > 30:      # 最多留 30 条，旧的先淘汰
            self.data["memos"] = self.data["memos"][-30:]
        self.save()
        print("[管家] 备忘：%s" % text)
        return ("（内部消息：用户刚让你记住：%s。已存进备忘录。"
                "简短确认一句，一两句，别复述这条消息。）" % text)

    def _hint_memos(self):
        memos = self.data["memos"]
        if not memos:
            return ("（内部消息：用户查备忘录，目前空的。简短告诉他现在没有记任何东西。）")
        items = "；".join(m["text"] for m in memos)
        return ("（内部消息：用户查备忘录，共%d条：%s。简短按聊天口吻列给他，"
                "别用表格，别写太长。）" % (len(memos), items))

    def _del_memo(self, kw):
        hits = [m for m in self.data["memos"] if kw in m["text"] or m["text"] in kw]
        if len(hits) == 1:
            self.data["memos"].remove(hits[0])
            self.save()
            print("[管家] 忘掉备忘：%s" % hits[0]["text"])
            return ("（内部消息：用户要忘掉「%s」，你已删掉这条备忘。简短确认一句。）"
                    % hits[0]["text"])
        if len(hits) > 1:
            names = "、".join(m["text"] for m in hits)
            return ("（内部消息：用户要删「%s」，但类似的备忘有好几条：%s。"
                    "简短问他是哪一条。）" % (kw, names))
        return ("（内部消息：用户要删「%s」，但你翻了备忘录没找到。"
                "简短告诉他没找到，问他要删的是哪条。）" % kw)

    # ---------- 待办 ----------

    def _add_todo(self, t, ds):
        now = datetime.datetime.now()
        prompt = (EXTRACT_TODO.replace("{date}", now.strftime("%Y年%m月%d日"))
                              .replace("{weekday}", WEEKDAYS[now.weekday()])
                              .replace("{text}", t))
        try:
            raw = ds.chat([{"role": "system", "content": "只输出 JSON。"},
                           {"role": "user", "content": prompt}],
                          temperature=0.2, max_tokens=200)
        except Exception:
            raw = ""
        r = _parse_json(raw) or {}
        todo = str(r.get("text", "") or "").strip()
        if not todo:
            return ("（内部消息：用户说记个待办，但你没听清要记什么。"
                    "简短问他要记啥。）")
        due = str(r.get("due", "") or "").strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", due):  # 日期抽歪了宁可当天不设
            due = ""
        self.data["todos"].append({"text": todo, "due": due, "done": False, "reminded": False})
        self.save()
        due_str = "，截止%s" % due if due else ""
        print("[管家] 记待办：%s%s" % (todo, due_str))
        return ("（内部消息：用户刚让你记一条待办「%s」%s，你已经记下了。"
                "简短确认一句，一两句，别复述这条消息。）" % (todo, due_str))

    def _hint_todos(self):
        active = [t for t in self.data["todos"] if not t["done"]]
        if not active:
            return ("（内部消息：用户在查待办，目前一条都没有。"
                    "简短告诉他「都办完啦」之类。）")
        items = "；".join("%s（%s前）" % (t["text"], t["due"]) if t.get("due")
                          else t["text"] for t in active)
        return ("（内部消息：用户在查待办，还没完成的共%d条：%s。"
                "像聊天一样列给他，不用表格，别写太长。）" % (len(active), items))

    def _done_todo(self, kw):
        active = [t for t in self.data["todos"] if not t["done"]]
        hits = [t for t in active if kw in t["text"] or t["text"] in kw]
        if len(hits) == 1:
            hits[0]["done"] = True
            self.save()
            print("[管家] 完成待办：%s" % hits[0]["text"])
            return ("（内部消息：用户说「%s」办完了，你已把待办「%s」划掉。"
                    "简短确认一句。）" % (kw, hits[0]["text"]))
        if len(hits) > 1:
            names = "、".join(t["text"] for t in hits)
            return ("（内部消息：用户说要完成「%s」，但类似的待办有好几条：%s。"
                    "简短问他是哪一条。）" % (kw, names))
        return ("（内部消息：用户说要完成「%s」，但你翻了一遍没找到这条待办。"
                "简短告诉他没找到，问他要办的是哪件事。）" % kw)


if __name__ == "__main__":
    # 离线自测：假模型只回预设 JSON，验证路由+存取+查询逻辑（不联网不花钱）
    import tempfile

    class _FakeDS:
        def chat(self, messages, **kw):
            return '{"text": "交房租", "due": "2026-08-05"}'

    tmp = tempfile.mkdtemp(prefix="xiaoqi_mgr_")
    mgr = LifeManager(os.path.join(tmp, "manager.json"))
    for msg in ["记住线上测试环境地址 http://10.10.0.8:8080",
                "记一下周三交房租",
                "备忘录",
                "那个测试环境的地址是多少",
                "忘掉测试环境地址",
                "我有哪些待办",
                "完成了交房租"]:
        handled, hint = mgr.handle(msg, _FakeDS())
        print("[测试] %s -> 管家命令: %s" % (msg, handled))
        if handled:
            print("      %s" % hint)
    print("\n数据：")
    print(json.dumps(mgr.data, ensure_ascii=False, indent=2))
    print("自测通过 ✅（不联网；真实效果请重启小管家后在微信里发命令）")
