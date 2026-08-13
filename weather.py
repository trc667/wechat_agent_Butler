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
    "Patchy light drizzle": "零星毛毛雨",
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


def parse_weather_day(data, city, index=1, label="明日"):
    """解析未来第 index 天（1=明天）的天气：label + 天气 + 最高/最低。纯函数。"""
    try:
        day = data["weather"][index]
    except (KeyError, IndexError):
        return None
    hourly = day.get("hourly") or []
    desc = ""
    if hourly:  # 取中午前后一条作为当天代表描述
        mid = hourly[len(hourly) // 2]
        desc = (mid.get("weatherDesc") or [{}])[0].get("value", "").strip()
        desc = _WEATHER_ZH.get(desc, desc)
    maxt = day.get("maxtempC", "?")
    mint = day.get("mintempC", "?")
    if desc:
        return "%s%s %s，最高 %s / 最低 %s" % (label, city, desc, maxt, mint)
    return "%s%s 最高 %s / 最低 %s" % (label, city, maxt, mint)


def fetch_weather(city="北京", timeout=10):
    """查城市当前+今日天气。网络失败/解析失败返回 None（调用方降级）。"""
    try:
        r = requests.get(_WTTR % city, timeout=timeout, proxies=_NO_PROXY)
        r.raise_for_status()
        return parse_weather(r.json(), city)
    except Exception:
        return None


def fetch_weather_day(city="北京", index=1, label="明日", timeout=10):
    """查未来第 index 天（1=明天）天气。失败返回 None。"""
    try:
        r = requests.get(_WTTR % city, timeout=timeout, proxies=_NO_PROXY)
        r.raise_for_status()
        return parse_weather_day(r.json(), city, index, label)
    except Exception:
        return None


def fetch_weather_week(city="北京", days=3, timeout=10):
    """查未来 N 天（明天起）天气趋势，返回多行文本。失败返回 None。"""
    try:
        r = requests.get(_WTTR % city, timeout=timeout, proxies=_NO_PROXY)
        r.raise_for_status()
        data = r.json()
        lines = []
        for i in range(1, days + 1):
            if i == 1:
                label = "明日"
            elif i == 2:
                label = "后天"
            else:
                try:
                    d = data["weather"][i].get("date", "")
                    label = (d[5:7] + "月" + d[8:10] + "日") if len(d) >= 10 else "第%d天" % (i + 1)
                except (KeyError, IndexError):
                    label = "第%d天" % (i + 1)
            line = parse_weather_day(data, city, i, label)
            if line:
                lines.append(line)
        if not lines:
            return None
        return "未来天气：\n" + "\n".join(lines)
    except Exception:
        return None


# 雨/雪/雷关键词（中英都要，wttr.in 可能返回英文描述）
_RAIN_WORDS = ("雨", "雪", "雷", "rain", "snow", "thunder", "shower", "drizzle")


def fetch_weather_alert(city="北京", timeout=10):
    """查今天是否有雨/雪/高温：有则返回一条提醒语（如「今天有雨记得带伞」），
    没有或查询失败返回 None。供早上定时提醒用。"""
    try:
        r = requests.get(_WTTR % city, timeout=timeout, proxies=_NO_PROXY)
        r.raise_for_status()
        data = r.json()
        today = data["weather"][0]
        hourly = today.get("hourly") or []
        descs, temps = [], []
        for h in hourly:
            d = (h.get("weatherDesc") or [{}])[0].get("value", "").strip()
            if d:
                descs.append(d)
            try:
                temps.append(int(h.get("tempC", "0")))
            except (TypeError, ValueError):
                pass
        for d in descs:
            if any(w in d.lower() for w in _RAIN_WORDS):
                desc = _WEATHER_ZH.get(d, d)
                return "今天%s天气%s，出门记得带伞" % (city, desc)
        maxt = max(temps) if temps else 0
        if maxt >= 35:
            return "今天%s最高 %d 度，注意防暑多喝水" % (city, maxt)
        return None
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
