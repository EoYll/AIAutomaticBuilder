"""BuildPipeline：以 fake LLM / fake 打包器验证完整构建编排（不执行真实副作用）。"""

from __future__ import annotations

from pathlib import Path

from rpa_builder.code_validator import SecurityFinding
from rpa_builder.config import AppConfig
from rpa_builder.pipeline import BuildPipeline, BuildRequest


class FakeResult:
    def __init__(self, code: str) -> None:
        self.text = code
        self.code = code
        self.model = "fake"
        self.finish_reason = "stop"


class FakeLLM:
    def complete(self, system_prompt: str, user_message: str, **kwargs):  # noqa: ANN001
        return FakeResult('from rpa_tools import *\n\ndef main():\n    print("done")\n')


class FakePackager:
    def __init__(self) -> None:
        self.install_called = False
        self.ensure_playwright_called = False

    def install_dependencies(self) -> None:
        self.install_called = True

    def ensure_playwright(self) -> None:
        self.ensure_playwright_called = True

    def build_exe(self, script_path: str, dist_dir: str, temp_dir: str, *, name: str, show_console: bool) -> Path:
        exe = Path(dist_dir) / f"{name}.exe"
        exe.write_text("fake exe", encoding="utf-8")
        return exe


class RecordingEvents:
    def __init__(self, confirm: bool = True) -> None:
        self.confirm = confirm
        self.logs: list[str] = []
        self.done: Path | None = None

    def log(self, message: str) -> None:
        self.logs.append(message)

    def confirm_script(self, code: str, findings: list[SecurityFinding]) -> bool:
        return self.confirm

    def on_done(self, artifact: Path) -> None:
        self.done = artifact


def _make_pipeline(tmp_path: Path, events: RecordingEvents) -> tuple[BuildPipeline, FakePackager]:
    config = AppConfig()
    packager = FakePackager()
    pipeline = BuildPipeline(config, FakeLLM(), packager, base_dir=tmp_path, events=events)
    return pipeline, packager


def test_pipeline_generates_py_without_packaging(tmp_path):
    events = RecordingEvents()
    pipeline, packager = _make_pipeline(tmp_path, events)

    result = pipeline.build(BuildRequest(requirement="生成一个Excel", script_name="demo", package_exe=False))

    assert result is not None
    assert result.suffix == ".py"
    assert (tmp_path / "build_temp" / "demo.py").exists()
    assert (tmp_path / "build_temp" / "rpa_tools.py").exists()
    assert events.done == result
    # 仅生成 .py 时仍安装依赖（脚本运行需要），但不打包 EXE
    assert packager.install_called
    assert packager.ensure_playwright_called


def test_pipeline_packages_exe(tmp_path):
    events = RecordingEvents()
    pipeline, packager = _make_pipeline(tmp_path, events)

    result = pipeline.build(BuildRequest(requirement="生成一个Excel", script_name="demo", package_exe=True))

    assert result is not None
    assert result.name == "demo.exe"
    assert result.exists()
    assert packager.install_called
    assert packager.ensure_playwright_called
    assert events.done == result


def test_pipeline_respects_user_cancel(tmp_path):
    events = RecordingEvents(confirm=False)
    pipeline, _ = _make_pipeline(tmp_path, events)

    result = pipeline.build(BuildRequest(requirement="x", package_exe=False))

    assert result is None
    assert any("取消" in m for m in events.logs)


def test_pipeline_syntax_error_stops(tmp_path):
    class BadLLM(FakeLLM):
        def complete(self, system_prompt: str, user_message: str, **kwargs):  # noqa: ANN001
            return FakeResult("def broken(:\n")

    events = RecordingEvents()
    config = AppConfig()
    packager = FakePackager()
    pipeline = BuildPipeline(config, BadLLM(), packager, base_dir=tmp_path, events=events)

    result = pipeline.build(BuildRequest(requirement="x"))

    assert result is None
    assert any("语法错误" in m for m in events.logs)


def test_pipeline_empty_code_from_llm(tmp_path):
    class EmptyLLM(FakeLLM):
        def complete(self, system_prompt: str, user_message: str, **kwargs):  # noqa: ANN001
            return FakeResult("   ")

    events = RecordingEvents()
    config = AppConfig()
    pipeline = BuildPipeline(config, EmptyLLM(), FakePackager(), base_dir=tmp_path, events=events)

    result = pipeline.build(BuildRequest(requirement="x"))

    assert result is None
    assert any("返回为空" in m for m in events.logs)
