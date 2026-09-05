"""AI 生成代码的校验与安全静态扫描。

两重防线：
1. ``validate_python``：语法校验，杜绝直接报错的产物；
2. ``security_scan``：对高危操作（系统命令、动态执行、网络后门等）做静态行级扫描，
   结果在人工审阅阶段展示，作为“提示词规则”之外的第二层约束。

注意：这是**辅助人工审查**的静态扫描，并非沙箱隔离，不可作为唯一安全边界。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

# (severity, 正则, 说明)；severity ∈ {"error", "warning"}
_DANGEROUS_PATTERNS: list[tuple[str, str, str]] = [
    ("error", r"os\.system", "调用 os.system 执行系统命令"),
    ("error", r"\bsubprocess\b", "调用 subprocess 执行子进程"),
    ("error", r"\beval\s*\(", "使用 eval 动态求值"),
    ("error", r"\bexec\s*\(", "使用 exec 动态执行代码"),
    ("error", r"\bwinreg\b", "读写 Windows 注册表"),
    ("error", r"\bsocket\b", "网络套接字操作"),
    ("error", r"\bshutil\.rmtree\b", "递归删除目录"),
    ("warning", r"\b__import__\s*\(", "动态导入模块"),
    ("warning", r"\binput\s*\(", "在控制台阻塞等待输入"),
    ("warning", r"base64\.(b64decode|decodebytes)", "解码二进制内容"),
    ("warning", r"urllib\.request", "直接发起网络请求（建议用 rpa_tools.http_get）"),
]


@dataclass(frozen=True)
class SecurityFinding:
    """一条静态扫描发现。"""

    severity: str  # "error" | "warning"
    pattern: str  # 命中的模式
    message: str  # 人类可读描述
    line: int  # 1 起始行号

    @property
    def is_blocking(self) -> bool:
        return self.severity == "error"


def validate_python(source: str) -> tuple[bool, str]:
    """对 AI 生成的代码做语法校验，返回 (是否合法, 错误信息)。"""
    try:
        ast.parse(source)
        return True, ""
    except SyntaxError as e:
        return False, f"第 {e.lineno} 行: {e.msg}"


def security_scan(source: str) -> list[SecurityFinding]:
    """静态扫描代码中的高危操作，返回发现列表（按行号排序）。"""
    findings: list[SecurityFinding] = []
    for line_no, line in enumerate(source.splitlines(), start=1):
        for severity, pattern, message in _DANGEROUS_PATTERNS:
            if re.search(pattern, line):
                findings.append(SecurityFinding(severity, pattern, message, line_no))
    return findings
