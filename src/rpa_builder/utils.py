"""通用工具函数。"""

from __future__ import annotations

import re
from pathlib import Path

DEFAULT_SCRIPT_NAME = "MyAutomation"


def sanitize_filename(name: str, fallback: str = DEFAULT_SCRIPT_NAME) -> str:
    """清理文件名中的非法字符；为空时回退到默认名。"""
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", (name or "").strip())
    return cleaned or fallback


def read_rpa_tools(path: str | Path) -> str:
    """读取运行时工具库源码（磁盘文件为唯一来源，构建时实时读取）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        return f"# 读取 rpa_tools.py 失败: {e}\n"
