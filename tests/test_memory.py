# -*- coding: utf-8 -*-
"""memory.py 单元测试：JSON 容错解析、去重合并、记忆提炼全流程、存取回环。"""
from memory import Memory


class FakeDS:
    def __init__(self, raw):
        self.raw = raw

    def chat(self, messages, **kw):
        return self.raw


def _make_mem(tmp_path, raw=None):
    ds = FakeDS(raw) if raw is not None else None
    return Memory(ds, data_dir=str(tmp_path))


# ---------- JSON 容错解析 ----------

def test_parse_json_plain():
    assert Memory._parse_json('{"a": 1}') == {"a": 1}


def test_parse_json_fenced():
    assert Memory._parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_with_surrounding_text():
    assert Memory._parse_json('好的，结果如下：{"a": 1}。完毕') == {"a": 1}


def test_parse_json_invalid():
    assert Memory._parse_json("不是json") is None
    assert Memory._parse_json("{broken") is None
    assert Memory._parse_json("") is None
    assert Memory._parse_json(None) is None


# ---------- profile 合并 ----------

def test_merge_profile_add_and_update(tmp_path):
    mem = _make_mem(tmp_path)
    assert mem._merge_profile({"职业": "程序员"}) is True
    assert mem.data["user_profile"]["职业"] == "程序员"
    assert mem._merge_profile({"职业": "程序员"}) is False  # 相同值不变更
    assert mem._merge_profile({"职业": "产品经理"}) is True  # 更新
    assert mem.data["user_profile"]["职业"] == "产品经理"


def test_merge_profile_ignores_empty(tmp_path):
    mem = _make_mem(tmp_path)
    assert mem._merge_profile({"城市": "", "爱好": None}) is False
    assert "城市" not in mem.data["user_profile"]
    assert "爱好" not in mem.data["user_profile"]


def test_merge_profile_non_dict(tmp_path):
    mem = _make_mem(tmp_path)
    assert mem._merge_profile("not a dict") is False
    assert mem._merge_profile(None) is False


# ---------- facts 去重与滚动 ----------

def test_merge_facts_exact_dedup(tmp_path):
    mem = _make_mem(tmp_path)
    assert mem._merge_facts(["用户是程序员"]) is True
    assert mem._merge_facts(["用户是程序员"]) is False
    assert len(mem.data["facts"]) == 1


def test_merge_facts_similar_dedup(tmp_path):
    mem = _make_mem(tmp_path)
    assert mem._merge_facts(["用户是程序员，常用 Python"]) is True
    # 重叠字符占比 > 0.6 视为重复
    assert mem._merge_facts(["用户是程序员，常用 Python 写代码"]) is False
    assert len(mem.data["facts"]) == 1


def test_merge_facts_different_kept(tmp_path):
    mem = _make_mem(tmp_path)
    assert mem._merge_facts(["用户喜欢跑步"]) is True
    assert mem._merge_facts(["用户养了一只猫"]) is True
    assert len(mem.data["facts"]) == 2


def test_merge_facts_skip_too_long(tmp_path):
    mem = _make_mem(tmp_path)
    long_fact = "用" * 100
    assert mem._merge_facts([long_fact]) is False
    assert mem.data["facts"] == []


def test_merge_facts_rollover_50(tmp_path):
    mem = _make_mem(tmp_path)
    # 用真实长度的句子填满 50 条（短串如 "f5"/"f50" 会触发相似度误判，不适合本用例）
    mem.data["facts"] = ["事实%02d号记录" % i for i in range(50)]
    assert mem._merge_facts(["新增一条完全不同的长事实"]) is True
    assert len(mem.data["facts"]) == 50
    assert mem.data["facts"][-1] == "新增一条完全不同的长事实"
    assert "事实00号记录" not in mem.data["facts"]


def test_merge_facts_non_list(tmp_path):
    mem = _make_mem(tmp_path)
    assert mem._merge_facts("不是列表") is False


# ---------- dates 合并 ----------

def test_merge_dates(tmp_path):
    mem = _make_mem(tmp_path)
    assert mem._merge_dates([{"date": "08-05", "event": "生日"}]) is True
    assert mem._merge_dates([{"date": "08-05", "event": "生日"}]) is False  # 相同
    assert mem._merge_dates([{"date": "08-05", "event": "纪念日"}]) is True  # 更新
    assert mem.data["important_dates"][0]["event"] == "纪念日"


def test_merge_dates_skips_invalid(tmp_path):
    mem = _make_mem(tmp_path)
    assert mem._merge_dates([{"date": "", "event": "x"}]) is False
    assert mem._merge_dates([{"date": "08-05", "event": ""}]) is False
    assert mem._merge_dates("not a list") is False
    assert mem.data["important_dates"] == []


# ---------- text 渲染 ----------

def test_text_empty(tmp_path):
    assert _make_mem(tmp_path).text() == ""


def test_text_renders_all_sections(tmp_path):
    mem = _make_mem(tmp_path)
    mem._merge_profile({"职业": "程序员"})
    mem._merge_facts(["用户喜欢跑步"])
    mem._merge_dates([{"date": "01-01", "event": "元旦"}])
    text = mem.text()
    assert "档案：职业：程序员" in text
    assert "用户喜欢跑步" in text
    assert "元旦（01-01）" in text


# ---------- 记忆提炼全流程 ----------

def test_extract_and_merge_full_flow(tmp_path):
    raw = ('{"profile": {"职业": "程序员"}, "new_facts": ["用户常用 Claude Code"], "dates": []}')
    mem = Memory(FakeDS(raw), data_dir=str(tmp_path))
    mem.append_history("user", "我是程序员，常用 Claude Code 写代码")
    mem.append_history("assistant", "好的")
    assert mem.extract_and_merge(mem.deepseek, mem.recent_history(10)) is True
    assert mem.data["user_profile"].get("职业") == "程序员"
    assert "用户常用 Claude Code" in mem.data["facts"]


def test_extract_and_merge_empty_slice(tmp_path):
    mem = _make_mem(tmp_path)
    assert mem.extract_and_merge(mem.deepseek, []) is False


def test_extract_and_merge_bad_json(tmp_path):
    mem = Memory(FakeDS("模型吐了一堆废话没有 json"), data_dir=str(tmp_path))
    mem.append_history("user", "随便聊聊")
    assert mem.extract_and_merge(mem.deepseek, mem.recent_history(10)) is False
    assert mem.data["facts"] == []


# ---------- 历史与持久化 ----------

def test_history_roundtrip(tmp_path):
    mem = _make_mem(tmp_path)
    mem.append_history("user", "你好")
    mem.append_history("assistant", "你好呀")
    hist = mem.recent_history(10)
    assert len(hist) == 2
    assert hist[0]["role"] == "user"
    assert hist[1]["content"] == "你好呀"


def test_recent_history_respects_limit(tmp_path):
    mem = _make_mem(tmp_path)
    for i in range(20):
        mem.append_history("user", "消息%d" % i)
    hist = mem.recent_history(5)
    assert len(hist) == 5
    assert hist[0]["content"] == "消息15"
    assert hist[-1]["content"] == "消息19"


def test_save_load_roundtrip(tmp_path):
    mem = _make_mem(tmp_path)
    mem._merge_facts(["某条持久事实"])
    mem.save()
    mem2 = Memory(None, data_dir=str(tmp_path))
    assert mem2.data["facts"] == ["某条持久事实"]
