"""Tkinter 图形界面入口。

视图层仅负责界面与事件转发；构建逻辑位于 ``BuildPipeline``。
线程安全策略：工作线程不直接触碰 tkinter，所有日志/对话框任务经 ``task_queue``
投递到主线程处理（``root.after`` 轮询），避免跨线程操作 GUI 崩溃。
"""

from __future__ import annotations

import glob
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from typing import Any

from rpa_builder.code_validator import SecurityFinding
from rpa_builder.config import AppConfig
from rpa_builder.llm import DeepSeekProvider
from rpa_builder.packager import DefaultSubprocessRunner, Packager
from rpa_builder.pipeline import BuildPipeline, BuildRequest

FONT_UI = "微软雅黑"
FONT_MONO = "Consolas"
_DEFAULT_API_URL = "https://api.deepseek.com/v1/chat/completions"
_DEFAULT_MODEL = "deepseek-chat"


class Application:
    """主窗口。同时实现 ``BuildEvents`` 接口桥接流水线与界面。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.config = AppConfig.load()
        self.task_queue: queue.Queue[tuple[Any, ...]] = queue.Queue()

        root.title("🤖 AI自动化构建器")
        root.geometry("820x720")
        root.minsize(700, 600)

        self._build_widgets()
        root.after(100, self._process_main_queue)

    # ---------- 控件构建 ----------

    def _build_widgets(self) -> None:
        frame_cfg = tk.LabelFrame(
            self.root,
            text="⚙️ AI 接口配置（API Key 仅存于内存/环境变量，不写入 config.json）",
            font=(FONT_UI, 10),
        )
        frame_cfg.pack(pady=8, padx=10, fill=tk.X)

        self.url_entry = self._make_cfg_row(frame_cfg, "API 地址", show=None)
        self.api_entry = self._make_cfg_row(frame_cfg, "API Key", show="*")
        self.model_entry = self._make_cfg_row(frame_cfg, "模型", show=None)

        self.url_entry.insert(0, self.config.api_url)
        self.api_entry.insert(0, self.config.api_key)  # 来自环境变量 RPA_API_KEY
        self.model_entry.insert(0, self.config.model)

        tk.Label(self.root, text="📝 描述你的自动化需求 (支持网页/桌面混合):", font=(FONT_UI, 10)).pack(
            anchor="w", padx=10
        )
        self.text_area = scrolledtext.ScrolledText(self.root, height=8, font=(FONT_MONO, 11), wrap=tk.WORD)
        self.text_area.pack(padx=10, pady=5, fill=tk.BOTH, expand=True)
        self.text_area.insert(
            tk.END,
            "打开 https://example.com，自适应抓取页面所有链接和标题，保存为 result.json 和 result.xlsx",
        )

        frame_options = tk.Frame(self.root)
        frame_options.pack(pady=5, padx=10, fill=tk.X)
        self.console_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            frame_options, text="🖥️ EXE显示控制台窗口(调试用)", variable=self.console_var, font=(FONT_UI, 9)
        ).pack(side=tk.LEFT)
        self.package_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            frame_options,
            text="📦 打包为EXE(取消则仅生成.py脚本，便于快速调试)",
            variable=self.package_var,
            font=(FONT_UI, 9),
        ).pack(side=tk.LEFT, padx=(15, 0))

        frame_name = tk.Frame(self.root)
        frame_name.pack(pady=2, padx=10, fill=tk.X)
        tk.Label(frame_name, text="📛 名称:", font=(FONT_UI, 9)).pack(side=tk.LEFT)
        self.name_entry = tk.Entry(frame_name, width=24)
        self.name_entry.pack(side=tk.LEFT, padx=4)
        self.name_entry.insert(0, self.config.script_name)
        tk.Label(frame_name, text="生成的脚本/EXE 文件名", fg="#888", font=(FONT_UI, 9)).pack(side=tk.LEFT)

        frame_btn = tk.Frame(self.root)
        frame_btn.pack(pady=8)
        self.btn_generate = ttk.Button(frame_btn, text="⚡ 生成并打包 EXE", command=self.start_build)
        self.btn_generate.pack(side=tk.LEFT, padx=6)
        self.btn_feedback = ttk.Button(frame_btn, text="🔁 带日志重新生成", command=self.start_build_with_feedback)
        self.btn_feedback.pack(side=tk.LEFT, padx=6)

        tk.Label(self.root, text="📋 实时构建日志:", font=(FONT_UI, 10)).pack(anchor="w", padx=10)
        self.log_area = scrolledtext.ScrolledText(
            self.root, height=14, font=(FONT_MONO, 9), bg="#f4f4f4", fg="#333", wrap=tk.WORD
        )
        self.log_area.pack(padx=10, pady=5, fill=tk.BOTH, expand=True)
        self.log_area.insert(tk.END, "👋 欢迎！填写 API Key 和需求，点击按钮开始生成程序。\n")

    def _make_cfg_row(self, parent: tk.Widget, label: str, show: str | None = None) -> tk.Entry:
        row = tk.Frame(parent)
        row.pack(fill=tk.X, padx=6, pady=2)
        tk.Label(row, text=label, width=10, anchor="w", font=(FONT_UI, 9)).pack(side=tk.LEFT)
        entry = tk.Entry(row, show=show or "")
        entry.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=4)
        return entry

    # ---------- BuildEvents 实现（线程安全，经 task_queue 回主线程） ----------

    def log(self, message: str) -> None:
        """BuildEvents.log：投递日志到主线程。"""
        self.task_queue.put(("log", message))

    def confirm_script(self, code: str, findings: list[SecurityFinding]) -> bool:
        """BuildEvents.confirm_script：阻塞等待用户在主线程确认。"""
        result_holder: dict[str, bool] = {}
        done = threading.Event()
        self.task_queue.put(("confirm", code, findings, done, result_holder))
        done.wait()
        return result_holder.get("ok", False)

    def on_done(self, artifact: Path) -> None:
        """BuildEvents.on_done：报告产物路径。"""
        self.task_queue.put(("done", str(artifact)))

    def _process_main_queue(self) -> None:
        try:
            while True:
                item = self.task_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self.log_area.insert(tk.END, item[1])
                    self.log_area.see(tk.END)
                elif kind == "confirm":
                    _, code, findings, done, result_holder = item
                    self._show_confirm_dialog(code, findings, done, result_holder)
                elif kind == "done":
                    messagebox.showinfo("完成", f"自动化程序已生成！\n位置: {item[1]}")
        except queue.Empty:
            pass
        self.root.after(100, self._process_main_queue)

    def _show_confirm_dialog(
        self,
        code: str,
        findings: list[SecurityFinding],
        done: threading.Event,
        result_holder: dict[str, bool],
    ) -> None:
        top = tk.Toplevel(self.root)
        top.title("📝 代码审阅 - 请确认 AI 生成的代码")
        top.geometry("780x580")
        top.minsize(620, 420)

        if findings:
            summary = "；".join(f"第{f.line}行[{f.severity}]{f.message}" for f in findings)
            tk.Label(
                top,
                text=f"⚠️ 安全扫描发现 {len(findings)} 项风险：{summary}",
                fg="#a00",
                font=(FONT_UI, 9),
                wraplength=740,
                justify="left",
            ).pack(padx=8, pady=(8, 0), anchor="w")
        tk.Label(top, text="请确认代码安全、符合预期后再打包。", fg="#a00", font=(FONT_UI, 9)).pack(padx=8, anchor="w")

        frame_code = tk.Frame(top)
        frame_code.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        txt = scrolledtext.ScrolledText(frame_code, font=(FONT_MONO, 9), wrap=tk.NONE, bg="#ffffff", fg="#222")
        txt.insert(tk.END, code)
        txt.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        # 代码长行用横向滚动条查看（wrap=NONE 下不会被裁掉）
        xscroll = ttk.Scrollbar(frame_code, orient=tk.HORIZONTAL, command=txt.xview)
        xscroll.pack(side=tk.BOTTOM, fill=tk.X)
        txt.configure(xscrollcommand=xscroll.set)

        bar = tk.Frame(top)
        bar.pack(fill=tk.X, padx=8, pady=6)

        def _ok() -> None:
            result_holder["ok"] = True
            top.destroy()
            done.set()

        def _cancel() -> None:
            result_holder["ok"] = False
            top.destroy()
            done.set()

        tk.Button(bar, text="✔ 确认打包", command=_ok, width=12).pack(side=tk.RIGHT, padx=4)
        tk.Button(bar, text="✘ 取消", command=_cancel, width=12).pack(side=tk.RIGHT)
        top.protocol("WM_DELETE_WINDOW", _cancel)
        top.transient(self.root)
        top.grab_set()
        top.focus_set()

    # ---------- 构建流程 ----------

    def collect_config(self) -> AppConfig:
        """收集界面配置；api_key 仅保存在内存，绝不落盘。"""
        self.config.api_url = self.url_entry.get().strip() or _DEFAULT_API_URL
        self.config.api_key = self.api_entry.get().strip()
        self.config.model = self.model_entry.get().strip() or _DEFAULT_MODEL
        self.config.script_name = self.name_entry.get().strip() or "MyAutomation"
        self.config.save()
        return self.config

    def start_build(self, feedback: str | None = None) -> None:
        cfg = self.collect_config()
        requirement = self.text_area.get("1.0", tk.END).strip()
        if not cfg.api_key:
            messagebox.showerror("错误", "请填写 API Key（或设置环境变量 RPA_API_KEY）！")
            return
        if not requirement:
            messagebox.showerror("错误", "请描述你要自动化的操作！")
            return

        self.log_area.delete("1.0", tk.END)
        self.log("开始构建任务...\n")
        self.log(f"📝 需求:\n{requirement}\n\n")

        self.btn_generate.config(state=tk.DISABLED, text="⏳ 构建中...")
        self.btn_feedback.config(state=tk.DISABLED)

        request = BuildRequest(
            requirement=requirement,
            script_name=cfg.script_name,
            package_exe=self.package_var.get(),
            show_console=self.console_var.get(),
            feedback=feedback,
        )
        thread = threading.Thread(target=self._run_pipeline, args=(cfg, request), daemon=True)
        thread.start()
        self.check_thread(thread)

    def _run_pipeline(self, cfg: AppConfig, request: BuildRequest) -> None:
        """后台线程入口：构建流水线，全部副作用经事件回调回到主线程。"""
        try:
            runner = DefaultSubprocessRunner(self.log)
            packager = Packager(runner, self.log)
            llm = DeepSeekProvider(cfg.api_url, cfg.api_key, cfg.model, log=self.log)
            pipeline = BuildPipeline(cfg, llm, packager, events=self)
            pipeline.build(request)
        except Exception as e:  # noqa: BLE001
            self.log(f"❌ 构建线程异常: {e}\n")

    def check_thread(self, thread: threading.Thread) -> None:
        if thread.is_alive():
            self.root.after(500, lambda: self.check_thread(thread))
        else:
            self.btn_generate.config(state=tk.NORMAL, text="⚡ 生成并打包 EXE")
            self.btn_feedback.config(state=tk.NORMAL)
            self.log("\n🏁 任务结束。\n")

    def start_build_with_feedback(self) -> None:
        """带上次运行日志重新生成：读取最新日志 + report.json，让 AI 自诊断并修正。"""
        feedback = self.collect_feedback()
        if not feedback:
            messagebox.showinfo("提示", "未找到运行日志（~/rpa_logs），请先运行一次生成的脚本")
            return
        self.start_build(feedback=feedback)

    def collect_feedback(self) -> str | None:
        """收集最新运行日志与页面结构报告，作为 AI 修正的反馈上下文。"""
        log_dir = os.path.join(os.path.expanduser("~"), "rpa_logs")
        logs = sorted(glob.glob(os.path.join(log_dir, "*.log")), key=os.path.getmtime, reverse=True)
        if not logs:
            return None
        try:
            with open(logs[0], encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            content = ""
        text = f"最新日志文件: {logs[0]}\n--- 日志内容（截断） ---\n{content[-6000:]}"
        for p in ("report.json", os.path.join("build_temp", "report.json")):
            if os.path.exists(p):
                try:
                    with open(p, encoding="utf-8", errors="replace") as f:
                        text += f"\n\n--- 页面结构报告 report.json ---\n{f.read()[:3000]}"
                except OSError:
                    pass
                break
        return text

    def on_close(self) -> None:
        self.collect_config()  # 关闭前保存非敏感配置
        self.root.destroy()


def main() -> None:
    """GUI 入口。"""
    root = tk.Tk()
    app = Application(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
