"""小淇的长期记忆：memory.json（档案/事实/重要日期）+ history.jsonl（聊天流水）。

记忆提炼：每 N 轮对话用 DeepSeek 跑一次小调用，从用户消息中提取持久事实，合并去重后落盘。
"""

import copy
import json
import os
import time

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
MEMORY_PATH = os.path.join(DATA_DIR, "memory.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.jsonl")

EMPTY_MEMORY = {
    "user_profile": {},   # 称呼 / 生日 / 工作 / 爱好 ...
    "facts": [],          # 持久事实，上限 50 条
    "important_dates": [],  # [{"date": "MM-DD", "event": "..."}]
    "notes": "",
}

EXTRACT_PROMPT = """你是记忆助手。从下面的微信对话里，提取「用户」说过、值得长期记住的事实。
忽略情绪化闲聊、寒暄、一次性话题。如果对话里没有新信息，就输出空数组。

只输出一个 JSON 对象，格式：
{"profile": {"字段": "值", ...}, "new_facts": ["..."], "dates": [{"date": "MM-DD", "event": "..."}]}

要求：
- profile：只放有信息的字段，如 称呼、生日、工作、爱好、城市、家人；没信息就 {}
- new_facts：关于用户本人的持久事实，一条一句、简短明确，最多 8 条；没有就 []
- dates：重要的日子（生日、纪念日、认识的日子），date 格式 MM-DD；没有就 []
- 不要重复已有的记忆：{existing}

对话：
{conversation}"""


class Memory:
    def __init__(self, deepseek=None, data_dir=None):
        self.deepseek = deepseek
        self.data_dir = data_dir or DATA_DIR
        self.memory_path = os.path.join(self.data_dir, "memory.json")
        self.history_path = os.path.join(self.data_dir, "history.jsonl")
        self.data = copy.deepcopy(EMPTY_MEMORY)  # 深拷贝：避免多实例共享同一份列表/字典
        self._load()

    # ---------- 基础读写 ----------

    def _load(self):
        try:
            with open(self.memory_path, "r", encoding="utf-8") as f:
                user = json.load(f)
            for k in EMPTY_MEMORY:
                if k in user:
                    self.data[k] = user[k]
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def save(self):
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.memory_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def text(self):
        """把记忆渲染成提示词片段，供 system prompt 使用。"""
        lines = []
        p = self.data["user_profile"]
        if p:
            lines.append("档案：" + "，".join("%s：%s" % (k, v) for k, v in p.items()))
        if self.data["facts"]:
            lines.append("知道的关于他的事：" + "；".join(self.data["facts"]))
        if self.data["important_dates"]:
            lines.append("重要日子：" + "；".join(
                "%s（%s）" % (d["event"], d["date"]) for d in self.data["important_dates"]))
        if self.data["notes"]:
            lines.append("备注：" + self.data["notes"])
        return "\n".join(lines)

    # ---------- 聊天流水 ----------

    def append_history(self, role, content):
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"role": role, "content": content, "ts": int(time.time())},
                               ensure_ascii=False) + "\n")

    def recent_history(self, n):
        """读最近 n 条聊天记录（按时间正序返回）。"""
        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                lines = [ln for ln in f.read().splitlines() if ln.strip()]
        except FileNotFoundError:
            return []
        entries = []
        for ln in lines[-n:]:
            try:
                entries.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return entries

    # ---------- 记忆提炼 ----------

    def extract_and_merge(self, deepseek, history_slice):
        """对最近一段对话做一次记忆提炼并合并。返回是否更新了记忆。"""
        if not history_slice:
            return False
        existing = self.text() or "无"
        conversation = "\n".join(
            "%s：%s" % ("用户" if h["role"] == "user" else "管家", h["content"])
            for h in history_slice[-16:]
        )
        # 用 replace 而非 format：提示词里的 JSON 花括号会被 format 误当占位符
        prompt = (EXTRACT_PROMPT.replace("{existing}", existing)
                               .replace("{conversation}", conversation))
        raw = deepseek.chat(
            [{"role": "system", "content": "只输出 JSON，不要多余解释。"},
             {"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=512)
        result = self._parse_json(raw)
        if not result:
            return False
        changed = False
        changed |= self._merge_profile(result.get("profile"))
        changed |= self._merge_facts(result.get("new_facts"))
        changed |= self._merge_dates(result.get("dates"))
        if changed:
            self.save()
        return changed

    @staticmethod
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

    def _merge_profile(self, profile):
        if not isinstance(profile, dict):
            return False
        changed = False
        for k, v in profile.items():
            v = str(v).strip() if v is not None else ""
            if v and (k not in self.data["user_profile"]
                      or self.data["user_profile"].get(k) != v):
                self.data["user_profile"][k] = v
                changed = True
        return changed

    def _merge_facts(self, facts):
        if not isinstance(facts, list):
            return False
        changed = False
        for f in facts:
            f = str(f).strip()
            if not f or len(f) > 60:
                continue
            if self._is_dup(f):
                continue
            self.data["facts"].append(f)
            changed = True
        if len(self.data["facts"]) > 50:  # 滚动保留最近 50 条
            self.data["facts"] = self.data["facts"][-50:]
            changed = True
        return changed

    def _is_dup(self, new_fact):
        for old in self.data["facts"]:
            if new_fact == old:
                return True
            # 简单相似度：重叠字符占比 > 0.6 视为重复
            overlap = len(set(new_fact) & set(old))
            if overlap >= 0.6 * max(len(set(new_fact)), 1):
                return True
        return False

    def _merge_dates(self, dates):
        if not isinstance(dates, list):
            return False
        changed = False
        existing = {d["date"]: d["event"] for d in self.data["important_dates"]}
        for d in dates:
            if not isinstance(d, dict):
                continue
            date, event = str(d.get("date", "")).strip(), str(d.get("event", "")).strip()
            if not date or not event:
                continue
            if existing.get(date) != event:
                existing[date] = event
                changed = True
        self.data["important_dates"] = [
            {"date": k, "event": v} for k, v in sorted(existing.items())]
        return changed
