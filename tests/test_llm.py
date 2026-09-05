"""DeepSeekProvider：请求构建、重试、空响应、密钥校验（mock requests）。"""

from __future__ import annotations

import pytest

from rpa_builder.llm import DeepSeekProvider, LLMError


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class FakeSession:
    """逐次吐出 payloads；errors 非空时先抛异常（模拟网络故障）。"""

    def __init__(self, payloads: list[dict], errors: list[Exception | None] | None = None) -> None:
        self.payloads = list(payloads)
        self.errors = list(errors or [])
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, headers=None, json=None, timeout=None) -> FakeResponse:  # noqa: ANN001
        self.calls.append((url, json))
        if self.errors:
            err = self.errors.pop(0)
            if err is not None:
                raise err
        return FakeResponse(self.payloads.pop(0))


def _payload(text: str, finish: str = "stop") -> dict:
    return {"choices": [{"message": {"content": text}, "finish_reason": finish}]}


def _provider(session: FakeSession, retry_delay: float = 0) -> DeepSeekProvider:
    return DeepSeekProvider("https://x/v1/chat/completions", "sk-test", "m", session=session, retry_delay=retry_delay)


def test_complete_extracts_code():
    session = FakeSession([_payload('```python\nprint("hi")\n```')])
    result = _provider(session).complete("sys", "user")
    assert result.code == 'print("hi")'
    assert result.finish_reason == "stop"
    # 校验请求体
    _, body = session.calls[0]
    assert body["model"] == "m"
    assert body["messages"][0]["role"] == "system"


def test_retries_on_empty_then_succeeds():
    session = FakeSession([_payload(""), _payload("ok")])
    result = _provider(session).complete("sys", "user")
    assert result.text == "ok"
    assert len(session.calls) == 2


def test_retries_on_network_error_then_succeeds():
    session = FakeSession([_payload("ok")], errors=[RuntimeError("connection reset")])
    result = _provider(session).complete("sys", "user")
    assert result.text == "ok"
    assert len(session.calls) == 2


def test_raises_after_exhausting_retries():
    session = FakeSession([_payload("")] * 3)
    with pytest.raises(LLMError):
        _provider(session).complete("sys", "user")


def test_empty_api_key_rejected():
    with pytest.raises(ValueError):
        DeepSeekProvider("https://x", "", "m")
