"""配置文件加载：统一从 config.json 读取，键缺失时用默认值兜底。

密钥优先级：环境变量 > .env 文件（DEEPSEEK_API_KEY 等）> config.json。
config.json 入库时只留占位符，真实密钥放 .env（已被 .gitignore 排除）。
"""

import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
DOTENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

DEFAULTS = {
    "deepseek_api_key": "",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash",
    "max_tokens": 1024,
    "temperature": 1.0,
    "allow_list": [],
    "admin_userid": "",          # 你的企微成员ID（定时问候没人聊过时发给ta）
    "min_reply_interval": 3,
    "history_keep": 12,
    "memory_extract_every": 5,
    "weather_city": "北京",   # 天气查询的默认城市（说「今天天气」时用）
    "dashscope_api_key": "",   # 识图（阿里云百炼 qwen-vl-plus，.env 的 DASHSCOPE_API_KEY）
    "dashscope_model": "qwen-vl-plus",
    "dashscope_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "daily_greeting": {
        "enabled": True,
        "time": "09:00",
        "text": "早安宝贝～今天也要元气满满哦 ☀️",
    },
    "clock_reminders": {   # 定时提醒（如上下班打卡），每天每个时间点推一次
        "enabled": True,
        "times": [
            {"time": "08:25", "text": "上班打卡时间到，记得打卡哦"},
            {"time": "12:00", "text": "中午下班啦，记得打卡再休息"},
            {"time": "13:25", "text": "下午上班时间到，记得打卡"},
            {"time": "18:00", "text": "下班时间到，记得打卡"},
        ],
    },
    "wecom": {
        "corpid": "",            # 企业ID（管理后台 -> 我的企业）
        "agentid": "",           # 自建应用ID
        "secret": "",            # 自建应用密钥
        "token": "",             # 回调配置里的 Token（自己随便填的随机串）
        "encoding_aes_key": "",  # 回调配置里的 EncodingAESKey（43位随机串）
        "mode": "safe",          # safe=安全模式 / plain=明文模式
        "callback_port": 9000,   # 本地回调端口（内网穿透转发到这里）
        "callback_path": "/wechat/callback",
        "api_base": "https://qyapi.weixin.qq.com/cgi-bin",
    },
}

_cached = None


def _load_dotenv(path):
    """读取 .env 文件（每行 KEY=VALUE，忽略空行和 # 注释），返回 dict。
    文件不存在或解析失败返回 {}。"""
    env = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


def load_config(force=False):
    """读取并缓存配置；config.json 缺失或字段缺失时用默认值。
    密钥从 .env/环境变量读取，优先于 config.json（config.json 只留占位符）。"""
    global _cached
    if _cached is not None and not force:
        return _cached
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
        if isinstance(user_cfg, dict):
            for k, v in user_cfg.items():
                cfg[k] = v
    except (FileNotFoundError, json.JSONDecodeError):
        pass  # 缺文件或损坏就全用默认值
    # daily_greeting / clock_reminders / wecom 允许部分填写（深合并，先复制一份避免污染 DEFAULTS）
    for section in ("daily_greeting", "clock_reminders", "wecom"):
        merged = dict(DEFAULTS[section])
        if isinstance(cfg.get(section), dict):
            merged.update(cfg[section])
        cfg[section] = merged
    # 密钥优先取环境变量，其次 .env 文件（config.json 里的占位符不生效）
    env = _load_dotenv(DOTENV_PATH)
    for key, env_name in (("deepseek_api_key", "DEEPSEEK_API_KEY"),
                          ("base_url", "DEEPSEEK_BASE_URL"),
                          ("model", "DEEPSEEK_MODEL")):
        if env.get(env_name):
            cfg[key] = env[env_name]
        # 系统环境变量优先级最高（可临时覆盖 .env，比如 CI 里注入密钥）
        if os.environ.get(env_name):
            cfg[key] = os.environ[env_name]
    # wecom 主动发送只需 corpid/agentid/secret，同样支持 .env / 环境变量
    for key, env_name in (("corpid", "WECOM_CORPID"),
                          ("agentid", "WECOM_AGENTID"),
                          ("secret", "WECOM_SECRET")):
        val = env.get(env_name) or os.environ.get(env_name)
        if val:
            cfg["wecom"][key] = val
    # 识图（阿里云百炼）密钥/模型同样支持 .env / 环境变量
    for key, env_name in (("dashscope_api_key", "DASHSCOPE_API_KEY"),
                          ("dashscope_model", "DASHSCOPE_MODEL"),
                          ("dashscope_base_url", "DASHSCOPE_BASE_URL")):
        val = env.get(env_name) or os.environ.get(env_name)
        if val:
            cfg[key] = val
    _cached = cfg
    return cfg


def api_key_valid():
    key = load_config().get("deepseek_api_key", "")
    return bool(key) and "粘贴" not in key
