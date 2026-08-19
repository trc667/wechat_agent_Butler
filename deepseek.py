"""DeepSeek API 封装：非流式对话调用，失败自动重试（指数退避）。

支持 Function Calling（tools）：模型可自主决定调用工具，见 chat_with_tools / run_tool_loop。
"""

import json
import time

import requests

from config import load_config

# 国内服务走直连，显式禁用系统代理（避免被本地代理软件劫持）
_NO_PROXY = {"http": None, "https": None}


class DeepSeekError(Exception):
    pass


class DeepSeek:
    def __init__(self, api_key=None, base_url=None, model=None):
        cfg = load_config()
        self.api_key = api_key or cfg.get("deepseek_api_key", "")
        self.base_url = (base_url or cfg.get("base_url", "https://api.deepseek.com")).rstrip("/")
        self.model = model or cfg.get("model", "deepseek-v4-flash")
        self.max_tokens = cfg.get("max_tokens", 1024)
        self.temperature = cfg.get("temperature", 1.0)

    def chat(self, messages, temperature=None, max_tokens=None, max_retries=2):
        """调用对话接口，返回助手回复文本。

        兼容旧用法：记忆提炼/待办提取等内部小调用继续用这个（无工具）。
        """
        data = self._post_chat(messages, tools=None,
                               temperature=temperature, max_tokens=max_tokens,
                               max_retries=max_retries)
        return data["choices"][0]["message"]["content"].strip()

    def chat_with_tools(self, messages, tools, temperature=None, max_tokens=None,
                        max_retries=2):
        """带工具列表的对话调用。返回完整 assistant message（dict）。

        可能含 "tool_calls"（模型想调工具）或只有 "content"（普通回复）。
        """
        data = self._post_chat(messages, tools=tools,
                               temperature=temperature, max_tokens=max_tokens,
                               max_retries=max_retries)
        return data["choices"][0]["message"]

    def run_tool_loop(self, messages, tools, dispatcher, max_rounds=4, **kw):
        """Function Calling 主循环：调模型 -> 有 tool_calls 就分发执行 -> 回传结果 -> 再调。

        直到模型输出纯文本（或超轮数兜底返回已有内容）。
        dispatcher(name, arguments) -> str，执行工具返回文本结果。
        返回最终回复文本。
        """
        msgs = list(messages)
        for _ in range(max_rounds):
            msg = self.chat_with_tools(msgs, tools, **kw)
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                return (msg.get("content") or "").strip()
            # 把模型的工具调用意图加进对话
            msgs.append(msg)
            for tc in tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                try:
                    result = dispatcher(name, args)
                except Exception as e:
                    result = "工具执行失败: %s" % e
                msgs.append({"role": "tool", "tool_call_id": tc.get("id") or "",
                             "content": result})
        # 超轮数：用最后一轮上下文再问一次纯文本
        msg = self.chat_with_tools(msgs, tools=None, **kw)
        return (msg.get("content") or "").strip()

    def _post_chat(self, messages, tools=None, temperature=None, max_tokens=None,
                   max_retries=2):
        """底层：组装 payload 并带重试地 POST，返回完整响应 JSON。"""
        if not self.api_key:
            raise DeepSeekError("未配置 DeepSeek API key，请在 config.json 里填写 deepseek_api_key")

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.api_key,
        }
        url = self.base_url + "/chat/completions"

        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=90,
                                     proxies=_NO_PROXY)
                if resp.status_code == 401:
                    raise DeepSeekError(
                        "API key 无效（401），请到 DeepSeek 开放平台检查 deepseek_api_key"
                    )
                if resp.status_code == 404:
                    raise DeepSeekError(
                        "模型名可能不存在（404），请检查 config.json 里的 model，当前为: %s" % self.model
                    )
                resp.raise_for_status()
                return resp.json()
            except DeepSeekError:
                raise
            except (requests.RequestException, KeyError, ValueError) as e:
                if attempt >= max_retries:
                    raise DeepSeekError("DeepSeek 调用失败（已重试 %d 次）: %s" % (max_retries, e))
                time.sleep(2 ** (attempt + 1))

        raise DeepSeekError("DeepSeek 调用失败")  # 理论不可达

    def ping(self):
        """启动自检：花几个 token 确认 key 和模型可用。"""
        self.chat([{"role": "user", "content": "回复'在线'两个字即可"}], max_tokens=10)
