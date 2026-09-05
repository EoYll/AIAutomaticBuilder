"""AppConfig：默认值、优先级（环境变量 > config.json > 默认）、密钥不落盘。"""

from __future__ import annotations

import json

from rpa_builder.config import AppConfig


def test_defaults(monkeypatch, tmp_path):
    monkeypatch.delenv("RPA_API_KEY", raising=False)
    monkeypatch.delenv("RPA_API_URL", raising=False)
    cfg = AppConfig.load(tmp_path / "missing.json")
    assert cfg.api_url == "https://api.deepseek.com/v1/chat/completions"
    assert cfg.api_key == ""
    assert cfg.model == "deepseek-chat"
    assert cfg.script_name == "MyAutomation"


def test_json_values_loaded(tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps({"api_url": "https://json.example", "model": "json-model", "script_name": "json-name"}),
        encoding="utf-8",
    )
    cfg = AppConfig.load(cfg_file)
    assert cfg.api_url == "https://json.example"
    assert cfg.model == "json-model"
    assert cfg.script_name == "json-name"


def test_env_overrides_json(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps({"api_url": "https://json.example", "model": "json-model", "script_name": "json-name"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("RPA_API_URL", "https://env.example")
    monkeypatch.setenv("RPA_API_KEY", "sk-env-secret")
    cfg = AppConfig.load(cfg_file)
    # 环境变量优先
    assert cfg.api_url == "https://env.example"
    assert cfg.api_key == "sk-env-secret"
    # 未设置环境变量的字段仍来自 JSON
    assert cfg.model == "json-model"


def test_api_key_never_persisted(tmp_path):
    cfg = AppConfig(api_key="sk-secret", model="m")
    out = tmp_path / "config.json"
    cfg.save(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "api_key" not in data
    assert data["model"] == "m"


def test_malformed_json_falls_back(tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text("{ not valid json", encoding="utf-8")
    cfg = AppConfig.load(cfg_file)
    assert cfg.api_url == "https://api.deepseek.com/v1/chat/completions"


def test_dotenv_loaded(monkeypatch, tmp_path):
    monkeypatch.delenv("RPA_API_KEY", raising=False)
    monkeypatch.delenv("RPA_MODEL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("RPA_API_KEY=sk-from-dotenv\nRPA_MODEL=dotenv-model\n", encoding="utf-8")
    cfg = AppConfig.load(tmp_path / "missing.json", env_file=env_file)
    assert cfg.api_key == "sk-from-dotenv"
    assert cfg.model == "dotenv-model"


def test_real_env_overrides_dotenv(monkeypatch, tmp_path):
    monkeypatch.setenv("RPA_API_KEY", "sk-real-env")
    env_file = tmp_path / ".env"
    env_file.write_text("RPA_API_KEY=sk-from-dotenv\n", encoding="utf-8")
    cfg = AppConfig.load(tmp_path / "missing.json", env_file=env_file)
    assert cfg.api_key == "sk-real-env"
