"""目标页面预分析。

用 Playwright（headless）打开目标页：dump DOM 结构 + 捕获接口 JSON。
对 JS 重型页面（数据由接口返回）尤其关键：报告中会列出 api_calls（接口 URL +
JSON 样例），供 LLM 判断数据来源并用 http_get 直接抓接口，比解析 DOM 可靠得多。

**安全注意**：页面内容属于不可信外部数据，调用方在拼入提示词时应按不可信数据
处理（见 ``prompt.build_user_message`` 的定界隔离）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from typing import Any

_FAKE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _truncate_json(data: Any, depth: int = 0) -> Any:
    """截断 JSON 以便喂给 LLM（列表取前 3 项、字典取前 8 键、长字符串截断）。"""
    if depth > 2:
        return "..."
    if isinstance(data, list):
        return [_truncate_json(x, depth + 1) for x in data[:3]]
    if isinstance(data, dict):
        return {k: _truncate_json(v, depth + 1) for k, v in list(data.items())[:8]}
    if isinstance(data, str) and len(data) > 100:
        return data[:100] + "..."
    return data


def analyze_page_for_ai(url: str, log: Callable[[str], None] | None = None) -> str | None:
    """分析目标页面结构，返回 JSON 字符串；失败返回 None。"""
    log = log or (lambda msg: print(msg))
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log("⚠️ 未安装 playwright，跳过页面预分析（pip install playwright）\n")
        return None

    async def _dump() -> str:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=_FAKE_UA,
                viewport={"width": 1366, "height": 768},
                locale="zh-CN",
            )
            page = await context.new_page()
            api_calls: list[dict[str, Any]] = []

            async def _on_response(response: Any) -> None:
                with contextlib.suppress(Exception):
                    if "json" not in response.headers.get("content-type", ""):
                        return
                    entry: dict[str, Any] = {
                        "url": response.url,
                        "method": response.request.method,
                        "status": response.status,
                        "sample": None,
                    }
                    if len(api_calls) < 12:  # 仅对前 12 个读 body，避免内存/耗时
                        with contextlib.suppress(Exception):
                            entry["sample"] = _truncate_json(await response.json())
                    api_calls.append(entry)

            page.on("response", _on_response)
            try:
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                # 滚动几次，触发懒加载/分页接口
                for _ in range(3):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(800)
                info = await page.evaluate(
                    """() => {
                    const h = sel => [...document.querySelectorAll(sel)].map(e => (e.innerText||'').trim()).filter(Boolean).slice(0,10);
                    const tables = [...document.querySelectorAll('table')];
                    const links = [...document.querySelectorAll('a[href]')].slice(0,30).map(a => ({text:(a.innerText||'').trim().slice(0,40), href:a.href}));
                    return {
                        url: location.href,
                        title: document.title,
                        description: (document.querySelector("meta[name='description']")||{}).content || null,
                        h1: h('h1'), h2: h('h2'), h3: h('h3'),
                        table_count: tables.length,
                        first_table_headers: tables.slice(0,1).map(t => [...t.querySelectorAll('th')].map(e => e.innerText.trim())),
                        link_count: document.querySelectorAll('a[href]').length,
                        links: links,
                        inputs: [...document.querySelectorAll('input,select,textarea')].map(e => ({tag:e.tagName, id:e.id, name:e.name, type:e.type||''})).slice(0,10),
                        json_ld_count: document.querySelectorAll("script[type='application/ld+json']").length,
                    };
                }"""
                )
                # 去重并按顺序保留接口清单
                seen: set[str] = set()
                api_summary: list[dict[str, Any]] = []
                for a in api_calls:
                    if a["url"] in seen:
                        continue
                    seen.add(a["url"])
                    api_summary.append(a)
                    if len(api_summary) >= 6:
                        break
                info["api_calls"] = api_summary
                return json.dumps(info, ensure_ascii=False, indent=2)
            finally:
                await browser.close()

    try:
        return asyncio.run(_dump())
    except Exception as e:
        log(f"⚠️ 页面预分析失败: {e}\n")
        return None
