# ============================ BuilderGUI.py ============================
# AI 自动化构建器（本地库模式）
# 描述需求 -> 调用 DeepSeek API 生成 RPA 脚本 -> 打包为单文件 EXE
# API 地址 / Key / 模型名保存在同目录 config.json 中。
import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk
import threading
import subprocess
import sys
import os
import re
import json
import glob
import asyncio
import time
import queue
import importlib.util
import traceback
import requests

# ==================== 路径与配置 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
TEMP_DIR = os.path.join(BASE_DIR, "build_temp")
RPA_TOOLS_FILE = os.path.join(BASE_DIR, "rpa_tools.py")
OUTPUT_DIR = os.path.join(TEMP_DIR, "output")
EXE_NAME = "MyAutomation"


def sanitize_filename(name):
    """清理文件名中的非法字符；为空时回退到默认名。"""
    import re as _re
    cleaned = _re.sub(r'[\\/:*?"<>|]', "_", (name or "").strip())
    return cleaned or EXE_NAME

DEFAULT_CONFIG = {
    "api_url": "https://api.deepseek.com/v1/chat/completions",
    "api_key": "",
    "model": "deepseek-chat",
    "script_name": "MyAutomation",
}

# 构建机需要安装的依赖（仅在缺失时安装）
REQUIRED_DEPS = ["pyinstaller", "playwright", "pyautogui", "opencv-python", "pygetwindow", "pillow", "requests", "pywinauto", "openpyxl", "pywin32", "rapidocr_onnxruntime"]
# pip 包名 -> 导入模块名（用于检查是否已安装）
DEP_MODULES = {
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


# ==================== 配置读写 ====================
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            print(f"⚠️ 读取配置文件失败，使用默认配置: {e}")
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 保存配置文件失败: {e}")


def load_rpa_tools():
    """rpa_tools.py 以磁盘文件为唯一来源。"""
    try:
        with open(RPA_TOOLS_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        return f"# 读取 rpa_tools.py 失败: {e}\n"


RPA_TOOLS_CODE = load_rpa_tools()


def validate_python(source):
    """对 AI 生成的代码做语法校验，返回 (是否合法, 错误信息)。"""
    try:
        compile(source, "<ai_generated>", "exec")
        return True, ""
    except SyntaxError as e:
        return False, f"第 {e.lineno} 行: {e.msg}"


# ==================== AI 提示词 ====================
SYSTEM_PROMPT = """
你是一位RPA流程自动化专家。用户会描述一个自动化任务，你需要生成一个Python脚本。
【重要】你必须调用本地工具库 `rpa_tools` 来完成所有操作，不要编写底层实现。

`rpa_tools` 提供的接口（请在代码中通过 `from rpa_tools import *` 导入）：
【能力速览】
- 同步函数：桌面/窗口/鼠标/键盘/剪贴板/截图/OCR/HTTP/文件/Excel 增删改查/人工等待，直接调用即可
- 异步网页（WebPage 类）：访问/点击/填表/读数据/自适应抓取/登录会话/网络响应捕获，用 run_web_task 执行
- 【关键】异步方法必须加 await（如 `await page.goto(url)`），同步方法直接调用（如 `write_text_file(path, 内容)`）；
  不确定时可看下方各区块中的示例写法。

- 桌面操作（同步函数，直接调用）：
  - open_application(app_path)          # 打开应用，如 "notepad"、"calc"、"C:\\path\\app.exe"
  - activate_window(title)              # 激活标题包含指定文本的窗口
  - mouse_click(x, y)                   # 点击屏幕坐标
  - double_click(x, y) / right_click(x, y)   # 双击 / 右键点击
  - mouse_move(x, y)                    # 移动鼠标
  - mouse_drag(x1, y1, x2, y2)          # 按住拖拽（框选/拖动）
  - mouse_scroll(amount)                # 滚轮：正向上、负向下
  - input_text(text)                    # 输入文本（含中文，自动走剪贴板粘贴）
  - press_key(key)                      # 按键，如 'enter'；组合键用 '+' 连接，如 'ctrl+s'（等效 Ctrl+S）
  - copy_clipboard(text) / get_clipboard()    # 剪贴板写入 / 读取
  - take_screenshot(filename)           # 全屏截图
  - wait(seconds)                       # 等待
  - wait_for_user("提示语")             # 弹窗等待人工操作（登录/验证码），点确定后继续
  - wait_window(title)                  # 等待窗口出现（比固定 sleep 更稳健）
  - get_window_rect(title) / move_window(title, x, y) / close_window(title)   # 窗口位置/移动/关闭
  - ensure_dir(path) / copy_file(a,b) / move_file(a,b) / delete_file(path) / list_files(dir)  # 文件操作
  - write_text_file(path, 内容)        # 写入文本文件（覆盖）；append_text_file 追加；read_text_file(path) 读取
  - write_json_file(path, 数据)       # 数据以 JSON 落盘（中文不转义）
  - open_file(path)                    # 用系统默认程序打开文件（如 .txt 用记事本）
  - find_image_on_screen(image_path)    # 图像识别定位（带重试）
  - click_image(image_path)             # 识图点击：找到图片并点击中心（软件图标/按钮）
  - capture_region((左,上,宽,高), "模板.png")  # 截取区域存为模板图（供识图用）
  - click_text("确定")                  # 按文字识别并点击（OCR 坐标版，适合图标化按钮）
  - save_dialog(filename)               # 处理原生"另存为"对话框：填入文件名并保存
  - open_dialog(path)                   # 处理原生"打开"对话框：输入路径并确认

- Excel 操作（文件级增删改查，无需安装 Excel）：
  - 增：create_excel_file(path) 新建；ExcelFile(path).create("Sheet1") 建文件；.add_sheet("新表") 加工作表
  - 查：read_excel_cell(path, 行, 列)；read_excel_range(path, 1,1, 4,3) 读区域返回二维列表；.sheet_names() 列工作表
  - 改：write_excel_cell(path, 行, 列, 值)；ExcelFile(path).load().write_cell(...).save()
  - 删：delete_excel_row(path, 行)；delete_excel_column(path, 列)；.delete_row(row).delete_column(col).delete_sheet(name)
  - 链式示例：ExcelFile("报表.xlsx").create("数据").write_range(1,1,[["姓名","分数"],["张三",90]]).save()
  - 控制 Excel 程序（需安装 Microsoft Excel）：
      excel = ExcelApp()
      excel.new_workbook("报表").write_cell(1,1,"姓名").write_row(2,["张三","李四"])
      excel.save_as(r"C:\\data\\报表.xlsx")          # COM 另存为，不弹对话框
      excel.close()

- 数据采集：
  - ocr_screen(region=None)             # 屏幕文字识别（RapidOCR，内置中文模型；region 为 (左,上,宽,高)）
  - http_get(url, params=None)          # HTTP GET 请求，返回响应文本
  - http_post(url, data=None, json=None)  # HTTP POST 请求，返回响应文本
  - 【重要】需求含 URL 时，工具会自动分析目标页：结构参考中的 api_calls 列出该页面
    由接口返回的数据（接口 URL + JSON 样例）。若目标数据在接口里，【优先用 http_get 直接
    请求接口抓取】比解析 DOM 可靠得多；分页接口通常带 page 参数，用循环逐页请求 +
    time.sleep(1) 控制频率即可，避免触发反爬。

- 网页操作（异步，使用 WebPage 类与 run_web_task）：
  async def web_steps():
      async with WebPage(headless=False) as page:
          await page.goto("https://example.com")
          await page.click("选择器")
          await page.fill("选择器", "文本")
          await page.select_option("选择器", value="选项值")   # 下拉选择
          text = await page.get_text("选择器")                 # 读取元素文本
          await page.press_key("Enter")                        # 页面按键
          await page.wait_until_text("关键字")                 # 等待文本出现
          await page.screenshot("page.png")
      # 无需手动 close()，async with 退出（含异常）时自动关闭浏览器
      # 其他可用：await page.get_attribute("选择器", "属性名")  # 读取属性
      #           await page.evaluate("document.title")        # 执行 JS 并取值

- 网页数据抓取（自适应，无需关心具体选择器）：
      links = await page.extract_all_links()      # 全部链接 [{text,href}]
      texts = await page.extract_texts("h1,h2,h3") # 按语义标签抓文本列表
      table = await page.extract_table()           # 表格转二维列表
      body  = await page.extract_article_text()    # 正文
      meta  = await page.extract_meta()            # 标题/描述/关键词
      ld    = await page.extract_json_ld()         # JSON-LD 结构化数据
      ms    = await page.extract_regex(r"模式")     # 正则抓取片段
      await page.find_by_text("下一页")             # 按可见文本点击（无需选择器）
      await page.auto_scroll_load(5)               # 滚动加载动态内容
      await page.wait_network_idle()               # 等网络空闲
      await page.dump_page("report.json")          # 导出页面结构报告
      write_json_file("data.json", 抓取结果)        # 结果存 JSON（中文不转义）

- 登录与会话（浏览器为可见窗口，可手动输入）：
      await page.wait_for_login("退出登录")         # 弹窗提示后等用户手动登录，检测到信号自动继续
      await page.save_session("session.json")       # 登录成功后保存会话，下次免登录
      await page.load_session("session.json")       # 加载会话（在 goto 前调用）
      page = WebPage(session_file="session.json")   # 构造时指定，启动自动加载会话
      wait_for_user("请手动完成验证码后点确定")       # 纯手动等待弹窗

- 网络响应捕获（拿接口 JSON，比解析 DOM 更可靠，适合动态加载数据）：
      page.start_response_capture("api")             # 开始捕获 URL 含 "api" 的 JSON 响应（同步，不加 await）
      ... 点击/滚动/翻页触发请求 ...
      data = page.get_captured_responses()           # 读取捕获结果 [{url, json}]（同步）
      page.stop_response_capture()                   # 停止（同步）
      # 或等单个接口返回：
      # task = asyncio.create_task(page.wait_for_response("api/list"))  # 先挂等待
      # await page.click("#加载更多")                                  # 再触发
      # json2 = await task

  # 通过 run_web_task(web_steps) 执行上述异步流程

【规则】：
1. 脚本必须包含 `if __name__ == "__main__":` 入口，并把整体流程写在 `main()` 函数中；
   入口处调用 `run_with_logging(main)`，以记录日志并在异常时自动保存失败截图。
2. asyncio.run 与 run_web_task 在整个脚本中【只能被调用一次】。若同时涉及桌面和网页操作，
   请在 main() 中：先依次执行同步的桌面操作，最后调用一次 run_web_task(web_steps) 完成网页部分。
3. 流程中使用 wait() 或 time.sleep() 保证步骤稳定。
4. 保存文档/生成文件时【优先直接写入】，不要依赖 GUI"另存为"对话框：
   - 文本内容：write_text_file("test.txt", "Hello World") 直接写文件（最可靠）；
     需要展示时再用 open_file("test.txt") 用默认程序（如记事本）打开。
   - Excel：用 ExcelFile / create_excel_file 直接生成。
   仅当必须操作已打开应用的"另存为"对话框时才用 save_dialog（best-effort，可能因系统差异失败）。
5. 涉及登录/验证码时：用 wait_for_login 或 wait_for_user 等待人工操作，并设置长超时（≥120 秒），
   不要用短 sleep；登录成功后（检测到登录信号）再继续抓取。
6. 只能使用 Python 标准库、asyncio 以及 rpa_tools 提供的接口；
   【禁止】执行系统命令、读取或修改系统敏感文件（如注册表、系统目录）。
   `from rpa_tools import *` 不会自动带入 time/asyncio 等标准库，如需使用请显式 import。
7. 只输出可直接运行的纯 Python 代码，不要包含任何解释文字或多余符号。
8. 中文路径字符串请使用原始字符串 r"..." 或正确转义。
"""


# ==================== 页面预分析 ====================
def extract_first_url(text: str):
    """从需求文本中提取第一个 URL。"""
    m = re.search(r'https?://[^\s"\']+', text)
    return m.group(0) if m else None


def _truncate_json(data, depth=0):
    """截断 JSON 以便喂给 AI（列表取前 3 项、字典取前 8 键、长字符串截断）。"""
    if depth > 2:
        return "..."
    if isinstance(data, list):
        return [_truncate_json(x, depth + 1) for x in data[:3]]
    if isinstance(data, dict):
        return {k: _truncate_json(v, depth + 1) for k, v in list(data.items())[:8]}
    if isinstance(data, str) and len(data) > 100:
        return data[:100] + "..."
    return data


def analyze_page_for_ai(url: str):
    """用 Playwright（headless）打开目标页：dump DOM 结构 + 捕获接口 JSON。

    对 JS 重型页面（数据由接口返回）尤其关键：报告中会列出 api_calls（接口 URL +
    JSON 样例），供 AI 判断数据来源并用 http_get 直接抓接口，比解析 DOM 可靠得多。
    返回 JSON 字符串；失败返回 None。
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None

    async def _dump():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
                viewport={"width": 1366, "height": 768},
                locale="zh-CN",
            )
            page = await context.new_page()
            api_calls = []

            async def _on_response(response):
                try:
                    if "json" not in response.headers.get("content-type", ""):
                        return
                    entry = {"url": response.url, "method": response.request.method,
                             "status": response.status, "sample": None}
                    if len(api_calls) < 12:  # 仅对前 12 个读 body，避免内存/耗时
                        try:
                            entry["sample"] = _truncate_json(await response.json())
                        except Exception:
                            pass
                    api_calls.append(entry)
                except Exception:
                    pass

            page.on("response", _on_response)
            try:
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                # 滚动几次，触发懒加载/分页接口
                for _ in range(3):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(800)
                info = await page.evaluate("""() => {
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
                }""")
                # 去重并按顺序保留接口清单
                seen, api_summary = set(), []
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
        print(f"⚠️ 页面预分析失败: {e}")
        return None


# ==================== 构建线程 ====================
class BuildThread(threading.Thread):
    def __init__(self, app, api_url, api_key, model, user_requirement, show_console, package_exe=True, script_name="MyAutomation", feedback=None):
        super().__init__()
        self.app = app
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.requirement = user_requirement
        self.show_console = show_console
        self.package_exe = package_exe
        self.script_name = sanitize_filename(script_name)
        self.feedback = feedback

    def run(self):
        try:
            self.app.update_log("🚀 开始构建...\n")
            os.makedirs(TEMP_DIR, exist_ok=True)
            os.makedirs(OUTPUT_DIR, exist_ok=True)

            # 1) 准备本地工具库
            tools_path = os.path.join(TEMP_DIR, "rpa_tools.py")
            self.app.update_log("📦 准备本地RPA工具库...\n")
            with open(tools_path, "w", encoding="utf-8") as f:
                f.write(RPA_TOOLS_CODE)

            # 2) 页面预分析（需求含 URL 时，让 AI 能"看到"真实页面结构）
            url = extract_first_url(self.requirement)
            page_context = None
            if url:
                self.app.update_log(f"🔍 需求含目标网址，正在分析页面结构: {url}\n")
                self.ensure_playwright()
                page_context = analyze_page_for_ai(url)
                if page_context:
                    self.app.update_log(f"✅ 页面结构已分析（{len(page_context)} 字符），将提供给 AI 编写精确逻辑\n")
                else:
                    self.app.update_log("⚠️ 页面分析失败，将不带结构参考生成\n")

            # 3) AI 生成调用脚本
            self.app.update_log("🧠 正在向AI请求生成调用脚本...\n")
            script_code = self.call_ai(extra_context=page_context)
            if not script_code:
                self.app.update_log("❌ AI 返回为空，请检查 API Key / URL / 网络\n")
                return

            # 4) 语法校验
            ok, err = validate_python(script_code)
            if not ok:
                self.app.update_log(f"❌ AI 生成代码存在语法错误，已终止：\n{err}\n")
                return

            preview = script_code[:500] + ("..." if len(script_code) > 500 else "")
            self.app.update_log(f"📝 AI生成代码预览:\n{preview}\n\n")

            # 5) 打包前人工审阅
            if not self.app.confirm_script(script_code):
                self.app.update_log("🛑 用户取消，未打包\n")
                return

            # 6) 保存脚本
            script_path = os.path.join(TEMP_DIR, f"{self.script_name}.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(script_code)
            self.app.update_log(f"💾 脚本已保存至: {script_path}\n")

            # 7) 安装依赖
            self.app.update_log("📦 检查并安装依赖...\n")
            self.install_dependencies()

            # 8) 安装浏览器内核
            self.app.update_log("🌐 下载 Playwright 浏览器内核(首次约100MB)...\n")
            self.run_subprocess([sys.executable, "-m", "playwright", "install", "chromium"])

            # 9) 打包（可选：勾选才打包为 EXE）
            if not self.package_exe:
                script_path = os.path.join(TEMP_DIR, f"{self.script_name}.py")
                self.app.update_log(f"\n✅ 脚本已生成（未打包）：{script_path}\n")
                self.app.update_log(f"💡 调试运行：python build_temp\\{self.script_name}.py\n")
                self.app.show_done(script_path)
                return

            self.app.update_log("📦 正在打包为 EXE (请耐心等待1-3分钟)...\n")
            console_flag = "--noconsole" if not self.show_console else "--console"
            cmd = [
                sys.executable, "-m", "PyInstaller",
                "--onefile",
                "--noconfirm",          # 已存在产物时直接覆盖，避免交互阻塞
                console_flag,
                "--name", self.script_name,
                "--distpath", OUTPUT_DIR,
                "--workpath", os.path.join(TEMP_DIR, "build"),
                "--specpath", TEMP_DIR,
                "--paths", TEMP_DIR,
                # 打包 OCR 模型文件。注意：勿用 --collect-all，它会强制导入 rapidocr 的全部子模块，
                # 连带 matplotlib/torch 等重型库（本机同时装有 PyQt5/PyQt6 时必现 Qt 冲突）。
                "--collect-data", "rapidocr_onnxruntime",
                "--exclude-module", "PyQt5",
                "--exclude-module", "PyQt6",
                "--exclude-module", "PySide2",
                "--exclude-module", "PySide6",
                "--exclude-module", "qtpy",
                "--exclude-module", "matplotlib",
                "--exclude-module", "IPython",
                "--exclude-module", "torch",
                "--exclude-module", "tensorflow",
                script_path,
            ]
            self.run_subprocess(cmd)

            exe_path = os.path.join(OUTPUT_DIR, f"{self.script_name}.exe")
            self.app.update_log(f"\n✅ 打包成功！\n🎯 EXE文件: {exe_path}\n")
            self.app.update_log("💡 提示：Playwright 浏览器内核不会打包进 EXE，"
                                "目标机器首次运行报错时请执行 `playwright install chromium`\n")
            self.app.show_done(exe_path)

        except Exception as e:
            self.app.update_log(f"❌ 异常: {str(e)}\n")
            self.app.update_log(traceback.format_exc())

    def call_ai(self, extra_context=None):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        user_msg = self.requirement
        if self.feedback:
            user_msg += f"\n\n【上次运行反馈，请分析原因并输出修正后的完整脚本（仍是纯代码）】\n{self.feedback}"
        if extra_context:
            user_msg += f"\n\n【目标页面结构参考（已由工具自动分析，请据此编写准确的选择器与提取逻辑）】\n{extra_context}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.3,
            "max_tokens": 8192,  # 复杂脚本需要更长输出，避免中途截断
        }
        # 重试机制：网络瞬时故障或空响应时自动重试
        last_err = None
        for attempt in range(1, 4):
            try:
                resp = requests.post(self.api_url, headers=headers, json=payload, timeout=180)
                resp.raise_for_status()
                result = resp.json()
                choice = result["choices"][0]
                msg = choice.get("message", {})
                raw = msg.get("content") or ""
                if not raw.strip():
                    finish = choice.get("finish_reason")
                    self.app.update_log(f"⚠️ AI 第 {attempt} 次返回空内容"
                                        f"（finish_reason={finish}，message 字段={list(msg.keys())}），重试中...\n")
                    last_err = "AI 返回空内容"
                    time.sleep(2)
                    continue
                code_match = re.search(r"```python\s*(.*?)```", raw, re.DOTALL)
                return code_match.group(1).strip() if code_match else raw.strip()
            except Exception as e:
                last_err = e
                self.app.update_log(f"⚠️ API 调用失败（第 {attempt} 次）: {e}\n")
                time.sleep(2)
        self.app.update_log(f"❌ AI 多次调用失败: {last_err}\n")
        return None

    def install_dependencies(self):
        missing = [p for p in REQUIRED_DEPS if importlib.util.find_spec(DEP_MODULES[p]) is None]
        if not missing:
            self.app.update_log("✅ 依赖已满足，跳过安装\n")
            return
        self.app.update_log(f"⏳ 安装缺失依赖: {', '.join(missing)}\n")
        for pkg in missing:
            self.run_subprocess([sys.executable, "-m", "pip", "install", pkg, "-q", "--disable-pip-version-check"])

    def ensure_playwright(self):
        """确保 playwright + chromium 可用（页面预分析需要）。"""
        if importlib.util.find_spec("playwright") is None:
            self.app.update_log("⏳ 安装 playwright（页面预分析需要）...\n")
            self.run_subprocess([sys.executable, "-m", "pip", "install", "playwright", "-q", "--disable-pip-version-check"])
        self.app.update_log("🌐 确保 Playwright 浏览器内核可用（首次约100MB）...\n")
        self.run_subprocess([sys.executable, "-m", "playwright", "install", "chromium"])

    def run_subprocess(self, cmd):
        self.app.update_log(f">>> {' '.join(cmd)}\n")
        kwargs = {
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
        for line in process.stdout:
            self.app.update_log(line)
        process.wait()
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd)
        self.app.update_log("--- 完成 ---\n")


# ==================== 界面 ====================
class Application:
    def __init__(self, root):
        self.root = root
        self.config = load_config()
        self.task_queue = queue.Queue()

        root.title("🤖 AI自动化构建器 (本地库模式)")
        root.geometry("820x720")
        root.minsize(700, 600)

        # ---- AI 接口配置（保存于 config.json）----
        frame_cfg = tk.LabelFrame(root, text="⚙️ AI 接口配置（保存于 config.json）", font=('微软雅黑', 10))
        frame_cfg.pack(pady=8, padx=10, fill=tk.X)

        self.url_entry = self._make_cfg_row(frame_cfg, "API 地址", show=None)
        self.api_entry = self._make_cfg_row(frame_cfg, "API Key", show="*")
        self.model_entry = self._make_cfg_row(frame_cfg, "模型", show=None)

        self.url_entry.insert(0, self.config.get("api_url", DEFAULT_CONFIG["api_url"]))
        self.api_entry.insert(0, self.config.get("api_key", ""))
        self.model_entry.insert(0, self.config.get("model", DEFAULT_CONFIG["model"]))

        # ---- 需求输入 ----
        tk.Label(root, text="📝 描述你的自动化需求 (支持网页/桌面混合):", font=('微软雅黑', 10)).pack(anchor='w', padx=10)
        self.text_area = scrolledtext.ScrolledText(root, height=8, font=('Consolas', 11), wrap=tk.WORD)
        self.text_area.pack(padx=10, pady=5, fill=tk.BOTH, expand=True)
        self.text_area.insert(tk.END, "打开 https://example.com，自适应抓取页面所有链接和标题，保存为 result.json 和 result.xlsx")

        # ---- 选项 ----
        frame_options = tk.Frame(root)
        frame_options.pack(pady=5, padx=10, fill=tk.X)
        self.console_var = tk.BooleanVar(value=False)
        tk.Checkbutton(frame_options, text="🖥️ EXE显示控制台窗口(调试用)", variable=self.console_var,
                       font=('微软雅黑', 9)).pack(side=tk.LEFT)
        self.package_var = tk.BooleanVar(value=True)
        tk.Checkbutton(frame_options, text="📦 打包为EXE(取消则仅生成.py脚本，便于快速调试)", variable=self.package_var,
                       font=('微软雅黑', 9)).pack(side=tk.LEFT, padx=(15, 0))

        # ---- 命名 ----
        frame_name = tk.Frame(root)
        frame_name.pack(pady=2, padx=10, fill=tk.X)
        tk.Label(frame_name, text="📛 名称:", font=('微软雅黑', 9)).pack(side=tk.LEFT)
        self.name_entry = tk.Entry(frame_name, width=24)
        self.name_entry.pack(side=tk.LEFT, padx=4)
        self.name_entry.insert(0, self.config.get("script_name", EXE_NAME))
        tk.Label(frame_name, text="生成的脚本/EXE 文件名", fg='#888', font=('微软雅黑', 9)).pack(side=tk.LEFT)

        # ---- 按钮 ----
        frame_btn = tk.Frame(root)
        frame_btn.pack(pady=8)
        self.btn_generate = ttk.Button(frame_btn, text="⚡ 生成并打包 EXE", command=self.start_build, style="TButton")
        self.btn_generate.pack(side=tk.LEFT, padx=6)
        self.btn_feedback = ttk.Button(frame_btn, text="🔁 带日志重新生成", command=self.start_build_with_feedback, style="TButton")
        self.btn_feedback.pack(side=tk.LEFT, padx=6)

        # ---- 日志 ----
        tk.Label(root, text="📋 实时构建日志:", font=('微软雅黑', 10)).pack(anchor='w', padx=10)
        self.log_area = scrolledtext.ScrolledText(root, height=14, font=('Consolas', 9), bg='#f4f4f4', fg='#333', wrap=tk.WORD)
        self.log_area.pack(padx=10, pady=5, fill=tk.BOTH, expand=True)
        self.log_area.insert(tk.END, "👋 欢迎！填写 API Key 和需求，点击按钮开始生成程序。\n")

        # 主线程事件轮询：从队列取日志/对话框任务，避免跨线程直接操作 tkinter
        self.root.after(100, self._process_main_queue)

    def _make_cfg_row(self, parent, label, show=None):
        row = tk.Frame(parent)
        row.pack(fill=tk.X, padx=6, pady=2)
        tk.Label(row, text=label, width=10, anchor='w', font=('微软雅黑', 9)).pack(side=tk.LEFT)
        entry = tk.Entry(row, show=show)
        entry.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=4)
        return entry

    # ---------- 线程安全日志/对话框 ----------
    def update_log(self, text):
        self.task_queue.put(("log", text))

    def show_done(self, exe_path):
        self.task_queue.put(("done", exe_path))

    def confirm_script(self, code):
        """在工作线程中调用：弹出代码审阅窗口，确认后返回 True。"""
        result_holder = {}
        done = threading.Event()
        self.task_queue.put(("confirm", code, done, result_holder))
        done.wait()
        return result_holder.get("ok", False)

    def _process_main_queue(self):
        try:
            while True:
                item = self.task_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self.log_area.insert(tk.END, item[1])
                    self.log_area.see(tk.END)
                elif kind == "confirm":
                    self._show_confirm_dialog(item[1], item[2], item[3])
                elif kind == "done":
                    messagebox.showinfo("完成", f"自动化程序已生成！\n位置: {item[1]}")
        except queue.Empty:
            pass
        self.root.after(100, self._process_main_queue)

    def _show_confirm_dialog(self, code, done, result_holder):
        top = tk.Toplevel(self.root)
        top.title("📝 代码审阅 - 请确认 AI 生成的代码")
        top.geometry("780x580")
        top.minsize(620, 420)
        frame_code = tk.Frame(top)
        frame_code.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        txt = scrolledtext.ScrolledText(frame_code, font=('Consolas', 9), wrap=tk.NONE, bg='#ffffff', fg='#222')
        txt.insert(tk.END, code)
        txt.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        # 代码长行用横向滚动条查看（wrap=NONE 下不会被裁掉，避免"文字消失"）
        xscroll = ttk.Scrollbar(frame_code, orient=tk.HORIZONTAL, command=txt.xview)
        xscroll.pack(side=tk.BOTTOM, fill=tk.X)
        txt.configure(xscrollcommand=xscroll.set)
        tk.Label(top, text="请确认代码安全、符合预期后再打包。", fg='#a00', font=('微软雅黑', 9)).pack(padx=8)
        bar = tk.Frame(top)
        bar.pack(fill=tk.X, padx=8, pady=6)

        def _ok():
            result_holder["ok"] = True
            top.destroy()
            done.set()

        def _cancel():
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
    def collect_config(self):
        self.config["api_url"] = self.url_entry.get().strip() or DEFAULT_CONFIG["api_url"]
        self.config["api_key"] = self.api_entry.get().strip()
        self.config["model"] = self.model_entry.get().strip() or DEFAULT_CONFIG["model"]
        self.config["script_name"] = sanitize_filename(self.name_entry.get())
        save_config(self.config)
        return self.config

    def start_build(self, feedback=None):
        cfg = self.collect_config()
        if not cfg["api_key"]:
            messagebox.showerror("错误", "请填写你的 API Key！")
            return
        requirement = self.text_area.get("1.0", tk.END).strip()
        if not requirement:
            messagebox.showerror("错误", "请描述你要自动化的操作！")
            return
        self.log_area.delete("1.0", tk.END)
        self.update_log("开始构建任务...\n")
        self.update_log(f"📝 需求:\n{requirement}\n\n")
        self.btn_generate.config(state=tk.DISABLED, text="⏳ 构建中...")
        self.btn_feedback.config(state=tk.DISABLED)
        thread = BuildThread(self, cfg["api_url"], cfg["api_key"], cfg["model"],
                             requirement, self.console_var.get(), self.package_var.get(),
                             cfg.get("script_name", EXE_NAME), feedback=feedback)
        thread.daemon = True
        thread.start()
        self.check_thread(thread)

    def check_thread(self, thread):
        if thread.is_alive():
            self.root.after(500, lambda: self.check_thread(thread))
        else:
            self.btn_generate.config(state=tk.NORMAL, text="⚡ 生成并打包 EXE")
            self.btn_feedback.config(state=tk.NORMAL)
            self.update_log("\n🏁 任务结束。\n")

    def start_build_with_feedback(self):
        """带上次运行日志重新生成：读取最新日志 + report.json，让 AI 自诊断并修正。"""
        feedback = self.collect_feedback()
        if not feedback:
            messagebox.showinfo("提示", "未找到运行日志（~/rpa_logs），请先运行一次生成的脚本")
            return
        self.start_build(feedback=feedback)

    def collect_feedback(self):
        """收集最新运行日志与页面结构报告，作为 AI 修正的反馈上下文。"""
        log_dir = os.path.join(os.path.expanduser("~"), "rpa_logs")
        logs = sorted(glob.glob(os.path.join(log_dir, "*.log")), key=os.path.getmtime, reverse=True)
        if not logs:
            return None
        try:
            with open(logs[0], encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            content = ""
        text = f"最新日志文件: {logs[0]}\n--- 日志内容（截断） ---\n{content[-6000:]}"
        for p in ("report.json", os.path.join("build_temp", "report.json")):
            if os.path.exists(p):
                try:
                    with open(p, encoding="utf-8", errors="replace") as f:
                        text += f"\n\n--- 页面结构报告 report.json ---\n{f.read()[:3000]}"
                except Exception:
                    pass
                break
        return text

    def on_close(self):
        self.collect_config()  # 关闭前保存配置
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = Application(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
