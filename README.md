# AI 自动化构建器（本地库模式）

通过自然语言描述自动化需求，调用 DeepSeek API 生成 RPA 脚本，并结合本地工具库 `rpa_tools.py` 打包为单文件 EXE。

## 使用

1. 安装构建机依赖：`pip install -r requirements.txt`
2. 编辑 `config.json`，填写 `api_key`（或运行程序后在界面填写）。
3. 运行 `python main.py`，输入需求，点击「生成并打包 EXE」。

## 配置（config.json）

| 字段 | 说明 |
| --- | --- |
| `api_url` | DeepSeek 兼容的接口地址（默认已包含 `/v1/chat/completions`） |
| `api_key` | API 密钥 |
| `model` | 模型名称，如 `deepseek-chat` |

## 说明

- AI 生成的代码在打包前会弹出窗口供**人工审阅确认**，请确认代码安全后再打包。
- 生成的 EXE 依赖 Playwright 浏览器内核，该内核**不会被打包进 EXE**。若目标机器首次运行报缺少浏览器，请在该机器执行 `playwright install chromium`。
- 单文件 EXE 体积较大（数百 MB），属正常现象（含 PyAutoGUI + OpenCV + Playwright）。
