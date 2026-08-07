"""DeepSeek API 封装：非流式对话调用，失败自动重试（指数退避）。"""

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
        """调用对话接口，返回助手回复文本。"""
        if not self.api_key:
            raise DeepSeekError("未配置 DeepSeek API key，请在 config.json 里填写 deepseek_api_key")

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
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
                    # 模型名不存在时报错提示（deepseek-chat 已弃用，默认应为 deepseek-v4-flash）
                    raise DeepSeekError(
                        "模型名可能不存在（404），请检查 config.json 里的 model，当前为: %s" % self.model
                    )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                return content
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
