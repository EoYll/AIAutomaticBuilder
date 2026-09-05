# rpa-builder · AI 自动化构建器

**用一句话描述需求，自动生成可运行的 RPA 脚本并打包为单文件 EXE。**

本项目是一个自然语言驱动的 RPA 脚本生成器：用户用中文描述自动化任务 → LLM（DeepSeek）依据本地工具库 `rpa_tools` 的能力清单生成调用脚本 → 语法校验 + 安全静态扫描 → 人工审阅 → PyInstaller 打包为独立 EXE。

支持**桌面操作**（PyAutoGUI）、**网页自动化**（Playwright）、**Excel 增删改查**（openpyxl/COM）、**屏幕 OCR**、**接口抓取**的混合流程。

[![CI](https://github.com/EoYll/AIAutomaticBuilder/actions/workflows/ci.yml/badge.svg)](https://github.com/EoYll/AIAutomaticBuilder/actions/workflows/ci.yml)

## 特性

- 🧠 **自然语言驱动**：无需编程基础，描述需求即生成脚本
- 🔍 **页面预分析**：需求含 URL 时自动用无头浏览器分析页面结构并捕获接口 JSON，让 LLM 写出精确的选择器与抓取逻辑
- 🔁 **带日志重新生成**：把上次运行日志 + 页面报告回喂给 LLM，自诊断并修正
- 🛡️ **双重防线**：语法校验 + 对 `os.system` / `subprocess` / `eval` / 注册表等的高危操作静态扫描，审阅时可视化展示
- 🔐 **密钥安全**：API Key 只经环境变量注入，绝不落盘，`config.json` 仅存非敏感偏好
- 🖥️ / 💻 **双入口**：同一构建流水线，Tkinter GUI 与命令行 CLI 共用

## 架构

```
┌────────────────────────────────────────────────────────────┐
│                       入口 (Entry)                          │
│        GUI (src/rpa_builder/gui.py)     CLI (cli.py)       │
└──────────────────────────┬─────────────────────────────────┘
                           │ BuildEvents 回调（日志/审阅/完成）
                           ▼
┌────────────────────────────────────────────────────────────┐
│                 BuildPipeline (pipeline.py)                 │
│   编排：需求 → 预分析 → 生成 → 校验/扫描 → 审阅 → 打包        │
├────────────┬────────────┬──────────────┬───────────────────┤
│  config.py │  llm.py    │ packager.py  │ page_analyzer.py  │
│ 配置/密钥   │ LLM 抽象与  │ 依赖安装 +    │ Playwright 无头    │
│  加载      │ DeepSeek   │ PyInstaller  │ 页面结构/接口预分析 │
│            │ 实现+重试   │ 封装          │                   │
├────────────┴────────────┴──────────────┴───────────────────┤
│  prompt.py      code_validator.py        utils.py          │
│  提示词构建/    语法校验 + 安全静态扫描   文件名清理等        │
│  不可信数据隔离                                              │
└────────────────────────────────────────────────────────────┘
                           │
                           ▼ 生成脚本引用
┌────────────────────────────────────────────────────────────┐
│  rpa_tools.py —— 运行期 RPA 工具库（打包进 EXE 的 SDK）      │
│  桌面 / 网页 / Excel / OCR / HTTP / 文件 / 日志兜底          │
└────────────────────────────────────────────────────────────┘
```

**设计要点**

- **核心与界面解耦**：`BuildPipeline` 不依赖 tkinter，通过 `BuildEvents` 回调协议输出日志、请求人工确认、报告完成。GUI 与 CLI 各自实现该协议，核心可被单元测试直接驱动。
- **依赖注入**：LLM 客户端（`LLMProvider` 协议）与子进程运行器（`SubprocessRunner` 协议）均可注入 mock，测试不产生真实副作用。
- **不可信数据隔离**：页面预分析结果（第三方网页内容）在拼入提示词时用显式定界符包裹并声明为"数据样例"，降低 prompt injection 风险。

## 目录结构

```
.
├── src/rpa_builder/        # 构建器核心（分层包）
│   ├── config.py           # 配置加载：环境变量 > config.json > 默认值
│   ├── llm.py              # LLMProvider 抽象 + DeepSeek 实现（重试/退避）
│   ├── prompt.py           # 系统提示词、user 消息组装、代码提取
│   ├── page_analyzer.py    # 页面预分析（DOM + 接口捕获）
│   ├── code_validator.py   # 语法校验 + 安全静态扫描
│   ├── packager.py         # 依赖安装、浏览器内核、PyInstaller 封装
│   ├── pipeline.py         # BuildPipeline 流水线编排
│   ├── gui.py              # Tkinter 图形界面
│   └── cli.py              # 命令行入口
├── rpa_tools.py            # 运行期 RPA 工具库（生成脚本的 SDK）
├── tests/                  # 单元测试（mock 外部依赖）
├── docs/ARCHITECTURE.md    # 架构文档
├── .github/workflows/ci.yml
├── pyproject.toml
└── .env.example            # 环境变量模板（复制为 .env）
```

## 快速开始

```bash
# 1) 安装（推荐可编辑模式，含开发依赖）
pip install -e ".[dev]"

# 2) 配置密钥（二选一）
#    a) 复制 .env.example 为 .env 并填写 RPA_API_KEY
#    b) 设置系统环境变量 RPA_API_KEY
```

## 使用

### GUI 模式

```bash
python main.py          # 或 rpa-builder-gui
```

在界面填写需求描述，点击「⚡ 生成并打包 EXE」。生成代码会先弹出审阅窗口（含安全扫描结果），确认后才打包。

### CLI 模式

```bash
# 仅生成 .py（快速调试）
rpa-builder -r "生成含100个随机数的Excel，保存为 test.xlsx" --no-package

# 生成并打包 EXE，打包前在终端人工确认
rpa-builder -r "打开 https://example.com 抓取所有链接与标题，保存为 result.json" --confirm
```

### 常用参数

| 参数 | 说明 |
| --- | --- |
| `-r, --requirement` | 自动化需求描述（必填） |
| `-k, --api-key` | API Key（优先于环境变量） |
| `-m, --model` | 模型名称 |
| `-n, --name` | 生成的脚本/EXE 文件名 |
| `--no-package` | 仅生成 `.py`，不打包 |
| `--console` | EXE 显示控制台窗口（调试用） |
| `--confirm` | 打包前在终端人工确认代码 |

## 配置

读取优先级：**环境变量 > `config.json` > 内置默认值**。

| 环境变量 | 说明 |
| --- | --- |
| `RPA_API_URL` | 兼容接口地址（默认含 `/v1/chat/completions`） |
| `RPA_API_KEY` | API 密钥（**敏感，仅存环境变量**） |
| `RPA_MODEL` | 模型名称 |
| `RPA_SCRIPT_NAME` | 默认产物文件名 |

`config.json` 只保存非敏感偏好，`api_key` **永不写入磁盘**。模板见 `config.example.json`。

## 安全设计

1. **密钥管理**：API Key 仅经环境变量/界面内存注入，`config.json` 与版本库中均不出现明文。
2. **代码静态扫描**：`code_validator.security_scan` 对生成代码做行级高危操作检测（`os.system`、`subprocess`、`eval/exec`、注册表、网络套接字等），审阅时展示风险项。
3. **提示词注入防护**：页面预分析得到的第三方内容按不可信数据隔离，不当作指令处理。
4. **人工审阅关卡**：任何产物在打包前都需在 GUI 弹窗 / CLI 确认。

> 注意：静态扫描是**辅助人工审查**，并非沙箱隔离，不应作为唯一安全边界。执行 AI 生成的代码前请人工确认其行为。

## 开发

```bash
pip install -e ".[dev]"

ruff check .             # Lint
ruff format --check .    # 格式检查
mypy src                 # 类型检查
pytest                   # 测试（27 个用例）
```

CI（GitHub Actions）在每次 push/PR 时对 Python 3.10 / 3.11 / 3.12 依次执行上述四步。

## 技术栈

Python 3.10+ · Tkinter · PyAutoGUI · Playwright · openpyxl · RapidOCR · PyInstaller · pydantic(可选) · ruff · mypy · pytest

## License

MIT
