"""构建流水线：需求 → 页面预分析 → LLM 生成 → 校验/安全扫描 → 人工审阅 → 打包。

与 GUI/CLI 解耦：通过 ``BuildEvents`` 回调输出日志、请求人工确认、报告完成。
因此同一核心可被 GUI 与 CLI 复用，并可注入 fake LLM / fake 打包器做单元测试。
"""

from __future__ import annotations

import os
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rpa_builder.code_validator import SecurityFinding, security_scan, validate_python
from rpa_builder.config import AppConfig
from rpa_builder.llm import LLMProvider
from rpa_builder.packager import Packager
from rpa_builder.page_analyzer import analyze_page_for_ai
from rpa_builder.prompt import SYSTEM_PROMPT, build_user_message, extract_first_url
from rpa_builder.utils import read_rpa_tools, sanitize_filename


class BuildEvents(Protocol):
    """构建过程中的事件回调，由 GUI / CLI 各自实现。"""

    def log(self, message: str) -> None:
        """输出一条构建日志。"""

    def confirm_script(self, code: str, findings: list[SecurityFinding]) -> bool:
        """请求人工审阅生成的代码；返回 True 表示确认继续。"""

    def on_done(self, artifact: Path) -> None:
        """构建完成，报告产物路径（.py 或 .exe）。"""


@dataclass
class BuildRequest:
    """一次构建任务的输入参数。"""

    requirement: str
    script_name: str = "MyAutomation"
    package_exe: bool = True
    show_console: bool = False
    feedback: str | None = None


class _NullEvents:
    """默认空实现：不输出、不打断、自动确认（供无界面场景/测试）。"""

    def log(self, message: str) -> None:
        return None

    def confirm_script(self, code: str, findings: list[SecurityFinding]) -> bool:
        return True

    def on_done(self, artifact: Path) -> None:
        return None


class BuildPipeline:
    """构建流水线的编排者。所有副作用（子进程、LLM、文件）均通过注入对象完成。"""

    def __init__(
        self,
        config: AppConfig,
        llm: LLMProvider,
        packager: Packager,
        *,
        base_dir: Path | None = None,
        rpa_tools_file: Path | None = None,
        events: BuildEvents | None = None,
    ) -> None:
        self.config = config
        self.llm = llm
        self.packager = packager
        self.base_dir = base_dir or Path(__file__).resolve().parent.parent.parent
        self.rpa_tools_file = rpa_tools_file or self.base_dir / "rpa_tools.py"
        self.events: BuildEvents = events or _NullEvents()

    def _log(self, message: str) -> None:
        self.events.log(message)

    def build(self, request: BuildRequest) -> Path | None:
        """执行完整构建流程，返回产物路径（.py 或 .exe）；中途失败/取消返回 None。"""
        temp_dir = self.base_dir / "build_temp"
        output_dir = temp_dir / "output"
        name = sanitize_filename(request.script_name)

        try:
            self._log("🚀 开始构建...\n")
            os.makedirs(temp_dir, exist_ok=True)
            os.makedirs(output_dir, exist_ok=True)

            # 1) 准备本地工具库（磁盘文件为唯一来源）
            self._log("📦 准备本地RPA工具库...\n")
            with open(temp_dir / "rpa_tools.py", "w", encoding="utf-8") as f:
                f.write(read_rpa_tools(self.rpa_tools_file))

            # 2) 页面预分析（需求含 URL 时，让 LLM 能"看到"真实页面结构）
            page_context = self._analyze_page(request.requirement)

            # 3) LLM 生成调用脚本
            self._log("🧠 正在向AI请求生成调用脚本...\n")
            user_message = build_user_message(
                request.requirement,
                page_context=page_context,
                feedback=request.feedback,
            )
            result = self.llm.complete(SYSTEM_PROMPT, user_message)
            script_code = result.code
            if not script_code.strip():
                self._log("❌ LLM 返回为空，请检查 API Key / URL / 网络\n")
                return None
            self._log(f"📝 LLM 生成完毕（{len(script_code)} 字符）\n")

            # 4) 语法校验 + 安全静态扫描
            ok, err = validate_python(script_code)
            if not ok:
                self._log(f"❌ AI 生成代码存在语法错误，已终止：\n{err}\n")
                return None
            findings = security_scan(script_code)
            for finding in findings:
                self._log(f"⚠️ 安全扫描（{finding.severity}）第 {finding.line} 行: {finding.message}\n")

            preview = script_code[:500] + ("..." if len(script_code) > 500 else "")
            self._log(f"📝 AI生成代码预览:\n{preview}\n\n")

            # 5) 打包前人工审阅
            if not self.events.confirm_script(script_code, findings):
                self._log("🛑 用户取消，未打包\n")
                return None

            # 6) 保存脚本
            script_path = temp_dir / f"{name}.py"
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(script_code)
            self._log(f"💾 脚本已保存至: {script_path}\n")

            # 7) 安装依赖 + 浏览器内核
            self._log("📦 检查并安装依赖...\n")
            self.packager.install_dependencies()
            self.packager.ensure_playwright()

            # 8) 打包（可选：勾选才打包为 EXE）
            if not request.package_exe:
                self._log(f"\n✅ 脚本已生成（未打包）：{script_path}\n")
                self._log(f"💡 调试运行：python build_temp\\{name}.py\n")
                self.events.on_done(script_path)
                return script_path

            exe_path = self.packager.build_exe(
                str(script_path),
                str(output_dir),
                str(temp_dir),
                name=name,
                show_console=request.show_console,
            )
            self._log(f"\n✅ 打包成功！\n🎯 EXE文件: {exe_path}\n")
            self._log(
                "💡 提示：Playwright 浏览器内核不会打包进 EXE，"
                "目标机器首次运行报错时请执行 `playwright install chromium`\n"
            )
            self.events.on_done(exe_path)
            return exe_path

        except Exception as e:  # noqa: BLE001
            self._log(f"❌ 异常: {e}\n")
            self._log(traceback.format_exc())
            return None

    def _analyze_page(self, requirement: str) -> str | None:
        """需求含 URL 时预分析页面结构；无 URL 或失败返回 None。"""
        url = extract_first_url(requirement)
        if not url:
            return None
        self._log(f"🔍 需求含目标网址，正在分析页面结构: {url}\n")
        self.packager.ensure_playwright()
        page_context = analyze_page_for_ai(url, log=self._log)
        if page_context:
            self._log(f"✅ 页面结构已分析（{len(page_context)} 字符），将提供给 AI 编写精确逻辑\n")
        else:
            self._log("⚠️ 页面分析失败，将不带结构参考生成\n")
        return page_context
