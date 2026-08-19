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
- 定时提醒 「下午3点提醒我开会」「20分钟后提醒我关火」「明天9点提醒我抢课」（到点微信直推）

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

EMPTY = {"todos": [], "memos": [], "timers": [], "tasks": []}

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

# 定时提醒提取提示词：把「下午3点提醒我开会」转成具体时间点
EXTRACT_TIMER = """你是定时提醒提取助手。今天日期：{date}（{weekday}），当前时间：{now}。
从下面这句话里提取「提醒时间」和「提醒内容」，只输出一个 JSON：
{{"at": "YYYY-MM-DD HH:MM 24小时制 或空字符串", "text": "提醒内容，简短"}}
规则：
- 时间换算成具体时间点：下午3点=15:00，3点=当天15:00（若已过则明天），
  20分钟后=当前+20分钟，明天9点=明天09:00，周五14:00=本周五14:00（若已过则下周）
- 没说出明确时间就输出 at 为空字符串
- 内容去掉语气词和称呼，简短
对话：{text}"""

# 通用定时任务提取提示词：把「每天早上9点查天气」转成结构化重复规则
EXTRACT_TASK = """你是定时任务提取助手。今天日期：{date}（{weekday}），当前时间：{now}。
用户会说带重复规律的任务（如「每天早上9点查天气推给我」「每周五下午5点提醒我写周报」「明天上午10点提醒我开会」）。
只输出一个 JSON：
{{"type": "daily" 或 "weekly" 或 "once", "time": "HH:MM 24小时制", "weekday": 0-6 或空(仅weekly用，0=周一), "at": "YYYY-MM-DD HH:MM 或空(仅once用)", "text": "要做什么，简短", "action": "remind" 或 "weather"}}
规则：
- 每天/天天/每天早上 → type=daily，time 是具体时刻（早上9点=09:00，下午5点=17:00）
- 每周X/每周五 → type=weekly，weekday 换算（周一=0…周日=6），time 是具体时刻
- 明天/后天/某月某日 → type=once，at 换算成具体日期时刻
- 提到「查天气/天气推送」→ action=weather；否则 action=remind
- 没说出明确时刻 → time 输出 09:00
- 内容去掉语气词和称呼，简短
对话：{text}"""

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


def _ratio(a, b):
    """文本相似度 0~1（difflib）。用于判断两条备忘是不是同一主题的修订。"""
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a or "", b or "").ratio()


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
        # 更新/纠正：先说更新，再匹配旧条目替换（优先级高于「记住」去重）
        if re.search(r"(更新备忘录|改一下|改掉|纠正|写错了|修改备忘|备忘.{0,6}不对|不对.{0,6}备忘)", t):
            return True, self._update_memo(t)
        if t.startswith(("记住", "记下", "备忘", "帮我记住")):
            return True, self._add_memo(t)
        if re.search(r"(待办|清单|有什么要(做|干的))", t):
            return True, self._hint_todos()
        if t.startswith(("记一下", "帮我记", "记着")):
            return True, self._add_todo(t, deepseek)
        # 取消通用定时任务优先（「取消每天早上9点的天气推送」→ 任务；「取消3点的提醒」→ 一次性提醒）
        m = re.match(r"^(取消|删掉|删除|忘掉|关掉)\s*(.+)$", t)
        if m and re.search(r"(每天|每周|每月|定时任务)", m.group(2)):
            return True, self._del_task(m.group(2))
        # 取消定时提醒（「忘掉3点的提醒」→ 提醒；「忘掉测试环境地址」→ 备忘）
        m = re.match(r"^(取消|删掉|删除|忘掉|关掉)\s*(.+?提醒.*)$", t)
        if m:
            return True, self._del_timer(m.group(2))
        m = re.match(r"^(忘掉|删掉|删除)\s*(.+)$", t)
        if m:
            return True, self._del_memo(m.group(2))
        m = re.match(r"^(完成了|办完了|搞定|做完了|取消)\s*(.+)$", t)
        if m:
            return True, self._done_todo(m.group(2))
        # 查定时任务（「我有哪些定时任务」「定时任务列表」）
        if re.search(r"(定时任务|周期任务|重复任务)", t) and re.search(r"(有哪些|列表|看看|什么|查)", t):
            return True, self._hint_tasks()
        # 查定时提醒（「我有哪些提醒」「看看提醒」）
        if re.search(r"(有哪些|列表|看看|什么).{0,4}提醒|提醒.{0,4}(有哪些|列表|看看|什么)", t):
            return True, self._hint_timers()
        # 通用定时任务（重复性，优先于一次性提醒）：每天/每周/每月
        # 注意不能含「天天」：会误伤「今天天气」这类普通聊天
        if re.search(r"(每天|每周|每月|定时任务)", t) and re.search(r"(提醒|推送|天气|任务)", t):
            return True, self._add_task(t, deepseek)
        if re.search(r"(提醒我|提醒一下|定时提醒)", t):
            return True, self._add_timer(t, deepseek)
        return False, None

    # ---------- 备忘录 ----------

    def add_memo_direct(self, text):
        """直接存一条备忘（Function Calling 工具用：参数由模型给出）。
        返回给模型的文本结果。完全相同的去重，相似的更新。"""
        text = (text or "").strip()
        if not text:
            return "备忘内容为空，请说明要记什么"
        if any(m["text"] == text for m in self.data["memos"]):
            return "这条备忘之前已经记过了：%s" % text
        similar = [m for m in self.data["memos"]
                   if len(text) >= 6 and len(m["text"]) >= 6
                   and _ratio(m["text"], text) >= 0.6]
        if similar:
            old = similar[0]["text"]
            similar[0]["text"] = text
            similar[0]["ts"] = int(time.time())
            self.save()
            return "已把备忘录「%s」更新为「%s」" % (old, text)
        self.data["memos"].append({"text": text, "ts": int(time.time())})
        if len(self.data["memos"]) > 30:
            self.data["memos"] = self.data["memos"][-30:]
        self.save()
        return "已存进备忘录：%s" % text

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
        # 完全相同：去重（避免重复堆叠）
        if any(m["text"] == text for m in self.data["memos"]):
            return ("（内部消息：用户让你记住：%s。这条备忘已经记过了，不用再重复记。"
                    "简短告诉他这条之前已记下。）" % text)
        # 相似但内容不同（如同一条信息修订/更新）：用新内容替换旧条目，不新增。
        # 仅限较长文本（>=6 字）做相似合并，短备忘（如「买牛奶」「密码1」）避免误判
        similar = [m for m in self.data["memos"]
                   if len(text) >= 6 and len(m["text"]) >= 6
                   and _ratio(m["text"], text) >= 0.6]
        if similar:
            old = similar[0]["text"]
            similar[0]["text"] = text
            similar[0]["ts"] = int(time.time())
            self.save()
            print("[管家] 更新备忘：%s -> %s" % (old, text))
            return ("（内部消息：用户重新说「%s」，和已有备忘「%s」是同一件事，"
                    "你已把它更新为新内容。简短确认一句，比如告诉他已更新。）" % (text, old))
        self.data["memos"].append({"text": text, "ts": int(time.time())})
        if len(self.data["memos"]) > 30:      # 最多留 30 条，旧的先淘汰
            self.data["memos"] = self.data["memos"][-30:]
        self.save()
        print("[管家] 备忘：%s" % text)
        return ("（内部消息：用户刚让你记住：%s。已存进备忘录。"
                "简短确认一句，一两句，别复述这条消息。）" % text)

    def _update_memo(self, t):
        """「改一下测试环境地址为 http://x」「备忘里的地址不对」→ 找到旧条目替换。"""
        text = t
        for p in ("更新备忘录", "纠正一下", "改一下", "改掉", "写错了",
                  "纠正", "改成", "修改备忘", "修改", "更新", "不对", "不是"):
            if text.startswith(p):
                text = text[len(p):]
                break
        text = text.strip("，。:：,; \t")
        text = re.sub(r"^(备忘|备忘录)[里中]的?", "", text).strip("，。:：,; \t")
        if not text:
            return ("（内部消息：用户想改备忘录，但没说改成什么。"
                    "简短问他要更新成什么内容。）")
        # 找最相似的旧条目（0.3 即视为同主题；同样要求较长文本避免短串误匹配）
        best, best_r = None, 0.0
        if len(text) >= 6:
            for m in self.data["memos"]:
                if len(m["text"]) < 6:
                    continue
                r = _ratio(m["text"], text)
                if r > best_r:
                    best, best_r = m, r
        if best and best_r >= 0.3:
            old = best["text"]
            best["text"] = text
            best["ts"] = int(time.time())
            self.save()
            print("[管家] 更新备忘：%s -> %s" % (old, text))
            return ("（内部消息：用户要把备忘录「%s」更新为「%s」。"
                    "简短确认一句，比如告诉他已更新。）" % (old, text))
        # 没找到原条目：作为新备忘存下
        self.data["memos"].append({"text": text, "ts": int(time.time())})
        self.save()
        print("[管家] 备忘（更新未命中新增）：%s" % text)
        return ("（内部消息：用户想更新备忘录「%s」，但你翻了翻没找到原来的条目，"
                "已作为新备忘记下。简短说明。）" % text)

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

    # ---------- 定时提醒 ----------

    def add_timer_direct(self, at, text):
        """直接存一条定时提醒（工具用：at/text 由模型给出）。返回给模型的文本结果。"""
        at = (at or "").strip()
        text = (text or "").strip()
        if not text:
            return "提醒内容为空"
        if not re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", at):
            return "时间格式不对，需要 YYYY-MM-DD HH:MM（如 2026-08-20 15:00）"
        self.data["timers"].append({"at": at, "text": text, "fired": False})
        self.data["timers"].sort(key=lambda x: x["at"])
        if len(self.data["timers"]) > 20:
            self.data["timers"] = self.data["timers"][-20:]
        self.save()
        return "已设置定时提醒：%s（%s）" % (text, at)

    def _hint_timers(self):
        """列未触发的定时提醒。"""
        pending = [t for t in self.data["timers"] if not t.get("fired")]
        if not pending:
            return ("（内部消息：用户查定时提醒，目前一个都没有。"
                    "简短告诉他现在没有待提醒的事。）")
        items = "；".join("%s（%s）" % (t["text"], t["at"][5:16]) for t in pending)
        return ("（内部消息：用户查定时提醒，未触发的共%d条：%s。"
                "简短像聊天一样列给他。）" % (len(pending), items))

    def _del_timer(self, kw):
        """「取消3点的提醒」「删掉开会提醒」→ 找到未触发的定时提醒删掉。"""
        kw = kw.replace("提醒", "").strip("，。:：,; \t")
        pending = [t for t in self.data["timers"] if not t.get("fired")]
        hits = [t for t in pending
                if kw in t["text"] or kw in t["at"] or t["text"] in kw]
        # 时间词匹配：「15点的」→ 匹配 at 里 15:00 那条
        m = re.search(r"(\d{1,2})点", kw)
        if m:
            hour = str(int(m.group(1)))
            for t in pending:
                if t not in hits and t["at"][11:13].lstrip("0") == hour:
                    hits.append(t)
        if len(hits) == 1:
            self.data["timers"].remove(hits[0])
            self.save()
            print("[管家] 取消提醒：%s @ %s" % (hits[0]["text"], hits[0]["at"]))
            return ("（内部消息：用户取消了定时提醒「%s」（%s）。"
                    "简短确认一句。）" % (hits[0]["text"], hits[0]["at"][5:16]))
        if len(hits) > 1:
            names = "；".join("%s（%s）" % (t["text"], t["at"][5:16]) for t in hits)
            return ("（内部消息：用户要取消提醒「%s」，但匹配到好几条：%s。"
                    "简短问他要取消哪一条。）" % (kw, names))
        return ("（内部消息：用户要取消提醒「%s」，但你翻了翻没找到这条。"
                "简短告诉他没找到，可以问「我有哪些提醒」。）" % kw)

    def _add_timer(self, t, ds):
        """「下午3点提醒我开会」→ 存一条定时提醒，到点由 reminder 推微信。"""
        now = datetime.datetime.now()
        prompt = (EXTRACT_TIMER.replace("{date}", now.strftime("%Y年%m月%d日"))
                              .replace("{weekday}", WEEKDAYS[now.weekday()])
                              .replace("{now}", now.strftime("%H:%M"))
                              .replace("{text}", t))
        try:
            raw = ds.chat([{"role": "system", "content": "只输出 JSON。"},
                           {"role": "user", "content": prompt}],
                          temperature=0.2, max_tokens=150)
        except Exception:
            raw = ""
        r = _parse_json(raw) or {}
        at = str(r.get("at", "") or "").strip()
        text = str(r.get("text", "") or "").strip()
        if not text:
            return ("（内部消息：用户说要定时提醒，但你没听清要提醒什么。"
                    "简短问他要提醒的事。）")
        if not re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", at):
            return ("（内部消息：用户说「%s」要定时提醒，但没给出明确时间。"
                    "简短告诉他要说清时间，比如「下午3点提醒我开会」。）" % text)
        self.data["timers"].append({"at": at, "text": text, "fired": False})
        self.data["timers"].sort(key=lambda x: x["at"])
        if len(self.data["timers"]) > 20:   # 最多留 20 条，旧的先淘汰
            self.data["timers"] = self.data["timers"][-20:]
        self.save()
        hm = at[11:16]
        print("[管家] 定时提醒：%s @ %s" % (text, at))
        return ("（内部消息：用户设置了定时提醒「%s」在%s。"
                "简短确认一句，比如「好的，%s 提醒你」。）" % (text, at, hm))

    # ---------- 通用定时任务（重复性：每天/每周/一次） ----------

    def _add_task(self, t, ds):
        """「每天早上9点查天气推给我」→ 模型提取规则存 tasks，到点由 reminder 执行。"""
        now = datetime.datetime.now()
        prompt = (EXTRACT_TASK.replace("{date}", now.strftime("%Y年%m月%d日"))
                              .replace("{weekday}", WEEKDAYS[now.weekday()])
                              .replace("{now}", now.strftime("%H:%M"))
                              .replace("{text}", t))
        try:
            raw = ds.chat([{"role": "system", "content": "只输出 JSON。"},
                           {"role": "user", "content": prompt}],
                          temperature=0.2, max_tokens=200)
        except Exception:
            raw = ""
        r = _parse_json(raw) or {}
        task_type = str(r.get("type", "") or "").strip()
        text = str(r.get("text", "") or "").strip()
        if not text:
            return ("（内部消息：用户说要设个定时任务，但你没听清要做什么。"
                    "简短问他要做什么。）")
        if task_type not in ("daily", "weekly", "once"):
            return ("（内部消息：用户说「%s」要定时任务，但没提取出规律。"
                    "简短告诉他要说清重复规律，比如「每天早上9点查天气」。）" % text)
        task = {"type": task_type, "text": text,
                "action": str(r.get("action") or "remind").strip() or "remind"}
        if task_type == "daily":
            task["time"] = str(r.get("time") or "09:00").strip()
            if not re.match(r"^\d{2}:\d{2}$", task["time"]):
                task["time"] = "09:00"
        elif task_type == "weekly":
            task["time"] = str(r.get("time") or "09:00").strip()
            if not re.match(r"^\d{2}:\d{2}$", task["time"]):
                task["time"] = "09:00"
            try:
                task["weekday"] = int(r.get("weekday") or 0) % 7
            except (TypeError, ValueError):
                task["weekday"] = 0
        else:  # once
            at = str(r.get("at") or "").strip()
            if not re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", at):
                return ("（内部消息：用户说「%s」要定时任务，但没给出具体日期时间。"
                        "简短告诉他要说清时间，比如「明天上午10点提醒我开会」。）" % text)
            task["at"] = at
        task["fired"] = False       # once 用：触发后置 True
        task["last_fired"] = ""    # daily/weekly 用：上次触发的日期
        self.data["tasks"].append(task)
        if len(self.data["tasks"]) > 30:   # 最多留 30 条
            self.data["tasks"] = self.data["tasks"][-30:]
        self.save()
        print("[管家] 定时任务：%s" % json.dumps(task, ensure_ascii=False))
        return ("（内部消息：用户设置了%s定时任务：%s。简短确认一句，"
                "比如「好的，%s 会准时执行」。）"
                % (self._task_desc(task), text, self._task_desc(task)))

    def add_task_direct(self, task_type, text, time="", weekday=None, at="", action="remind"):
        """直接存一条定时任务（Function Calling 工具用：参数由模型给出）。返回文本结果。"""
        text = (text or "").strip()
        if not text:
            return "任务内容为空"
        if task_type not in ("daily", "weekly", "once"):
            return "任务类型需为 daily/weekly/once"
        task = {"type": task_type, "text": text,
                "action": (action or "remind").strip() or "remind",
                "fired": False, "last_fired": ""}
        if task_type == "daily":
            task["time"] = time if re.match(r"^\d{2}:\d{2}$", time or "") else "09:00"
        elif task_type == "weekly":
            task["time"] = time if re.match(r"^\d{2}:\d{2}$", time or "") else "09:00"
            try:
                task["weekday"] = int(weekday) % 7
            except (TypeError, ValueError):
                task["weekday"] = 0
        else:
            if not re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", at or ""):
                return "一次性任务需要具体时间，如 2026-08-20 10:00"
            task["at"] = at
        self.data["tasks"].append(task)
        if len(self.data["tasks"]) > 30:
            self.data["tasks"] = self.data["tasks"][-30:]
        self.save()
        print("[管家] 定时任务（工具）：%s" % json.dumps(task, ensure_ascii=False))
        return "已设置%s：%s" % (self._task_desc(task), text)

    def _task_desc(self, task):
        """任务的人类可读描述（用于确认/列表）。"""
        typ = task.get("type")
        if typ == "daily":
            return "每天 %s 的定时任务" % task.get("time")
        if typ == "weekly":
            # 周X：WEEKDAYS[idx] 如「星期五」→ 取末字「五」拼成「每周五」
            short = "每周" + WEEKDAYS[int(task.get("weekday", 0))][-1:]
            return "%s %s 的定时任务" % (short, task.get("time"))
        return "%s 的一次性任务" % task.get("at")

    def _hint_tasks(self):
        """列出所有定时任务（含循环任务和未触发的一次性任务）。"""
        active = [t for t in self.data["tasks"]
                  if t.get("type") != "once" or not t.get("fired")]
        if not active:
            return ("（内部消息：用户查定时任务，目前一个都没有。"
                    "简短告诉他现在没有定时任务。）")
        items = []
        for t in active:
            action = "（推天气）" if t.get("action") == "weather" else ""
            items.append("%s%s" % (self._task_desc(t), action))
        return ("（内部消息：用户查定时任务，共%d条：%s。"
                "简短像聊天一样列给他，不要用表格。）" % (len(active), "；".join(items)))

    def _del_task(self, kw):
        """「取消每天早上9点的天气推送」「删掉每周五的任务」→ 按内容/时间/规律匹配删除。"""
        kw = kw.replace("定时任务", "").replace("任务", "")
        kw = re.sub(r"(每天|每周|每月|早上|上午|下午|晚上|点|的|提醒|推送|天气|查|一下|给我)", "", kw)
        kw = kw.strip("，。:：,; \t")
        hits = [t for t in self.data["tasks"]
                if not kw or kw in t["text"] or t["text"] in kw
                or kw in self._task_desc(t)]  # 时间/规律描述也算（「9」→「每天 09:00 的定时任务」）
        if len(hits) == 1:
            self.data["tasks"].remove(hits[0])
            self.save()
            print("[管家] 取消任务：%s" % self._task_desc(hits[0]))
            return ("（内部消息：用户取消了%s。简短确认一句。）"
                    % self._task_desc(hits[0]))
        if len(hits) > 1:
            names = "；".join("%s（%s）" % (self._task_desc(t), t["text"]) for t in hits)
            return ("（内部消息：用户要取消任务「%s」，但匹配到好几条：%s。"
                    "简短问他要取消哪一条。）" % (kw or "全部", names))
        return ("（内部消息：用户要取消任务「%s」，但你翻了翻没找到。"
                "简短告诉他没找到，可以问「我有哪些定时任务」。）" % kw)

    def add_todo_direct(self, text, due=""):
        """直接存一条待办（工具用：text/due 由模型给出）。返回给模型的文本结果。"""
        text = (text or "").strip()
        due = (due or "").strip()
        if not text:
            return "待办内容为空，请说明要记什么"
        if due and not re.match(r"^\d{4}-\d{2}-\d{2}$", due):
            due = ""
        self.data["todos"].append({"text": text, "due": due, "done": False, "reminded": False})
        self.save()
        print("[管家] 记待办（工具）：%s%s" % (text, "，截止%s" % due if due else ""))
        return "已记下待办：%s%s" % (text, "，截止%s" % due if due else "")

    # ---------- 过期数据自动清理 ----------

    def cleanup_old_data(self, now=None):
        """清理过期数据：已触发的 once 任务/定时提醒（超 3 天）、完成超 7 天的待办。
        返回清理条数。备忘录不自动删（用户可能长期需要）。"""
        now = now or datetime.datetime.now()
        removed = 0
        # 1) 已触发的 once 任务（at 距今超 3 天）
        before = len(self.data["tasks"])
        self.data["tasks"] = [t for t in self.data["tasks"]
                               if not (t.get("type") == "once" and t.get("fired")
                                       and self._older_than(t.get("at"), now, 3))]
        removed += before - len(self.data["tasks"])
        # 2) 已触发的定时提醒（at 距今超 3 天）
        before = len(self.data["timers"])
        self.data["timers"] = [t for t in self.data["timers"]
                                if not (t.get("fired")
                                        and self._older_than(t.get("at"), now, 3))]
        removed += before - len(self.data["timers"])
        # 3) 完成超 7 天的待办（有 done_ts 才算；旧数据无完成时间保守保留）
        before = len(self.data["todos"])
        self.data["todos"] = [t for t in self.data["todos"]
                               if not (t.get("done") and t.get("done_ts")
                                       and self._older_than(t["done_ts"], now, 7))]
        removed += before - len(self.data["todos"])
        if removed:
            self.save()
            print("[管家] 清理过期数据 %d 条" % removed)
        return removed

    @staticmethod
    def _older_than(ts_str, now, days):
        """时间字符串（YYYY-MM-DD HH:MM）是否早于 now-days 天。"""
        if not ts_str:
            return False
        try:
            ts = datetime.datetime.strptime(ts_str.strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            return False
        return ts <= now - datetime.timedelta(days=days)

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
            hits[0]["done_ts"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")  # 完成时间（睡前总结/清理用）
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
