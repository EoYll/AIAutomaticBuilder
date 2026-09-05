"""命令行入口：无图形界面也能调用完整构建流水线。

与 GUI 共用同一 ``BuildPipeline``，体现“同核心、双入口”的设计。

用法示例::

    rpa-builder --requirement "打开 https://example.com 抓取标题" --name demo
    rpa-builder -r "生成含100个随机数的Excel" --no-package --confirm

API Key 通过环境变量 ``RPA_API_KEY`` 或 ``--api-key`` 注入。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from rpa_builder.code_validator import SecurityFinding
from rpa_builder.config import AppConfig
from rpa_builder.llm import DeepSeekProvider
from rpa_builder.packager import DefaultSubprocessRunner, Packager
from rpa_builder.pipeline import BuildPipeline, BuildRequest


class _CliEvents:
    """把流水线事件映射到终端输出。"""

    def __init__(self, confirm: bool) -> None:
        self._confirm = confirm

    def log(self, message: str) -> None:
        sys.stdout.write(message)
        sys.stdout.flush()

    def confirm_script(self, code: str, findings: list[SecurityFinding]) -> bool:
        if findings:
            print("\n⚠️ 安全扫描发现以下风险：")
            for f in findings:
                print(f"  [{f.severity}] 第 {f.line} 行: {f.message}")
        if not self._confirm:
            return True
        print("\n----- 生成的脚本 -----")
        print(code)
        print("----------------------")
        return input("确认打包? [y/N]: ").strip().lower() in ("y", "yes")

    def on_done(self, artifact: Path) -> None:
        print(f"\n✅ 完成: {artifact}")


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="自然语言驱动的 AI-RPA 自动化脚本生成器（命令行模式）")
    p.add_argument("--requirement", "-r", help="自动化需求描述（必填）")
    p.add_argument("--api-key", "-k", default=None, help="API Key（优先于环境变量）")
    p.add_argument("--model", "-m", default=None, help="模型名称")
    p.add_argument("--name", "-n", default=None, help="生成的脚本/EXE 文件名")
    p.add_argument("--no-package", action="store_true", help="仅生成 .py 脚本，不打包 EXE")
    p.add_argument("--console", action="store_true", help="EXE 显示控制台窗口（调试用）")
    p.add_argument("--confirm", action="store_true", help="打包前在终端人工确认生成的代码")
    p.add_argument("--config", default=None, help="config.json 路径")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)

    config = AppConfig.load(args.config)
    if args.api_key:
        config.api_key = args.api_key
    if args.model:
        config.model = args.model
    if args.name:
        config.script_name = args.name

    if not config.api_key:
        print("❌ 未配置 API Key，请设置环境变量 RPA_API_KEY 或使用 --api-key")
        return 1
    if not args.requirement:
        print("❌ 缺少需求描述，请使用 --requirement")
        return 1

    events = _CliEvents(confirm=args.confirm)
    runner = DefaultSubprocessRunner(events.log)
    packager = Packager(runner, events.log)
    llm = DeepSeekProvider(config.api_url, config.api_key, config.model, log=events.log)
    pipeline = BuildPipeline(config, llm, packager, events=events)

    request = BuildRequest(
        requirement=args.requirement,
        script_name=config.script_name,
        package_exe=not args.no_package,
        show_console=args.console,
    )
    result = pipeline.build(request)
    return 0 if result is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
