# -*- coding: utf-8 -*-
"""config.py 单元测试：默认值兜底、config.json 合并、.env 覆盖、环境变量优先、深合并。"""
import json

import config as config_mod


def _reset():
    config_mod._cached = None


def test_defaults_when_no_files(monkeypatch, tmp_path):
    _reset()
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(tmp_path / "no.json"))
    monkeypatch.setattr(config_mod, "DOTENV_PATH", str(tmp_path / "no.env"))
    cfg = config_mod.load_config()
    assert cfg["deepseek_api_key"] == ""
    assert cfg["base_url"] == "https://api.deepseek.com"
    assert cfg["model"] == "deepseek-v4-flash"
    assert cfg["allow_list"] == []
    assert cfg["min_reply_interval"] == 3
    assert cfg["wecom"]["mode"] == "safe"
    assert cfg["daily_greeting"]["enabled"] is True


def test_json_overrides_defaults(monkeypatch, tmp_path):
    _reset()
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"model": "custom-model", "max_tokens": 512}),
                        encoding="utf-8")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(cfg_file))
    monkeypatch.setattr(config_mod, "DOTENV_PATH", str(tmp_path / "no.env"))
    cfg = config_mod.load_config()
    assert cfg["model"] == "custom-model"
    assert cfg["max_tokens"] == 512
    assert cfg["base_url"] == "https://api.deepseek.com"  # 未覆盖的仍用默认


def test_dotenv_fills_secret(monkeypatch, tmp_path):
    _reset()
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"deepseek_api_key": ""}), encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nDEEPSEEK_API_KEY=sk-test-123\nDEEPSEEK_MODEL=model-x\n",
                        encoding="utf-8")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(cfg_file))
    monkeypatch.setattr(config_mod, "DOTENV_PATH", str(env_file))
    cfg = config_mod.load_config()
    assert cfg["deepseek_api_key"] == "sk-test-123"
    assert cfg["model"] == "model-x"


def test_dotenv_beats_json_placeholder(monkeypatch, tmp_path):
    _reset()
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"deepseek_api_key": "old-placeholder"}), encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(cfg_file))
    monkeypatch.setattr(config_mod, "DOTENV_PATH", str(env_file))
    cfg = config_mod.load_config()
    assert cfg["deepseek_api_key"] == "from-dotenv"


def test_env_var_beats_dotenv(monkeypatch, tmp_path):
    _reset()
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"deepseek_api_key": "from-json"}), encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(cfg_file))
    monkeypatch.setattr(config_mod, "DOTENV_PATH", str(env_file))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
    cfg = config_mod.load_config()
    assert cfg["deepseek_api_key"] == "from-env"


def test_broken_json_falls_back_to_defaults(monkeypatch, tmp_path):
    _reset()
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text("{ 不是合法json ", encoding="utf-8")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(cfg_file))
    monkeypatch.setattr(config_mod, "DOTENV_PATH", str(tmp_path / "no.env"))
    cfg = config_mod.load_config()
    assert cfg["model"] == "deepseek-v4-flash"


def test_wecom_partial_merge(monkeypatch, tmp_path):
    _reset()
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"wecom": {"corpid": "ww123"}}), encoding="utf-8")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(cfg_file))
    monkeypatch.setattr(config_mod, "DOTENV_PATH", str(tmp_path / "no.env"))
    cfg = config_mod.load_config()
    assert cfg["wecom"]["corpid"] == "ww123"
    assert cfg["wecom"]["mode"] == "safe"        # 深合并：没填的用默认
    assert cfg["wecom"]["callback_port"] == 9000


def test_api_key_valid_with_dotenv(monkeypatch, tmp_path):
    _reset()
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"deepseek_api_key": ""}), encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=sk-valid-key\n", encoding="utf-8")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(cfg_file))
    monkeypatch.setattr(config_mod, "DOTENV_PATH", str(env_file))
    assert config_mod.api_key_valid() is True


def test_api_key_invalid_without_secret(monkeypatch, tmp_path):
    _reset()
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"deepseek_api_key": ""}), encoding="utf-8")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(cfg_file))
    monkeypatch.setattr(config_mod, "DOTENV_PATH", str(tmp_path / "no.env"))
    assert config_mod.api_key_valid() is False
