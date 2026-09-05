"""配置加载：环境变量 > config.json > 内置默认值。

安全约定：
- API Key 通过环境变量 ``RPA_API_KEY`` 注入，**绝不写入任何磁盘文件**；
- ``config.json`` 仅保存非敏感项（api_url / model / script_name），供 GUI 记忆偏好；
- 读取优先级设计为“环境变量优先”，便于多环境切换，也防止密钥在 JSON 中落盘。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 项目根目录（src/rpa_builder/config.py -> 项目根）
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_FILE = BASE_DIR / "config.json"

DEFAULTS: dict[str, str] = {
    "api_url": "https://api.deepseek.com/v1/chat/completions",
    "api_key": "",
    "model": "deepseek-chat",
    "script_name": "MyAutomation",
}

# 允许写回 config.json 的非敏感键（api_key 永远不在其中）
_NON_SECRET_KEYS = ("api_url", "model", "script_name")

# 环境变量前缀：RPA_API_URL / RPA_API_KEY / RPA_MODEL / RPA_SCRIPT_NAME
_ENV_PREFIX = "RPA_"


def _env_value(key: str) -> str:
    """读取 ``RPA_<KEY>`` 环境变量；未设置返回空串。"""
    return os.environ.get(_ENV_PREFIX + key.upper(), "").strip()


def _load_dotenv(env_file: Path) -> None:
    """加载 ``.env`` 文件（已存在的同名环境变量优先，不被覆盖）。

    仅解析 ``KEY=value`` 行，忽略注释与空行；值支持去除首尾引号。
    这样 ``.env`` 可被 gitignore，密钥安全存放，同时系统环境变量仍然优先。
    """
    try:
        with open(env_file, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


@dataclass
class AppConfig:
    """构建器配置聚合。

    用法::

        cfg = AppConfig.load()          # 默认 -> config.json -> 环境变量
        cfg.api_key = "..."             # 仅内存，不落盘
        cfg.save()                      # 仅写回非敏感键
    """

    api_url: str = DEFAULTS["api_url"]
    api_key: str = DEFAULTS["api_key"]
    model: str = DEFAULTS["model"]
    script_name: str = DEFAULTS["script_name"]

    @classmethod
    def load(cls, config_file: str | Path | None = None, env_file: str | Path | None = None) -> AppConfig:
        """按 默认值 -> config.json -> .env -> 环境变量 的顺序加载配置。

        ``env_file`` 默认为项目根目录 ``.env``（不存在则跳过）。
        """
        _load_dotenv(Path(env_file or BASE_DIR / ".env"))
        cfg = cls()
        cfg._merge_file(Path(config_file or DEFAULT_CONFIG_FILE))
        cfg._merge_env()
        return cfg

    def _merge_file(self, path: Path) -> None:
        try:
            with open(path, encoding="utf-8") as f:
                data: Any = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        for key in _NON_SECRET_KEYS:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                setattr(self, key, value.strip())

    def _merge_env(self) -> None:
        # 环境变量优先，覆盖 config.json 中的同名项；api_key 只可能来自环境变量
        for key in (*_NON_SECRET_KEYS, "api_key"):
            value = _env_value(key)
            if value:
                setattr(self, key, value)

    def save(self, config_file: str | Path | None = None) -> None:
        """仅将非敏感字段写回 config.json（api_key 永不落盘）。"""
        path = Path(config_file or DEFAULT_CONFIG_FILE)
        data = {key: getattr(self, key) for key in _NON_SECRET_KEYS}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print(f"⚠️ 保存配置文件失败: {e}")
