# 架构文档

## 1. 项目定位

`rpa-builder` 是一个**自然语言驱动的 RPA 脚本生成器**：用户描述自动化需求，系统调用 LLM 依据本地工具库的能力清单生成脚本，经校验与人工审阅后打包为单文件 EXE。

关键产物有两类：

- **构建器**（`src/rpa_builder/`）：生成与打包的主程序，GUI/CLI 双入口。
- **运行期 SDK**（`rpa_tools.py`）：被打包进 EXE、供生成脚本 `from rpa_tools import *` 调用的 RPA 操作库。

## 2. 模块职责

| 模块 | 职责 | 关键抽象 |
| --- | --- | --- |
| `config.py` | 配置加载，优先级：环境变量 > config.json > 默认值；密钥不落盘 | `AppConfig` |
| `llm.py` | LLM 对话协议与 DeepSeek 实现；内置重试/退避/空响应处理 | `LLMProvider`、`DeepSeekProvider` |
| `prompt.py` | 系统提示词、user 消息组装（含不可信数据隔离）、代码块提取 | `SYSTEM_PROMPT`、`build_user_message` |
| `page_analyzer.py` | Playwright 无头分析目标页：DOM 结构 + 接口 JSON 捕获 | `analyze_page_for_ai` |
| `code_validator.py` | 语法校验（ast）+ 高危操作静态扫描（正则行级） | `validate_python`、`security_scan` |
| `packager.py` | 构建机依赖安装、Playwright 内核、PyInstaller 命令封装 | `SubprocessRunner`、`Packager` |
| `pipeline.py` | 完整流程编排与状态机；副作用全部经注入对象发生 | `BuildPipeline`、`BuildRequest`、`BuildEvents` |
| `gui.py` | Tkinter 视图层，实现 `BuildEvents`，线程安全桥接 | `Application` |
| `cli.py` | argparse 命令行入口，实现 `BuildEvents` | `main` |
| `utils.py` | 文件名清理、工具库源码读取 | — |

## 3. 核心流程（BuildPipeline）

```
build(BuildRequest)
 ├─ 1. 写出 rpa_tools.py 到 build_temp/
 ├─ 2. 需求含 URL？→ ensure_playwright + analyze_page_for_ai（不可信数据）
 ├─ 3. llm.complete(SYSTEM_PROMPT, build_user_message(...))
 │       失败时自动重试 3 次，空响应视为失败
 ├─ 4. validate_python → 语法错误则终止
 │      security_scan → 发现项进入审阅环节展示
 ├─ 5. events.confirm_script(code, findings) → 用户取消则终止
 ├─ 6. 写脚本文件 build_temp/<name>.py
 ├─ 7. packager.install_dependencies() + ensure_playwright()
 ├─ 8. package_exe？
 │       否 → on_done(.py)
 │       是 → packager.build_exe(...) → on_done(.exe)
 └─ 任一环节异常 → 记录 traceback，返回 None
```

## 4. 事件协议（BuildEvents）

GUI 与 CLI 复用同一流水线，差异全部封装在事件回调中：

| 回调 | GUI 实现 | CLI 实现 |
| --- | --- | --- |
| `log(msg)` | 投递 `task_queue`，主线程写入日志区 | 直接 `sys.stdout.write` |
| `confirm_script(code, findings)` | 弹 Toplevel 审阅窗口（含安全风险摘要） | 终端打印代码 + 风险项，`input()` 确认 |
| `on_done(path)` | `messagebox.showinfo` | 打印完成路径 |

GUI 的线程安全策略：工作线程不直接触碰 tkinter，所有任务经 `queue.Queue` 投递，主线程用 `root.after` 轮询消费。

## 5. 安全模型

```
┌────────────────────────────┐
│ 提示词规则（软约束）         │   SYSTEM_PROMPT 第 6 条禁止系统命令
├────────────────────────────┤
│ 静态扫描（辅助人工审阅）     │   security_scan 行级正则，error/warning 分级
├────────────────────────────┤
│ 人工审阅（关卡）            │   任何产物打包前必须确认
├────────────────────────────┤
│ 密钥隔离                   │   api_key 仅环境变量/内存
├────────────────────────────┤
│ 不可信数据隔离（提示词）     │   页面内容定界包裹，声明为数据样例
└────────────────────────────┘
```

**边界**：以上是缓解措施而非沙箱。真正的沙箱执行（容器/受限用户）不在当前范围内，是已知的演进方向。

## 6. 关键设计决策

1. **核心与界面解耦**：`BuildPipeline` 无 GUI 依赖，可用 fake LLM / fake packager 直接单元测试（见 `tests/test_pipeline.py`）。
2. **依赖注入**：`LLMProvider`、`SubprocessRunner`、`BuildEvents` 均为协议/回调，测试与多前端共用。
3. **配置优先级**：环境变量 > JSON > 默认，`save()` 只写回非敏感键 —— 防止密钥因任何一次 GUI 保存而落盘。
4. **`rpa_tools.py` 保持单文件**：它是打包进 EXE 的 SDK，生成脚本以 `from rpa_tools import *` 导入；单文件设计利于随 PyInstaller 分发，无需处理包内资源路径。
5. **SDK 防御式风格豁免简化类 lint**：见 `pyproject.toml` 的 `per-file-ignores`，其持久文件流与 best-effort 清理是有意为之。

## 7. 已知限制与演进方向

- 构建机需联网（pip 安装依赖、下载浏览器内核）。
- 生成的 EXE 体积较大（数百 MB），Playwright 内核不打包，目标机首装需手动 `playwright install chromium`。
- 未实现沙箱执行、任务调度、模板库、许可证等产品化能力（详见 README 定位，属后续演进）。
- `rpa_tools.py` 的异步方法要求单次 `asyncio.run`（`run_web_task` 限制），提示词中已约束生成脚本合并异步入口。
