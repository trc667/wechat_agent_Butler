# -*- coding: utf-8 -*-
"""天气查询工具：wttr.in 免费接口（无需 key），带中文天气描述。

用法：
    text = fetch_weather("北京")   # 返回一句简短中文天气，失败返回 None
"""

import requests

# 国内服务走直连，显式禁用系统代理（避免被本地代理软件劫持）
_NO_PROXY = {"http": None, "https": None}

_WTTR = "https://wttr.in/%s?format=j1&lang=zh"

# 容易误当城市名的词（"今天天气"里"今天"不是城市）
_NOISE = {"今天", "明天", "后天", "昨天", "现在", "早上", "晚上", "最近", "这周", "下周"}

# 提取城市前要剥掉的引导动词（"查下上海天气"里的"查下"）
_VERB = ("帮我查一下", "帮我查", "帮我看看", "查一下", "查下", "查查",
         "看看", "看下", "请问", "我要看", "我想看")

# wttr.in lang=zh 有时不生效（返回英文描述），常见天气词翻中文
_WEATHER_ZH = {
    "Sunny": "晴", "Clear": "晴", "Partly cloudy": "多云",
    "Cloudy": "阴", "Overcast": "阴", "Light rain": "小雨",
    "Light rain shower": "小阵雨", "Moderate rain": "中雨",
    "Heavy rain": "大雨", "Thundery outbreaks possible": "雷阵雨",
    "Light snow": "小雪", "Moderate snow": "中雪", "Mist": "薄雾",
    "Fog": "雾", "Haze": "霾", "Windy": "大风",
    "Patchy rain possible": "零星小雨", "Light drizzle": "毛毛雨",
    "Moderate or heavy rain shower": "中到大雨",
    "Patchy rain nearby": "局部有雨", "Smoky haze": "烟霾",
}


def parse_weather(data, city):
    """把 wttr.in j1 JSON 解析成一句中文天气描述。纯函数，便于测试。"""
    try:
        cur = data["current_condition"][0]
        today = data["weather"][0]
    except (KeyError, IndexError):
        return None
    desc = (cur.get("weatherDesc") or [{}])[0].get("value", "").strip()  # 注意 wttr.in 可能带尾随空格
    desc = _WEATHER_ZH.get(desc, desc)  # 英文描述翻成中文
    temp = cur.get("temp_C", "?")
    feels = cur.get("FeelsLikeC", temp)
    humid = cur.get("humidity", "?")
    wind = cur.get("windspeedKmph", "?")
    maxt = today.get("maxtempC", "?")
    mint = today.get("mintempC", "?")
    return ("%s 当前 %s 度（%s，体感 %s 度），湿度 %s%%，风力 %s km/h；"
            "今日最高 %s / 最低 %s。"
            % (city, temp, desc, feels, humid, wind, maxt, mint))


def fetch_weather(city="北京", timeout=10):
    """查城市当前+今日天气。网络失败/解析失败返回 None（调用方降级）。"""
    try:
        r = requests.get(_WTTR % city, timeout=timeout, proxies=_NO_PROXY)
        r.raise_for_status()
        return parse_weather(r.json(), city)
    except Exception:
        return None


def extract_city(text, default="北京"):
    """从「北京天气」「查下上海天气」「今天天气」里提取城市名；提取不到用默认。"""
    import re
    text = text or ""
    for v in _VERB:  # 先剥掉引导动词，避免"查下上海"被当城市
        if text.startswith(v):
            text = text[len(v):]
            break
    m = re.search(r"([\u4e00-\u9fa5A-Za-z]{1,6}?)的?天气", text)  # 非贪婪：别把"的"吃进城市名
    if not m:
        return default
    city = m.group(1).strip()
    # 剥掉时间前缀（"今天深圳"->"深圳"；"今天天气"剥完为空用默认）
    for noise in ("今天", "明天", "后天", "昨天", "现在"):
        if city.startswith(noise):
            city = city[len(noise):]
            break
    if not city or city in _NOISE or len(city) > 4:
        return default
    return city
