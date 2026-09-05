"""rpa-builder：自然语言驱动的 AI-RPA 自动化脚本生成器。

描述需求 → 调用 LLM 生成 RPA 调用脚本 → 校验/审阅 → 打包为单文件 EXE。
核心构建逻辑见 `rpa_builder.pipeline.BuildPipeline`，与 GUI/CLI 解耦。
"""

__version__ = "0.1.0"
