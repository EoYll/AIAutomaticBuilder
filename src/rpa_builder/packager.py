"""依赖安装、浏览器内核下载与 PyInstaller 打包。

子进程运行器通过 ``SubprocessRunner`` 协议暴露，测试中可替换为记录型假实现，
从而在不真正执行 pip / PyInstaller 的情况下验证打包流程。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

# 构建机需要安装的依赖：pip 包名 -> 导入模块名（用于检查是否已安装）
REQUIRED_DEPS: dict[str, str] = {
    "pyinstaller": "PyInstaller",
    "playwright": "playwright",
    "pyautogui": "pyautogui",
    "opencv-python": "cv2",
    "pygetwindow": "pygetwindow",
    "pillow": "PIL",
    "requests": "requests",
    "pywinauto": "pywinauto",
    "openpyxl": "openpyxl",
    "pywin32": "win32com",
    "rapidocr_onnxruntime": "rapidocr_onnxruntime",
}

# 打包时显式排除的重型库（避免 Qt 冲突 / 体积爆炸 / 打包失败）
EXCLUDED_MODULES = (
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "qtpy",
    "matplotlib",
    "IPython",
    "torch",
    "tensorflow",
)


class SubprocessRunner(Protocol):
    """执行命令并透传输出；非零退出码应抛异常。"""

    def run(self, cmd: list[str]) -> None:
        """运行 cmd，输出实时转发给日志；失败抛 CalledProcessError。"""
        ...


class DefaultSubprocessRunner:
    """真实执行子进程，并把输出实时转发给 log 回调。"""

    def __init__(self, log: Callable[[str], None]) -> None:
        self._log = log

    def run(self, cmd: list[str]) -> None:
        self._log(f">>> {' '.join(cmd)}\n")
        kwargs: dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "bufsize": 1,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen(cmd, **kwargs)
        if process.stdout is not None:
            for line in process.stdout:
                self._log(line)
        process.wait()
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd)
        self._log("--- 完成 ---\n")


class Packager:
    """负责把生成好的脚本安装依赖、下载浏览器内核并打包为单文件 EXE。"""

    def __init__(self, runner: SubprocessRunner, log: Callable[[str], None]) -> None:
        self.runner = runner
        self._log = log

    def install_dependencies(self) -> None:
        """仅安装缺失的构建机依赖（幂等）。"""
        missing = [pkg for pkg, mod in REQUIRED_DEPS.items() if importlib.util.find_spec(mod) is None]
        if not missing:
            self._log("✅ 依赖已满足，跳过安装\n")
            return
        self._log(f"⏳ 安装缺失依赖: {', '.join(missing)}\n")
        for pkg in missing:
            self.runner.run([sys.executable, "-m", "pip", "install", pkg, "-q", "--disable-pip-version-check"])

    def ensure_playwright(self) -> None:
        """确保 playwright 及 chromium 内核可用（页面预分析与生成脚本需要）。"""
        if importlib.util.find_spec("playwright") is None:
            self._log("⏳ 安装 playwright（页面预分析需要）...\n")
            self.runner.run([sys.executable, "-m", "pip", "install", "playwright", "-q", "--disable-pip-version-check"])
        self._log("🌐 确保 Playwright 浏览器内核可用（首次约100MB）...\n")
        self.runner.run([sys.executable, "-m", "playwright", "install", "chromium"])

    def build_exe(
        self,
        script_path: str,
        dist_dir: str,
        temp_dir: str,
        *,
        name: str,
        show_console: bool,
    ) -> Path:
        """调用 PyInstaller 将脚本打包为单文件 EXE，返回产物路径。

        注意：勿用 --collect-all，它会强制导入 rapidocr 的全部子模块，连带
        matplotlib/torch 等重型库（本机同时装有 PyQt5/PyQt6 时必现 Qt 冲突）。
        """
        console_flag = "--noconsole" if not show_console else "--console"
        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--noconfirm",  # 已存在产物时直接覆盖，避免交互阻塞
            console_flag,
            "--name",
            name,
            "--distpath",
            dist_dir,
            "--workpath",
            str(Path(temp_dir) / "build"),
            "--specpath",
            temp_dir,
            "--paths",
            temp_dir,
            # 打包 OCR 模型文件
            "--collect-data",
            "rapidocr_onnxruntime",
        ]
        for mod in EXCLUDED_MODULES:
            cmd += ["--exclude-module", mod]
        cmd.append(script_path)

        self._log("📦 正在打包为 EXE (请耐心等待1-3分钟)...\n")
        self.runner.run(cmd)
        exe_path = Path(dist_dir) / f"{name}.exe"
        if not exe_path.exists():
            raise RuntimeError(f"打包失败：未找到产物 {exe_path}")
        return exe_path
