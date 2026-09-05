"""LLM 客户端抽象与 DeepSeek 实现。

对外只暴露 ``LLMProvider`` 协议，便于替换厂商（OpenAI/Gemini/本地模型）、
注入 mock 做单元测试。``DeepSeekProvider`` 内置重试与退避：网络瞬时故障或
空响应时自动重试（默认 3 次）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import requests

from rpa_builder.prompt import extract_code


class LLMError(RuntimeError):
    """LLM 调用失败（网络错误 / 返回空内容 / 非 2xx）。"""


@dataclass(frozen=True)
class LLMResult:
    """一次完整对话的结果。"""

    text: str  # 原始响应内容
    code: str  # 从中提取出的 Python 代码
    model: str
    finish_reason: str | None


class LLMProvider(Protocol):
    """LLM 对话协议：给定系统提示词与用户消息，返回 LLMResult。"""

    def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 8192,
    ) -> LLMResult:
        """完成一次对话。失败时抛 LLMError。"""
        ...


class DeepSeekProvider:
    """OpenAI 兼容的 chat/completions 客户端（默认指向 DeepSeek）。

    ``session`` 可由外部注入，便于测试替换为 mock；
    ``log`` 为日志回调（GUI 用它把日志投递到界面）。
    """

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str,
        *,
        session: requests.Session | None = None,
        timeout: float = 180.0,
        max_retries: int = 3,
        retry_delay: float = 2.0,
        log: Callable[[str], None] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("API Key 不能为空，请通过环境变量 RPA_API_KEY 或 --api-key 注入")
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._log = log or (lambda _msg: None)

    def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 8192,
    ) -> LLMResult:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.post(self.api_url, headers=headers, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                result = resp.json()
                choice = result["choices"][0]
                message = choice.get("message") or {}
                raw = message.get("content") or ""
                if not raw.strip():
                    finish = choice.get("finish_reason")
                    self._log(
                        f"⚠️ LLM 第 {attempt} 次返回空内容"
                        f"（finish_reason={finish}，message 字段={list(message.keys())}），重试中...\n"
                    )
                    last_err = LLMError("LLM 返回空内容")
                else:
                    return LLMResult(
                        text=raw,
                        code=extract_code(raw),
                        model=self.model,
                        finish_reason=choice.get("finish_reason"),
                    )
            except Exception as e:
                last_err = e
                self._log(f"⚠️ LLM 调用失败（第 {attempt} 次）: {e}\n")
            if attempt < self.max_retries:
                time.sleep(self.retry_delay)
        raise LLMError(f"LLM 多次调用失败: {last_err}") from last_err
