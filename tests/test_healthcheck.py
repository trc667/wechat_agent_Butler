# -*- coding: utf-8 -*-
"""healthcheck.py 单元测试：自检项判定与汇总输出。"""
import healthcheck


def _full_cfg():
    return {"deepseek_api_key": "sk-x", "dashscope_api_key": "sk-y",
            "weather_city": "北京"}


def test_all_ok(monkeypatch, capsys):
    monkeypatch.setattr(healthcheck, "_file_exists", lambda rel: True)
    rows = healthcheck.run_health_check(_full_cfg())
    assert len(rows) == 6
    assert all(ok for _, ok, _ in rows)
    out = capsys.readouterr().out
    assert "启动自检" in out
    assert "就绪 6/6" in out


def test_missing_keys(monkeypatch, capsys):
    monkeypatch.setattr(healthcheck, "_file_exists", lambda rel: True)
    cfg = {"deepseek_api_key": "", "dashscope_api_key": "",
           "weather_city": ""}
    rows = healthcheck.run_health_check(cfg)
    names = {n: ok for n, ok, _ in rows}
    assert names["DeepSeek API key"] is False
    assert names["识图模型（阿里云百炼）"] is False
    assert names["天气默认城市"] is False
    out = capsys.readouterr().out
    assert "就绪 3/6" in out


def test_missing_files(monkeypatch):
    monkeypatch.setattr(healthcheck, "_file_exists", lambda rel: False)
    rows = healthcheck.run_health_check(_full_cfg())
    names = {n: ok for n, ok, _ in rows}
    assert names["微信登录凭据"] is False
    assert names["会话令牌（主动推送）"] is False
    assert names["DeepSeek API key"] is True
