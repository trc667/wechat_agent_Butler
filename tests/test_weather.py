# -*- coding: utf-8 -*-
"""weather.py 单元测试：JSON 解析、城市名提取、网络调用降级。"""
import weather


SAMPLE = {
    "current_condition": [{
        "temp_C": "29", "FeelsLikeC": "30", "humidity": "60",
        "windspeedKmph": "12", "weatherDesc": [{"value": "晴"}],
    }],
    "weather": [{"maxtempC": "33", "mintempC": "24"}],
}


def test_parse_weather_ok():
    text = weather.parse_weather(SAMPLE, "北京")
    assert text is not None
    assert "北京 当前 29 度" in text
    assert "晴" in text and "湿度 60%" in text
    assert "今日最高 33 / 最低 24" in text


def test_parse_weather_english_desc_translated():
    # wttr.in 可能返回英文描述且带尾随空格，需翻译成中文
    data = {"current_condition": [{"temp_C": "29", "weatherDesc": [{"value": "Clear "}]}],
            "weather": [{"maxtempC": "33", "mintempC": "24"}]}
    text = weather.parse_weather(data, "深圳")
    assert text is not None and "晴" in text
    assert "Clear" not in text


# ---------- 明日 / 未来 N 天 / 预警 ----------

DAY_DATA = {"weather": [
    {"maxtempC": "33", "mintempC": "26",
     "hourly": [{"tempC": "30", "weatherDesc": [{"value": "Clear"}]},
                {"tempC": "32", "weatherDesc": [{"value": "Cloudy"}]}]},
    {"maxtempC": "31", "mintempC": "25", "date": "2026-08-07",
     "hourly": [{"tempC": "29", "weatherDesc": [{"value": "Light rain"}]}]},
    {"maxtempC": "30", "mintempC": "24", "date": "2026-08-08",
     "hourly": [{"tempC": "28", "weatherDesc": [{"value": "Sunny"}]}]},
]}


def test_parse_weather_day_tomorrow():
    text = weather.parse_weather_day(DAY_DATA, "深圳", 1, "明日")
    assert text == "明日深圳 小雨，最高 31 / 最低 25"


def test_parse_weather_day_out_of_range():
    assert weather.parse_weather_day(DAY_DATA, "深圳", 9) is None
    assert weather.parse_weather_day({}, "深圳") is None


def test_fetch_weather_day_success(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return DAY_DATA

    monkeypatch.setattr(weather.requests, "get", lambda *a, **k: FakeResp())
    assert weather.fetch_weather_day("深圳", 1) == "明日深圳 小雨，最高 31 / 最低 25"


def test_fetch_weather_week(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return DAY_DATA

    monkeypatch.setattr(weather.requests, "get", lambda *a, **k: FakeResp())
    text = weather.fetch_weather_week("深圳", days=2)
    assert text is not None and text.startswith("未来天气：")
    assert "明日深圳" in text and "后天深圳" in text


ALERT_RAIN = {"weather": [{"hourly": [
    {"tempC": "26", "weatherDesc": [{"value": "Light rain"}]}]}]}
ALERT_HOT = {"weather": [{"hourly": [
    {"tempC": "36", "weatherDesc": [{"value": "Sunny"}]}]}]}
ALERT_FINE = {"weather": [{"hourly": [
    {"tempC": "28", "weatherDesc": [{"value": "Clear"}]}]}]}


def _alert(data):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return data

    return lambda *a, **k: FakeResp()


def test_fetch_weather_alert_rain(monkeypatch):
    monkeypatch.setattr(weather.requests, "get", _alert(ALERT_RAIN))
    alert = weather.fetch_weather_alert("深圳")
    assert alert is not None and "带伞" in alert


def test_fetch_weather_alert_hot(monkeypatch):
    monkeypatch.setattr(weather.requests, "get", _alert(ALERT_HOT))
    alert = weather.fetch_weather_alert("深圳")
    assert alert is not None and "防暑" in alert


def test_fetch_weather_alert_fine(monkeypatch):
    monkeypatch.setattr(weather.requests, "get", _alert(ALERT_FINE))
    assert weather.fetch_weather_alert("深圳") is None


def test_parse_weather_missing_fields():
    assert weather.parse_weather({}, "北京") is None
    assert weather.parse_weather({"current_condition": []}, "北京") is None


def test_extract_city_explicit():
    assert weather.extract_city("北京天气", "上海") == "北京"
    assert weather.extract_city("查下上海天气", "北京") == "上海"
    assert weather.extract_city("广州的天气怎么样", "北京") == "广州"


def test_extract_city_with_time_prefix():
    # 「查查今天深圳的天气」：时间词不是城市的一部分
    assert weather.extract_city("查查今天深圳的天气", "北京") == "深圳"
    assert weather.extract_city("明天上海的天气", "北京") == "上海"
    assert weather.extract_city("查查今天天气", "北京") == "北京"  # 剥完为空用默认


def test_extract_city_noise_uses_default():
    assert weather.extract_city("今天天气", "北京") == "北京"
    assert weather.extract_city("明天天气怎么样", "北京") == "北京"
    assert weather.extract_city("早上天气", "北京") == "北京"


def test_extract_city_no_keyword_uses_default():
    assert weather.extract_city("你好呀", "北京") == "北京"
    assert weather.extract_city("", "北京") == "北京"


def test_fetch_weather_success(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return SAMPLE

    monkeypatch.setattr(weather.requests, "get", lambda *a, **k: FakeResp())
    text = weather.fetch_weather("北京")
    assert text is not None and "北京" in text


def test_fetch_weather_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("网络错误")

    monkeypatch.setattr(weather.requests, "get", boom)
    assert weather.fetch_weather("北京") is None
