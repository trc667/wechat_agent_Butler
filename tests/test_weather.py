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
