# -*- coding: utf-8 -*-
"""deepseek.py 工具调用（Function Calling）单元测试：tool_calls 解析、多轮回传、超轮兜底。"""
import deepseek
from deepseek import DeepSeek


def _make_ds():
    return DeepSeek(api_key="sk-test", base_url="https://api.deepseek.com", model="test-model")


def _fake_post(monkeypatch, responses):
    """按顺序返回预设响应的 requests.post mock。"""
    def fake_post(url, headers=None, json=None, **kw):
        resp = responses.pop(0)

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return resp

        return R()

    monkeypatch.setattr(deepseek.requests, "post", fake_post)


def _tool_call_msg(name, args, call_id="call_1"):
    return {"choices": [{"message": {"role": "assistant", "content": None,
                                     "tool_calls": [
                                         {"id": call_id, "type": "function",
                                          "function": {"name": name,
                                                       "arguments": args}}]}}]}


def test_chat_with_tools_returns_tool_calls(monkeypatch):
    _fake_post(monkeypatch, [_tool_call_msg("weather_query", '{"city": "深圳"}')])
    msg = _make_ds().chat_with_tools(
        [{"role": "user", "content": "天气"}], [{"type": "function"}])
    assert msg["tool_calls"][0]["function"]["name"] == "weather_query"


def test_chat_with_tools_plain_reply(monkeypatch):
    _fake_post(monkeypatch, [{"choices": [{"message": {"role": "assistant",
                                                      "content": "你好呀"}}]}])
    msg = _make_ds().chat_with_tools(
        [{"role": "user", "content": "你好"}], [{"type": "function"}])
    assert msg.get("content") == "你好呀"
    assert not msg.get("tool_calls")


def test_run_tool_loop_executes_and_returns_final(monkeypatch):
    """模型先调工具，拿到结果后再回复文本：dispatcher 被执行，结果回传。"""
    calls = []
    responses = [
        _tool_call_msg("weather_query", '{"city": "深圳"}'),
        {"choices": [{"message": {"role": "assistant", "content": "深圳今天 29 度，记得带伞。"}}]},
    ]
    _fake_post(monkeypatch, responses)

    def dispatcher(name, args):
        calls.append((name, args))
        return "深圳 29 度 小雨"

    out = _make_ds().run_tool_loop(
        [{"role": "user", "content": "深圳天气怎么样"}], [{"type": "function"}], dispatcher)
    assert out == "深圳今天 29 度，记得带伞。"
    assert calls == [("weather_query", {"city": "深圳"})]


def test_run_tool_loop_multi_tools_sequential(monkeypatch):
    """连续两个工具调用都执行（如查天气 + 记备忘）。"""
    calls = []
    responses = [
        _tool_call_msg("weather_query", '{"city": "深圳"}', "call_1"),
        _tool_call_msg("memo_add", '{"text": "深圳夏天热"}', "call_2"),
        {"choices": [{"message": {"role": "assistant", "content": "都处理好了。"}}]},
    ]
    _fake_post(monkeypatch, responses)

    def dispatcher(name, args):
        calls.append((name, args))
        return "ok"

    _make_ds().run_tool_loop([{"role": "user", "content": "查天气并记备忘"}],
                             [{"type": "function"}], dispatcher)
    assert calls == [("weather_query", {"city": "深圳"}),
                     ("memo_add", {"text": "深圳夏天热"})]


def test_run_tool_loop_max_rounds_fallback(monkeypatch):
    """模型一直想调工具（超 max_rounds）→ 兜底再问一次纯文本，不卡死。"""
    # max_rounds=3：循环 3 次都是 tool_call，兜底第 4 次返回文本
    responses = [
        _tool_call_msg("weather_query", "{}", "c%d" % i) for i in range(3)
    ] + [{"choices": [{"message": {"role": "assistant", "content": "兜底回复"}}]}]
    _fake_post(monkeypatch, responses)
    out = _make_ds().run_tool_loop(
        [{"role": "user", "content": "x"}], [{"type": "function"}],
        lambda n, a: "结果", max_rounds=3)
    assert out == "兜底回复"


def test_run_tool_loop_dispatcher_exception_survives(monkeypatch):
    """dispatcher 抛异常 → 异常文本回传，循环继续。"""
    responses = [
        _tool_call_msg("memo_add", "{}"),
        {"choices": [{"message": {"role": "assistant", "content": "已处理"}}]},
    ]
    _fake_post(monkeypatch, responses)

    def dispatcher(name, args):
        raise RuntimeError("boom")

    out = _make_ds().run_tool_loop([{"role": "user", "content": "x"}],
                                   [{"type": "function"}], dispatcher)
    assert out == "已处理"
