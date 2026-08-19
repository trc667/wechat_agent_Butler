# -*- coding: utf-8 -*-
"""Function Calling 工具注册表 + 分发器。

每个工具 = {name, description, parameters(schema), handler(ctx, args) -> str}。
加新功能 = 在 TOOLS 里加一条，模型就能自主调用，无需改关键词路由。

ctx 是 ToolContext：提供 .mgr（LifeManager，备忘录/待办/定时提醒）和 .cfg（配置）。
"""

import datetime
import json
import os
import re

from manager import LifeManager

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
EXPENSES_PATH = os.path.join(DATA_DIR, "expenses.json")

# 记账分类关键词映射（粗粒度够用）
_EXPENSE_CATEGORIES = [
    (("吃饭", "餐", "饭", "外卖", "早餐", "午餐", "晚餐", "奶茶", "咖啡"), "吃饭"),
    (("交通", "打车", "地铁", "公交", "高铁", "飞机", "加油"), "交通"),
    (("购物", "买", "淘宝", "京东", "衣服", "鞋"), "购物"),
    (("房租", "物业", "水电", "燃气"), "住房"),
]


def _categorize(note):
    note = note or ""
    for kws, cat in _EXPENSE_CATEGORIES:
        if any(k in note for k in kws):
            return cat
    return "其他"


def _load_expenses():
    try:
        with open(EXPENSES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"items": []}
    except (OSError, ValueError):
        return {"items": []}


def _save_expenses(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(EXPENSES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class ToolContext:
    """工具执行上下文：manager（数据）+ cfg（配置）。"""

    def __init__(self, mgr, cfg):
        self.mgr = mgr
        self.cfg = cfg


# ---------- 工具实现 ----------

def _memo_add(ctx, args):
    return ctx.mgr.add_memo_direct(args.get("text", ""))


def _memo_list(ctx, args):
    return ctx.mgr._hint_memos()


def _memo_delete(ctx, args):
    return ctx.mgr._del_memo(args.get("keyword", ""))


def _todo_add(ctx, args):
    return ctx.mgr.add_todo_direct(args.get("text", ""), args.get("due", ""))


def _todo_list(ctx, args):
    return ctx.mgr._hint_todos()


def _todo_done(ctx, args):
    return ctx.mgr._done_todo(args.get("keyword", ""))


def _timer_add(ctx, args):
    return ctx.mgr.add_timer_direct(args.get("at", ""), args.get("text", ""))


def _timer_list(ctx, args):
    return ctx.mgr._hint_timers()


def _timer_cancel(ctx, args):
    return ctx.mgr._del_timer(args.get("keyword", ""))


def _task_add(ctx, args):
    """定时任务（重复性）：每天/每周/一次。动作可指定天气推送。"""
    return ctx.mgr.add_task_direct(
        args.get("type", ""), args.get("text", ""),
        time=args.get("time", ""),
        weekday=args.get("weekday"),
        at=args.get("at", ""),
        action=args.get("action", "remind"))


def _task_list(ctx, args):
    return ctx.mgr._hint_tasks()


def _task_cancel(ctx, args):
    return ctx.mgr._del_task(args.get("keyword", ""))


def _weather_query(ctx, args):
    from weather import fetch_weather, fetch_weather_day, fetch_weather_week
    city = (args.get("city") or "").strip() or ctx.cfg.get("weather_city") or "北京"
    when = (args.get("when") or "today").strip()
    if when in ("明天", "明日", "tomorrow"):
        return fetch_weather_day(city, 1, "明日") or "暂时查不到 %s 明天的天气" % city
    if when in ("后天", "day_after"):
        return fetch_weather_day(city, 2, "后天") or "暂时查不到 %s 后天的天气" % city
    if when in ("这周", "一周", "未来", "week"):
        return fetch_weather_week(city, 3) or "暂时查不到 %s 未来几天的天气" % city
    return fetch_weather(city) or "暂时查不到 %s 的天气" % city


def _news_query(ctx, args):
    from news import fetch_news, load_history
    day = (args.get("day") or "today").strip()
    if day in ("今天", "今日", "latest", "today", ""):
        return fetch_news(5) or "新闻暂时抓不到，稍后再试"
    hist = load_history()
    names = {"周一": 0, "周二": 1, "周三": 2, "周四": 3,
             "周五": 4, "周六": 5, "周日": 6}
    key = None
    if day in names:
        idx = names[day]
        today = datetime.date.today()
        key = (today - datetime.timedelta(days=(today.weekday() - idx) % 7)).strftime("%Y-%m-%d")
    elif re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        key = day
    if key and key in hist:
        return hist[key]
    if key:
        return "%s 的新闻还没有存档" % day
    return fetch_news(5) or "新闻暂时抓不到，稍后再试"


def _expense_add(ctx, args):
    """记一笔开销：expense_add(amount, note)。返回确认文本。"""
    try:
        amount = float(args.get("amount", 0))
    except (TypeError, ValueError):
        return "金额格式不对，请提供数字"
    if amount <= 0:
        return "金额需要大于 0"
    note = (args.get("note") or "未备注").strip()
    cat = _categorize(note)
    data = _load_expenses()
    data["items"].append({"amount": amount, "category": cat, "note": note,
                          "date": datetime.date.today().strftime("%Y-%m-%d")})
    _save_expenses(data)
    return "已记一笔：%s %.2f 元（%s）" % (cat, amount, note)


def _expense_summary(ctx, args):
    """汇总开销：expense_summary(month 可选，默认本月)。"""
    month = (args.get("month") or "").strip()
    if not month:
        month = datetime.date.today().strftime("%Y-%m")
    data = _load_expenses()
    items = [it for it in data["items"] if (it.get("date") or "").startswith(month)]
    if not items:
        return "%s 还没有记账记录" % month
    total = sum(float(it.get("amount", 0)) for it in items)
    by_cat = {}
    for it in items:
        c = it.get("category") or "其他"
        by_cat[c] = by_cat.get(c, 0) + float(it.get("amount", 0))
    parts = "；".join("%s %.2f" % (c, v) for c, v in sorted(by_cat.items(), key=lambda x: -x[1]))
    return "%s 共 %d 笔，合计 %.2f 元：%s" % (month, len(items), total, parts)


# ---------- 工具清单（加新功能就在这里加一条） ----------

def _tool(name, description, params, handler):
    props = {k: {"type": v["type"], "description": v["description"]}
             for k, v in params.items()}
    required = [k for k, v in params.items() if v.get("required")]
    return {"type": "function",
            "function": {"name": name, "description": description,
                         "parameters": {"type": "object", "properties": props,
                                        "required": required}},
            "handler": handler}


def _p(t, desc, required=False):
    d = {"type": t, "description": desc}
    if required:
        d["required"] = True
    return d


TOOLS = [
    _tool("memo_add", "记住一条备忘录（用户说「记住XXX」时用）",
          {"text": _p("string", "要记住的内容", required=True)}, _memo_add),
    _tool("memo_list", "列出所有备忘录", {}, _memo_list),
    _tool("memo_delete", "删除一条备忘录（按关键词）",
          {"keyword": _p("string", "备忘录里的关键词", required=True)}, _memo_delete),
    _tool("todo_add", "记一条待办（用户说「记一下XX」时用）",
          {"text": _p("string", "待办事项", required=True),
           "due": _p("string", "截止日期 YYYY-MM-DD，可选")}, _todo_add),
    _tool("todo_list", "列出未完成的待办", {}, _todo_list),
    _tool("todo_done", "标记待办完成（按关键词）",
          {"keyword": _p("string", "待办里的关键词", required=True)}, _todo_done),
    _tool("timer_add", "设置一个定时提醒（用户说「X点提醒我XX」时用）",
          {"at": _p("string", "提醒时间 YYYY-MM-DD HH:MM，如 2026-08-20 15:00", required=True),
           "text": _p("string", "提醒内容", required=True)}, _timer_add),
    _tool("timer_list", "列出所有未触发的定时提醒", {}, _timer_list),
    _tool("timer_cancel", "取消一条定时提醒（按关键词）",
          {"keyword": _p("string", "提醒内容或时间里的关键词", required=True)}, _timer_cancel),
    _tool("task_add", "设置重复定时任务（用户说「每天早上X点做XX」「每周五X点提醒XX」时用）",
          {"type": _p("string", "daily=每天 / weekly=每周 / once=一次性", required=True),
           "text": _p("string", "任务内容，如 查天气 / 写周报", required=True),
           "time": _p("string", "时刻 HH:MM，如 09:00（daily/weekly 用）"),
           "weekday": _p("string", "周几 0-6，0=周一（weekly 用）"),
           "at": _p("string", "具体时间 YYYY-MM-DD HH:MM（once 用）"),
           "action": _p("string", "remind=推送提醒文本 / weather=推送天气数据，默认 remind")}, _task_add),
    _tool("task_list", "列出所有定时任务（含重复任务）", {}, _task_list),
    _tool("task_cancel", "取消一条定时任务（按内容关键词）",
          {"keyword": _p("string", "任务内容关键词，如 查天气", required=True)}, _task_cancel),
    _tool("weather_query", "查询天气（今天/明天/未来几天）",
          {"city": _p("string", "城市名，如 深圳；不填用默认城市"),
           "when": _p("string", "今天/明天/这周，默认今天")}, _weather_query),
    _tool("news_query", "看科技/AI 新闻（今天最新或回看某天的存档）",
          {"day": _p("string", "今天/周五/2026-08-14，默认今天")}, _news_query),
    _tool("expense_add", "记一笔开销（用户说「记一笔XX花多少钱」时用）",
          {"amount": _p("number", "金额，数字", required=True),
           "note": _p("string", "花在哪，如 中午吃饭")}, _expense_add),
    _tool("expense_summary", "汇总开销（本月或指定月份）",
          {"month": _p("string", "月份 YYYY-MM，如 2026-08；不填默认本月")}, _expense_summary),
]

_TOOLS_BY_NAME = {t["function"]["name"]: t for t in TOOLS}

# 发给 API 的 schema（剥离 handler 函数，否则 json 序列化失败）
TOOLS_SCHEMA = [{"type": t["type"], "function": t["function"]} for t in TOOLS]


def dispatch(name, arguments, ctx):
    """按工具名分发执行，返回文本结果。未知工具/异常兜底返回错误文本。"""
    tool = _TOOLS_BY_NAME.get(name)
    if not tool:
        return "未知工具：%s" % name
    try:
        return tool["handler"](ctx, arguments or {})
    except Exception as e:
        return "工具 %s 执行失败：%s" % (name, e)


def build_ctx(mgr, cfg):
    """构造工具上下文（bot 里用）。"""
    return ToolContext(mgr, cfg)
