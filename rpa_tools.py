"""
rpa_tools.py - 本地RPA操作库
支持网页（Playwright）和桌面（PyAutoGUI）操作，提供统一接口。
关键函数包含异常处理；图像查找等操作提供重试机制。

⚠️ 注意：asyncio.run()（即 run_web_task 内部实现）在一个进程内只能调用一次。
生成调用脚本时，请将全部网页操作合并进单个异步入口。
"""
import sys
import os
import time
import asyncio
import subprocess
import inspect
import traceback
from collections import namedtuple

import pyautogui
import pygetwindow as gw
from playwright.async_api import async_playwright

# ========== 全局配置 ==========
DEFAULT_TIMEOUT = 10
RETRY_TIMES = 3
RETRY_INTERVAL = 1.0

# 显式声明可导出接口，避免 `from rpa_tools import *` 把 time/asyncio 等
# 标准库名称一并灌入调用方命名空间。
__all__ = [
    "open_application", "activate_window", "mouse_click", "mouse_move",
    "mouse_drag", "double_click", "right_click", "mouse_scroll",
    "input_text", "press_key", "take_screenshot", "wait", "wait_for_user", "wait_window",
    "copy_clipboard", "get_clipboard",
    "get_window_rect", "move_window", "close_window",
    "ensure_dir", "copy_file", "move_file", "delete_file", "list_files",
    "write_text_file", "read_text_file", "append_text_file", "write_json_file", "open_file",
    "find_image_on_screen", "click_image", "capture_region", "save_dialog", "open_dialog",
    "ocr_screen", "ocr_screen_boxes", "click_text", "http_get", "http_post",
    "setup_logging", "run_with_logging",
    "ExcelFile", "ExcelApp", "create_excel_file", "write_excel_cell", "read_excel_cell",
    "read_excel_range", "delete_excel_row", "delete_excel_column",
    "WebPage", "run_web_task",
    "DEFAULT_TIMEOUT", "RETRY_TIMES", "RETRY_INTERVAL",
]


# ========== 日志安全兜底 ==========
# 打包为 --noconsole 的 EXE 中 sys.stdout/stderr 会被 PyInstaller 置为 None，
# 直接 print() 会抛 AttributeError 导致流程中断，因此这里统一替换为丢弃输出的对象。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")


_LOG_FILE = None
_LOG_STREAM = None


def _log(msg: str):
    """安全打印：任何编码/写入异常（如 GBK 控制台打印 emoji）都不应中断 RPA 主流程。
    若已通过 setup_logging() 开启文件日志，则同步写入日志文件。"""
    try:
        print(msg)
    except Exception:
        pass
    if _LOG_STREAM is not None:
        try:
            _LOG_STREAM.write(msg + "\n")
            _LOG_STREAM.flush()
        except Exception:
            pass


# ========== 日志与异常兜底 ==========

def setup_logging(enabled: bool = True, log_dir: str = None):
    """开启/关闭文件日志。默认写入 ~/rpa_logs/rpa_<时间戳>.log，返回日志文件路径。"""
    global _LOG_FILE, _LOG_STREAM
    if not enabled:
        if _LOG_STREAM is not None:
            try:
                _LOG_STREAM.close()
            except Exception:
                pass
        _LOG_FILE = None
        _LOG_STREAM = None
        return None
    if log_dir is None:
        log_dir = os.path.join(os.path.expanduser("~"), "rpa_logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, time.strftime("rpa_%Y%m%d_%H%M%S.log"))
        _LOG_FILE = log_path
        _LOG_STREAM = open(log_path, "a", encoding="utf-8")
        _log(f"📝 文件日志已开启: {log_path}")
        return log_path
    except Exception as e:
        _log(f"⚠️ 开启文件日志失败: {e}")
        _LOG_FILE = None
        _LOG_STREAM = None
        return None


def run_with_logging(main_func):
    """执行 main 流程并兜底记录日志：正常/异常都写入日志文件；
    发生异常时自动保存失败截图，避免 --noconsole EXE 中错误无处可查。"""
    global _LOG_STREAM
    log_path = setup_logging()
    _log("🚀 流程开始" + (f"（日志: {log_path}）" if log_path else ""))
    try:
        main_func()
        _log("🏁 流程正常结束")
        return True
    except Exception as e:
        _log(f"❌ 流程异常: {e}")
        _log(traceback.format_exc().rstrip())
        try:
            shot = os.path.join(os.path.dirname(log_path) if log_path else ".", "error_screenshot.png")
            take_screenshot(shot)
            _log(f"📸 已保存失败截图: {shot}")
        except Exception:
            pass
        return False
    finally:
        if _LOG_STREAM is not None:
            try:
                _LOG_STREAM.close()
            except Exception:
                pass
            _LOG_STREAM = None


# ========== 桌面操作（同步） ==========

def open_application(app_path: str, wait_seconds: int = 2):
    """打开应用。支持 .exe 绝对路径，也支持系统命令（如 notepad、calc）。"""
    try:
        if app_path.lower().endswith('.exe'):
            subprocess.Popen([app_path])
        else:
            subprocess.Popen(app_path, shell=True)
        time.sleep(wait_seconds)
        _log(f"✅ 已打开应用: {app_path}")
    except Exception as e:
        _log(f"❌ 打开应用失败: {e}")


def activate_window(title: str, partial: bool = True):
    """激活窗口。partial=True 按标题包含匹配；False 要求标题完全匹配。

    注意：Windows 前台窗口锁定可能使 activate() 静默无效，此时可改用
    find_image_on_screen + mouse_click 定位窗口内元素。
    """
    try:
        windows = gw.getWindowsWithTitle(title)
        if not partial:
            windows = [w for w in windows if w.title.strip().lower() == title.strip().lower()]
        if not windows:
            _log(f"❌ 未找到标题为 '{title}' 的窗口")
            return False
        win = windows[0]
        if win.isMinimized:
            win.restore()
        win.activate()
        time.sleep(0.5)
        _log(f"✅ 已激活窗口: {win.title}")
        return True
    except Exception as e:
        _log(f"❌ 激活窗口失败: {e}")
        return False


def mouse_click(x: int, y: int, button: str = 'left', clicks: int = 1):
    pyautogui.click(x, y, button=button, clicks=clicks)
    time.sleep(0.2)
    _log(f"🖱️ 点击 ({x}, {y})")


def mouse_move(x: int, y: int, duration: float = 0.5):
    pyautogui.moveTo(x, y, duration=duration)


def input_text(text: str, interval: float = 0.05):
    """输入文本。统一走"剪贴板复制 + Ctrl+V 粘贴"，免疫中文输入法干扰
    （pyautogui.write 在输入法激活时会把字母打乱）。剪贴板不可用时退回 write。"""
    if copy_clipboard(text):
        time.sleep(0.05)
        press_key("ctrl+v")
        return True
    _log("⚠️ 剪贴板不可用，退回 pyautogui.write")
    try:
        pyautogui.write(text, interval=interval)
        _log(f"⌨️ 输入: {text}")
        return True
    except Exception as e:
        _log(f"❌ 输入失败: {e}")
        return False


def press_key(key: str, presses: int = 1):
    """按键。支持单键（如 'enter'）与 '+' 分隔的组合键（如 'ctrl+s' 等效 Ctrl+S）。

    注意：直接调用 pyautogui.press('ctrl+s') 不会抛出异常，但会静默无效果，
    因此组合键必须拆分为 pyautogui.hotkey(*parts) 处理。
    """
    if "+" in key:
        parts = [p.strip() for p in key.split("+") if p.strip()]
        for _ in range(presses):
            pyautogui.hotkey(*parts)
        _log(f"⌨️ 组合键: {key}")
    else:
        pyautogui.press(key, presses=presses)
        _log(f"⌨️ 按键: {key}")


def take_screenshot(filename: str = "screenshot.png", region: tuple = None):
    if region:
        im = pyautogui.screenshot(region=region)
    else:
        im = pyautogui.screenshot()
    im.save(filename)
    _log(f"📸 截图已保存: {filename}")


def wait(seconds: float):
    time.sleep(seconds)
    _log(f"⏳ 等待 {seconds} 秒")


def wait_for_user(message: str = "请完成手动操作（登录/验证码）后点击确定继续。"):
    """弹 Windows 消息框，等待用户手动完成操作（登录、验证码等）后点击"确定"继续。

    浏览器为可见窗口，弹窗期间可正常操作浏览器。兼容 --noconsole 打包的 EXE。"""
    try:
        import ctypes
        # MB_ICONINFORMATION；hwnd=0 时不阻塞其他应用（可切换到浏览器操作）
        ctypes.windll.user32.MessageBoxW(0, message, "RPA 等待人工操作", 0x40)
        _log("⏳ 人工操作已完成")
        return True
    except Exception as e:
        _log(f"⚠️ 弹窗失败: {e}，改为等待 30 秒")
        wait(30)
        return False


_Point = namedtuple("_Point", ["x", "y"])


def find_image_on_screen(image_path: str, confidence: float = 0.8,
                         tries: int = RETRY_TIMES, interval: float = RETRY_INTERVAL):
    """图像识别定位，未找到时自动重试，最终返回中心坐标（带 .x/.y）或 None。

    使用 OpenCV 模板匹配；通过 np.fromfile + imdecode 读取模板，支持中文路径。
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        _log("⚠️ 未安装 opencv-python，无法进行图像识别（pip install opencv-python）")
        return None

    try:
        template = cv2.imdecode(np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        template = None
    if template is None:
        _log(f"⚠️ 无法读取模板图像: {image_path}")
        return None

    last_err = None
    for i in range(tries):
        try:
            screen = np.array(pyautogui.screenshot())
            screen_bgr = cv2.cvtColor(screen, cv2.COLOR_RGB2BGR)
            res = cv2.matchTemplate(screen_bgr, template, cv2.TM_CCOEFF_NORMED)
            _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(res)
            if max_val >= confidence:
                th, tw = template.shape[:2]
                x = max_loc[0] + tw // 2
                y = max_loc[1] + th // 2
                _log(f"🔍 找到图像 {image_path} 于 ({x},{y}) 置信度 {max_val:.2f}")
                return _Point(x, y)
        except Exception as e:
            last_err = e
        if i < tries - 1:
            time.sleep(interval)
    detail = f"（{last_err}）" if last_err else ""
    _log(f"⚠️ 重试 {tries} 次仍未找到图像 {image_path}{detail}")
    return None


def click_image(image_path: str, confidence: float = 0.8,
                tries: int = RETRY_TIMES, interval: float = RETRY_INTERVAL):
    """识图点击：在屏幕上查找指定图片（模板）并点击其中心。适合识别软件中的图标/按钮。
    返回是否成功。"""
    loc = find_image_on_screen(image_path, confidence=confidence, tries=tries, interval=interval)
    if loc:
        mouse_click(int(loc.x), int(loc.y))
        return True
    return False


def capture_region(region: tuple, filename: str = "template.png"):
    """截取屏幕指定区域并保存为图片，用于制作识图点击的模板图。
    region: (left, top, width, height)。返回保存路径或 None。"""
    try:
        im = pyautogui.screenshot(region=region)
        im.save(filename)
        _log(f"📸 已截取区域并保存模板: {filename}")
        return filename
    except Exception as e:
        _log(f"❌ 截取模板失败: {e}")
        return None


# ========== 剪贴板与中文输入 ==========

def copy_clipboard(text: str):
    """复制文本到剪贴板。中文文本经剪贴板粘贴输入最可靠。"""
    try:
        import win32clipboard
    except ImportError:
        _log("⚠️ 未安装 pywin32，无法操作剪贴板（pip install pywin32）")
        return False
    opened = False
    try:
        win32clipboard.OpenClipboard()
        opened = True
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        _log(f"📋 已复制到剪贴板: {text}")
        return True
    except Exception as e:
        _log(f"❌ 复制到剪贴板失败: {e}")
        return False
    finally:
        if opened:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass


def get_clipboard():
    """读取剪贴板文本，无文本时返回 None。"""
    try:
        import win32clipboard
    except ImportError:
        _log("⚠️ 未安装 pywin32，无法读取剪贴板（pip install pywin32）")
        return None
    opened = False
    try:
        win32clipboard.OpenClipboard()
        opened = True
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
            data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            _log(f"📋 读取剪贴板: {data}")
            return data
        return None
    except Exception as e:
        _log(f"❌ 读取剪贴板失败: {e}")
        return None
    finally:
        if opened:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass


# ========== 鼠标进阶动作 ==========

def double_click(x: int, y: int, button: str = 'left'):
    pyautogui.doubleClick(x, y, button=button)
    time.sleep(0.2)
    _log(f"🖱️ 双击 ({x}, {y})")


def right_click(x: int, y: int):
    pyautogui.rightClick(x, y)
    time.sleep(0.2)
    _log(f"🖱️ 右键点击 ({x}, {y})")


def mouse_drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.5, button: str = 'left'):
    """从 (x1,y1) 按住拖拽到 (x2,y2)。适合框选区域、拖动文件/滑块等。"""
    pyautogui.moveTo(x1, y1, duration=0.2)
    pyautogui.dragTo(x2, y2, duration=duration, button=button)
    time.sleep(0.2)
    _log(f"🖱️ 拖拽 ({x1},{y1}) -> ({x2},{y2})")


def mouse_scroll(amount: int, x: int = None, y: int = None):
    """滚动滚轮：amount 为正向上滚动，为负向下滚动。"""
    pyautogui.scroll(amount, x=x, y=y)
    _log(f"🖱️ 滚动: {amount}")


# ========== 窗口管理增强 ==========

def wait_window(title: str, timeout: float = DEFAULT_TIMEOUT, partial: bool = True):
    """等待标题匹配的窗口出现，返回窗口对象；超时返回 None。比固定 sleep 更稳健。"""
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            windows = gw.getWindowsWithTitle(title)
            if not partial:
                windows = [w for w in windows if w.title.strip().lower() == title.strip().lower()]
            if windows:
                _log(f"✅ 窗口已出现: {windows[0].title}")
                return windows[0]
        except Exception as e:
            last_err = e
        time.sleep(0.3)
    _log(f"⚠️ 等待窗口超时: '{title}'（{last_err}）")
    return None


def get_window_rect(title: str, partial: bool = True):
    """获取窗口位置与大小，返回 (left, top, width, height)；未找到返回 None。"""
    try:
        windows = gw.getWindowsWithTitle(title)
        if not partial:
            windows = [w for w in windows if w.title.strip().lower() == title.strip().lower()]
        if not windows:
            _log(f"❌ 未找到标题为 '{title}' 的窗口")
            return None
        win = windows[0]
        rect = (win.left, win.top, win.width, win.height)
        _log(f"📐 窗口位置: {rect}（标题: {win.title}）")
        return rect
    except Exception as e:
        _log(f"❌ 获取窗口位置失败: {e}")
        return None


def move_window(title: str, x: int, y: int, width: int = None, height: int = None, partial: bool = True):
    """移动窗口并可选调整大小。"""
    try:
        windows = gw.getWindowsWithTitle(title)
        if not partial:
            windows = [w for w in windows if w.title.strip().lower() == title.strip().lower()]
        if not windows:
            _log(f"❌ 未找到标题为 '{title}' 的窗口")
            return False
        win = windows[0]
        if width is None:
            width = win.width
        if height is None:
            height = win.height
        win.resizeTo(width, height)
        win.moveTo(x, y)
        time.sleep(0.3)
        _log(f"📐 已调整窗口: {title} -> ({x},{y},{width}x{height})")
        return True
    except Exception as e:
        _log(f"❌ 调整窗口失败: {e}")
        return False


def close_window(title: str, partial: bool = True):
    """关闭指定标题的窗口（注意：可能触发应用的保存确认提示）。"""
    try:
        windows = gw.getWindowsWithTitle(title)
        if not partial:
            windows = [w for w in windows if w.title.strip().lower() == title.strip().lower()]
        if not windows:
            _log(f"❌ 未找到标题为 '{title}' 的窗口")
            return False
        windows[0].close()
        time.sleep(0.3)
        _log(f"🚪 已关闭窗口: {title}")
        return True
    except Exception as e:
        _log(f"❌ 关闭窗口失败: {e}")
        return False


# ========== 文件操作 ==========

def ensure_dir(path: str):
    """确保目录存在（含父目录），不存在则创建。"""
    try:
        os.makedirs(path, exist_ok=True)
        _log(f"📁 已确保目录: {path}")
        return True
    except Exception as e:
        _log(f"❌ 创建目录失败: {e}")
        return False


def copy_file(src: str, dst: str):
    """复制文件（含属性）。"""
    try:
        import shutil
        shutil.copy2(src, dst)
        _log(f"📄 已复制: {src} -> {dst}")
        return True
    except Exception as e:
        _log(f"❌ 复制文件失败: {e}")
        return False


def move_file(src: str, dst: str):
    """移动/重命名文件。"""
    try:
        import shutil
        shutil.move(src, dst)
        _log(f"📄 已移动: {src} -> {dst}")
        return True
    except Exception as e:
        _log(f"❌ 移动文件失败: {e}")
        return False


def delete_file(path: str):
    """删除文件。"""
    try:
        os.remove(path)
        _log(f"🗑️ 已删除: {path}")
        return True
    except Exception as e:
        _log(f"❌ 删除文件失败: {e}")
        return False


def list_files(directory: str):
    """列出目录下的文件名（不含子目录）；失败返回空列表。"""
    try:
        names = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]
        _log(f"📄 目录 '{directory}' 共 {len(names)} 个文件")
        return names
    except Exception as e:
        _log(f"❌ 列出文件失败: {e}")
        return []


def write_text_file(path: str, content: str, encoding: str = "utf-8"):
    """直接写入文本文件（不弹任何对话框，最可靠）。目录不存在时自动创建。
    适合"保存文档内容"类需求，无需操作 GUI 另存为对话框。"""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding=encoding) as f:
            f.write(content)
        _log(f"✅ 已写入文件: {path}")
        return True
    except Exception as e:
        _log(f"❌ 写入文件失败: {e}")
        return False


def open_file(path: str):
    """用系统默认程序打开文件（如 .txt 自动用记事本打开）。"""
    try:
        os.startfile(os.path.abspath(path))
        _log(f"✅ 已用默认程序打开: {path}")
        return True
    except Exception as e:
        _log(f"❌ 打开文件失败: {e}")
        return False


def read_text_file(path: str, encoding: str = "utf-8"):
    """读取文本文件内容；失败返回 None。"""
    try:
        with open(path, "r", encoding=encoding) as f:
            content = f.read()
        _log(f"📄 已读取文件: {path}（{len(content)} 字符）")
        return content
    except Exception as e:
        _log(f"❌ 读取文件失败: {e}")
        return None


def append_text_file(path: str, content: str, encoding: str = "utf-8"):
    """追加内容到文本文件（不存在则创建）。"""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a", encoding=encoding) as f:
            f.write(content)
        _log(f"📄 已追加到文件: {path}")
        return True
    except Exception as e:
        _log(f"❌ 追加文件失败: {e}")
        return False


def write_json_file(path: str, data, encoding: str = "utf-8"):
    """将数据以 JSON 格式写入文件（中文不转义，便于阅读）。"""
    try:
        import json
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding=encoding) as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        _log(f"✅ 已写入 JSON: {path}")
        return True
    except Exception as e:
        _log(f"❌ 写入 JSON 失败: {e}")
        return False


# ========== 文件对话框与 Excel 操作（同步） ==========

def _iter_controls(dlg):
    """枚举对话框内的所有控件（优先全深度，兼容 uia / win32 后端）。"""
    try:
        return dlg.descendants()
    except Exception:
        return dlg.children()


def _find_filename_edit(dlg):
    """在"另存为"对话框中定位文件名输入框。

    兼容两类对话框：
    - WinUI（Windows 11 新记事本）：优先按已知 AutomationId 匹配；
    - 经典 Win32 对话框：回退为按控件类型/文本启发式匹配。
    """
    # 标准 Windows 文件对话框的文件名输入框 AutomationId（嵌入记事本等 WinUI 窗口时同样适用）
    for auto_id in ("1001", "FileNameControlHost", "FileNameTextBox", "FilenameEdit"):
        try:
            return dlg.child_window(auto_id=auto_id).wrapper_object()
        except Exception:
            continue
    edits = []
    for c in _iter_controls(dlg):
        try:
            cls = c.friendly_class_name()
        except Exception:
            cls = ""
        if cls and ("Edit" in cls or "ComboBox" in cls):
            edits.append(c)
    if edits:
        # 优先选择带文本（含默认文件名）的那个
        for e in edits:
            try:
                if e.texts():
                    return e
            except Exception:
                pass
        return edits[0]
    return None


def _set_edit_text(edit, text):
    try:
        edit.set_text(text)
    except Exception:
        try:
            edit.set_value(text)
        except Exception:
            edit.type_keys("^a{END}" + text)


def _find_button(dlg, keywords=("保存", "Save")):
    for c in _iter_controls(dlg):
        try:
            txt = " ".join(c.texts())
        except Exception:
            txt = ""
        try:
            cls = c.friendly_class_name()
        except Exception:
            cls = ""
        ctype = ""
        try:
            ctype = c.element_info.control_type
        except Exception:
            pass
        if any(k in txt for k in keywords) and (cls == "Button" or ctype == "Button"):
            return c
    return None


def _is_file_dialog(dlg, save_open_keywords):
    """判断窗口是否为真正的文件对话框。

    须同时存在：文件名输入框、"保存/打开"按钮、"取消"按钮。
    "取消"按钮是文件对话框的强特征，用于排除 VS Code 等带"保存"按钮但非对话框的主窗口。
    """
    try:
        if _find_filename_edit(dlg) is None:
            return False
        if _find_button(dlg, save_open_keywords) is None:
            return False
        if _find_button(dlg, ("取消", "Cancel")) is None:
            return False
        return True
    except Exception:
        return False


def _handle_file_dialog(path: str, title_re: str, button_keywords, label: str,
                        overwrite: bool, timeout: float):
    """在原生文件对话框（"另存为"/"打开"）中填入路径并确认。

    优先连接当前活动窗口：对话框弹出后即为活动窗口，故不依赖窗口标题匹配
    （Windows 11 新记事本等 WinUI 应用的对话框，其 HWND 标题并不含"另存为"等文本）。
    只有确认是真正的文件对话框（文件名输入框 + 保存/打开按钮 + 取消按钮齐备）才操作，
    避免把 VS Code 等带"保存"按钮的主窗口误判为对话框。
    """
    try:
        from pywinauto import Application
    except ImportError:
        _log("⚠️ 未安装 pywinauto，无法操作文件对话框（pip install pywinauto）")
        return False

    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        for backend in ("uia", "win32"):
            for strategy in ("active", "title"):
                try:
                    if strategy == "active":
                        app = Application(backend=backend).connect(active_only=True)
                    else:
                        app = Application(backend=backend).connect(title_re=title_re)
                    dlg = app.top_window()
                    if dlg is None:
                        raise RuntimeError("无活动窗口")
                    dlg.wait("ready", timeout=1)
                    if not _is_file_dialog(dlg, button_keywords):
                        raise RuntimeError("当前窗口不是文件对话框（缺文件名输入框或保存/打开/取消按钮）")
                    edit = _find_filename_edit(dlg)
                    _set_edit_text(edit, path)
                    btn = _find_button(dlg, button_keywords)
                    btn.click()
                    time.sleep(1.2)
                    if overwrite:
                        # 覆盖确认：保存后可能弹出"确认替换/确认另存为"
                        try:
                            conf = Application(backend="uia").connect(active_only=True)
                            yes = _find_button(conf.top_window(), ("是", "Yes", "替换", "Replace"))
                            if yes is not None:
                                yes.click()
                                time.sleep(0.5)
                        except Exception:
                            pass
                    # 验证对话框确实关闭，避免"未弹窗却误报成功"
                    try:
                        still = Application(backend="uia").connect(active_only=True).top_window()
                        if still is not None and _is_file_dialog(still, button_keywords):
                            _log(f"⚠️ '{label}'对话框未关闭，保存/打开可能未成功: {path}")
                            return False
                    except Exception:
                        pass
                    _log(f"✅ 已在'{label}'对话框确认: {path}")
                    return True
                except Exception as e:
                    last_err = e
        time.sleep(0.3)
    _log(f"⚠️ 未找到'{label}'对话框: {last_err}\n"
         f"   提示：保存文件建议改用 write_text_file / ExcelFile 等直接写入方式，避免依赖 GUI 对话框")
    return False


def save_dialog(filename: str, overwrite: bool = True, timeout: float = DEFAULT_TIMEOUT):
    """处理当前打开的原生"另存为"对话框：填入文件名并点击保存。

    前置步骤：请先 activate_window 目标窗口，再通过 press_key("ctrl+s") 触发保存，
    弹出对话框后本函数负责改名与保存。优先连接活动窗口，兼容经典 Win32 与 WinUI 对话框。

    参数:
        filename: 目标文件名，如 "test.txt"
        overwrite: 目标文件已存在时是否自动确认覆盖
    """
    return _handle_file_dialog(filename, r"另存为|Save As", ("保存", "Save"),
                               "另存为", overwrite, timeout)


def open_dialog(path: str, timeout: float = DEFAULT_TIMEOUT):
    """处理当前打开的原生"打开"对话框：输入文件路径并确认。

    前置步骤：请先通过 press_key("ctrl+o") 或点击应用内的"打开"按钮触发对话框。
    参数:
        path: 要打开的完整文件路径（推荐绝对路径）
    """
    return _handle_file_dialog(path, r"打开|Open", ("打开", "Open"),
                               "打开", False, timeout)


# ========== Excel 操作（文件级，基于 openpyxl） ==========

class ExcelFile:
    """基于 openpyxl 的 Excel 文件读写（纯文件级操作，无需安装 Microsoft Excel）。

    适合"生成报表 / 批量填表"等不依赖 Excel 界面的场景。
    用法:
        f = ExcelFile(r"C:\\data\\报表.xlsx").create("Sheet1")
        f.write_cell(1, 1, "姓名").write_row(2, ["张三", "李四"])
        f.save()   # 落盘为 报表.xlsx
    """
    def __init__(self, path: str):
        self.path = path
        self.wb = None

    def create(self, sheet_name: str = "Sheet1"):
        from openpyxl import Workbook
        self.wb = Workbook()
        self.wb.active.title = sheet_name
        return self

    def load(self):
        from openpyxl import load_workbook
        self.wb = load_workbook(self.path)
        return self

    def _ws(self, sheet_name=None):
        if self.wb is None:
            raise RuntimeError("请先调用 create() 或 load() 打开 Excel 文件")
        return self.wb[sheet_name] if sheet_name else self.wb.active

    def write_cell(self, row: int, col: int, value, sheet_name: str = None):
        self._ws(sheet_name).cell(row=row, column=col, value=value)
        return self

    def read_cell(self, row: int, col: int, sheet_name: str = None):
        return self._ws(sheet_name).cell(row=row, column=col).value

    def write_row(self, row: int, values, start_col: int = 1, sheet_name: str = None):
        for i, v in enumerate(values):
            self._ws(sheet_name).cell(row=row, column=start_col + i, value=v)
        return self

    # ---------- 区域读写 / 增删改查 ----------

    def read_range(self, row1: int, col1: int, row2: int, col2: int, sheet_name: str = None):
        """读取矩形区域，返回二维列表（行为外层）。"""
        ws = self._ws(sheet_name)
        return [[ws.cell(row=r, column=c).value for c in range(col1, col2 + 1)]
                for r in range(row1, row2 + 1)]

    def write_range(self, row1: int, col1: int, data, sheet_name: str = None):
        """从 (row1, col1) 起写入二维列表 data。"""
        ws = self._ws(sheet_name)
        for i, row in enumerate(data):
            for j, value in enumerate(row):
                ws.cell(row=row1 + i, column=col1 + j, value=value)
        return self

    def sheet_names(self):
        """列出所有工作表名。"""
        return list(self.wb.sheetnames)

    def add_sheet(self, sheet_name: str):
        """新增工作表。"""
        self.wb.create_sheet(sheet_name)
        return self

    def delete_sheet(self, sheet_name: str):
        """删除指定工作表。"""
        if sheet_name in self.wb.sheetnames:
            self.wb.remove(self.wb[sheet_name])
        return self

    def insert_row(self, row: int, sheet_name: str = None):
        """在指定行前插入一个空行。"""
        self._ws(sheet_name).insert_rows(row, 1)
        return self

    def insert_column(self, col: int, sheet_name: str = None):
        """在指定列前插入一个空列。"""
        self._ws(sheet_name).insert_cols(col, 1)
        return self

    def delete_column(self, col: int, count: int = 1, sheet_name: str = None):
        """删除指定列（可指定删除列数）。"""
        self._ws(sheet_name).delete_cols(col, count)
        return self

    def delete_row(self, row: int, count: int = 1, sheet_name: str = None):
        """删除指定行（可指定删除行数）。"""
        self._ws(sheet_name).delete_rows(row, count)
        return self

    def clear_range(self, row1: int, col1: int, row2: int, col2: int, sheet_name: str = None):
        """清空矩形区域的值。"""
        ws = self._ws(sheet_name)
        for r in range(row1, row2 + 1):
            for c in range(col1, col2 + 1):
                ws.cell(row=r, column=c).value = None  # 注意：cell(value=None) 不会清空，须直接赋 None
        return self

    def save(self):
        if self.wb is None:
            raise RuntimeError("没有可保存的工作簿")
        self.wb.save(self.path)
        _log(f"✅ Excel 已保存: {self.path}")
        return self

    def close(self):
        self.wb = None


def create_excel_file(path: str, sheet_name: str = "Sheet1"):
    """新建一个 Excel 文件（含单个工作表）。"""
    try:
        ExcelFile(path).create(sheet_name).save()
        return True
    except Exception as e:
        _log(f"❌ 创建 Excel 失败: {e}")
        return False


def write_excel_cell(path: str, row: int, col: int, value, sheet_name: str = None):
    """向已存在的 Excel 文件写入单元格并保存。"""
    try:
        ExcelFile(path).load().write_cell(row, col, value, sheet_name).save()
        return True
    except Exception as e:
        _log(f"❌ 写入 Excel 失败: {e}")
        return False


def read_excel_cell(path: str, row: int, col: int, sheet_name: str = None):
    """读取 Excel 文件中指定单元格的值。"""
    try:
        return ExcelFile(path).load().read_cell(row, col, sheet_name)
    except Exception as e:
        _log(f"❌ 读取 Excel 失败: {e}")
        return None


def read_excel_range(path: str, row1: int, col1: int, row2: int, col2: int, sheet_name: str = None):
    """读取 Excel 矩形区域，返回二维列表。"""
    try:
        return ExcelFile(path).load().read_range(row1, col1, row2, col2, sheet_name)
    except Exception as e:
        _log(f"❌ 读取 Excel 区域失败: {e}")
        return None


def delete_excel_row(path: str, row: int, count: int = 1, sheet_name: str = None):
    """删除 Excel 中指定行（含下方内容上移）。"""
    try:
        ExcelFile(path).load().delete_row(row, count, sheet_name).save()
        return True
    except Exception as e:
        _log(f"❌ 删除 Excel 行失败: {e}")
        return False


def delete_excel_column(path: str, col: int, count: int = 1, sheet_name: str = None):
    """删除 Excel 中指定列（含右侧内容左移）。"""
    try:
        ExcelFile(path).load().delete_column(col, count, sheet_name).save()
        return True
    except Exception as e:
        _log(f"❌ 删除 Excel 列失败: {e}")
        return False


# ========== Excel 操作（应用级，基于 COM） ==========

class ExcelApp:
    """基于 COM (win32com) 的 Excel 应用程序自动化，需安装 Microsoft Excel。

    适合"打开 Excel 界面并操作"的可见场景（录入数据、另存为等）。
    与 ExcelFile 不同，本类直接控制 Excel 程序窗口。
    用法:
        excel = ExcelApp()
        excel.new_workbook("报表")
        excel.write_cell(1, 1, "姓名").write_row(2, ["张三", "李四"])
        excel.save_as(r"C:\\data\\报表.xlsx")
        excel.close()
    """
    def __init__(self, visible: bool = True):
        try:
            import win32com.client
        except ImportError:
            raise RuntimeError("未安装 pywin32，无法控制 Excel 程序（pip install pywin32）")
        self.app = win32com.client.Dispatch("Excel.Application")
        self.app.Visible = visible
        self.app.DisplayAlerts = False  # 抑制确认弹窗（含覆盖保存提示）
        self.wb = None
        self.ws = None
        _log("📗 Excel 程序已启动")

    def new_workbook(self, sheet_name: str = "Sheet1"):
        self.wb = self.app.Workbooks.Add()
        self.ws = self.wb.ActiveSheet
        self.ws.Name = sheet_name
        return self

    def open_workbook(self, path: str):
        self.wb = self.app.Workbooks.Open(os.path.abspath(path))
        self.ws = self.wb.ActiveSheet
        return self

    def write_cell(self, row: int, col: int, value):
        self.ws.Cells(row, col).Value = value
        return self

    def read_cell(self, row: int, col: int):
        return self.ws.Cells(row, col).Value

    def write_row(self, row: int, values, start_col: int = 1):
        for i, v in enumerate(values):
            self.ws.Cells(row, start_col + i).Value = v
        return self

    def save(self):
        self.wb.Save()
        _log("✅ Excel 已保存")
        return self

    def save_as(self, path: str):
        """另存为。COM 直接写入目标路径，不弹"另存为"对话框。"""
        self.wb.SaveAs(os.path.abspath(path))
        _log(f"✅ Excel 已另存为: {path}")
        return self

    def close(self):
        try:
            if self.wb is not None:
                self.wb.Close(False)
        except Exception as e:
            _log(f"⚠️ 关闭工作簿异常: {e}")
        try:
            self.app.Quit()
        except Exception as e:
            _log(f"⚠️ 退出 Excel 异常: {e}")
        self.wb = None
        self.ws = None
        self.app = None
        _log("📗 Excel 程序已退出")


# ========== 数据采集（OCR / HTTP） ==========

def _get_ocr_engine():
    """获取（并缓存）RapidOCR 引擎。"""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        _log("⚠️ 未安装 rapidocr_onnxruntime，无法识别屏幕文字（pip install rapidocr_onnxruntime）")
        return None
    engine = getattr(_get_ocr_engine, "_engine", None)
    if engine is None:
        _log("⏳ 正在初始化 OCR 引擎（首次约需数秒）...")
        try:
            engine = RapidOCR()
        except Exception as e:
            _log(f"❌ OCR 引擎初始化失败: {e}")
            return None
        _get_ocr_engine._engine = engine
    return engine


def ocr_screen(region: tuple = None):
    """识别屏幕指定区域内的文字（基于 RapidOCR，内置中文识别模型）。

    region: (left, top, width, height)；None 表示全屏。
    返回识别文本（每行一段）；失败返回 None。
    """
    engine = _get_ocr_engine()
    if engine is None:
        return None
    try:
        import numpy as np
        img = pyautogui.screenshot(region=region)
        result, _ = engine(np.array(img))  # result: [[box, text, score], ...] 或 None
        if not result:
            _log("🔍 OCR 未识别到文字")
            return ""
        lines = [item[1] for item in result]
        text = "\n".join(lines)
        _log(f"🔍 OCR 识别完成（{len(lines)} 行）")
        return text
    except Exception as e:
        _log(f"❌ OCR 识别失败: {e}")
        return None


def ocr_screen_boxes(region: tuple = None):
    """识别屏幕指定区域内的文字并返回坐标，供"按文字定位/点击"使用。

    返回 [{text, center:(x,y)}]，center 为识别框中心（屏幕坐标）。
    """
    engine = _get_ocr_engine()
    if engine is None:
        return []
    try:
        import numpy as np
        img = pyautogui.screenshot(region=region)
        result, _ = engine(np.array(img))
        if not result:
            return []
        offset_x, offset_y = (region[0], region[1]) if region else (0, 0)
        items = []
        for box, text, _score in result:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            cx = int((min(xs) + max(xs)) / 2) + offset_x
            cy = int((min(ys) + max(ys)) / 2) + offset_y
            items.append({"text": text, "center": (cx, cy)})
        _log(f"🔍 OCR 识别 {len(items)} 个文字块（含坐标）")
        return items
    except Exception as e:
        _log(f"❌ OCR 识别失败: {e}")
        return []


def click_text(text: str, region: tuple = None):
    """按文字识别并点击屏幕上的文字（如"确定""下一步"等图标化按钮）。

    基于 OCR 坐标定位，无需依赖标准控件/模板图。返回是否成功。
    """
    for it in ocr_screen_boxes(region):
        if text in it["text"]:
            mouse_click(it["center"][0], it["center"][1])
            _log(f"🎯 已点击文字: {text}")
            return True
    _log(f"⚠️ 未在屏幕上找到文字: {text}")
    return False


def http_get(url: str, params: dict = None, headers: dict = None, timeout: int = 30):
    """发送 HTTP GET 请求，返回响应文本；失败返回 None。"""
    try:
        import requests
    except ImportError:
        _log("⚠️ 未安装 requests，无法发起 HTTP 请求")
        return None
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        _log(f"🌐 GET {url} -> {resp.status_code}")
        return resp.text
    except Exception as e:
        _log(f"❌ GET 请求失败: {e}")
        return None


def http_post(url: str, data=None, json=None, headers: dict = None, timeout: int = 30):
    """发送 HTTP POST 请求，返回响应文本；失败返回 None。data 为表单，json 为 JSON 体。"""
    try:
        import requests
    except ImportError:
        _log("⚠️ 未安装 requests，无法发起 HTTP 请求")
        return None
    try:
        resp = requests.post(url, data=data, json=json, headers=headers, timeout=timeout)
        resp.raise_for_status()
        _log(f"🌐 POST {url} -> {resp.status_code}")
        return resp.text
    except Exception as e:
        _log(f"❌ POST 请求失败: {e}")
        return None


# ========== 网页操作（异步） ==========

# Playwright 浏览器使用的模拟 User-Agent，降低被站点识别为自动化的概率
_FAKE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class WebPage:
    """Playwright 网页操作封装。

    支持上下文管理器，推荐用法：async with WebPage() as page:
    退出（含异常）时自动调用 close() 关闭浏览器，避免进程泄漏。
    """
    def __init__(self, headless: bool = False, session_file: str = None):
        self.headless = headless
        self.session_file = session_file
        self.browser = None
        self.page = None
        self.playwright = None
        self.context = None
        self.captured_responses = []
        self._capture_handler = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def start(self):
        self.playwright = await async_playwright().start()
        # 使用更接近真实用户的浏览器指纹，降低被站点识别为自动化（触发验证码）的概率
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        # 若提供了会话文件且存在，则直接加载登录状态（免登录）
        state = os.path.abspath(self.session_file) if (self.session_file and os.path.exists(self.session_file)) else None
        self.context = await self.browser.new_context(
            user_agent=_FAKE_UA,
            viewport={"width": 1366, "height": 768},
            locale="zh-CN",
            storage_state=state,
        )
        self.page = await self.context.new_page()
        _LIVE_PAGES.add(self)
        _log("🌐 浏览器已启动" + (f"（已加载会话 {self.session_file}）" if state else ""))

    async def goto(self, url: str, timeout: int = DEFAULT_TIMEOUT * 1000):
        await self.page.goto(url, timeout=timeout)
        _log(f"🌐 已打开: {url}")

    async def click(self, selector: str, timeout: int = DEFAULT_TIMEOUT * 1000):
        """点击元素。正常 click 超时（如百度首页元素被渲染为零尺寸）时，
        退化为 JS 强制点击（el.click()），可正常触发提交/跳转。"""
        try:
            await self.page.click(selector, timeout=timeout)
        except Exception as e:
            _log(f"⚠️ click 超时（{type(e).__name__}），改用 JS 强制点击")
            await self.page.eval_on_selector(selector, "el => el.click()")
        _log(f"🖱️ 点击元素: {selector}")

    async def fill(self, selector: str, text: str, timeout: int = DEFAULT_TIMEOUT * 1000):
        """填入文本。正常 fill 超时（如百度等站点首屏输入框暂不可见）时，
        退化为强制赋值并派发 input/change 事件，避免流程中断。"""
        try:
            await self.page.fill(selector, text, timeout=timeout)
        except Exception as e:
            _log(f"⚠️ fill 超时（{type(e).__name__}），改用强制赋值方式填入")
            await self.page.eval_on_selector(
                selector,
                "(el, value) => { el.value = value; "
                "el.dispatchEvent(new Event('input', {bubbles: true})); "
                "el.dispatchEvent(new Event('change', {bubbles: true})); }",
                text,
            )
        _log(f"⌨️ 填入 '{text}' 到 {selector}")

    async def screenshot(self, filename: str = "web_screenshot.png"):
        await self.page.screenshot(path=filename)
        _log(f"📸 网页截图已保存: {filename}")

    async def wait_for_selector(self, selector: str, timeout: int = DEFAULT_TIMEOUT * 1000):
        await self.page.wait_for_selector(selector, timeout=timeout)
        _log(f"⏳ 元素已出现: {selector}")

    async def wait(self, seconds: float):
        """等待指定秒数。与顶层 wait() 同名，供异步流程内 await page.wait(...) 调用。"""
        await asyncio.sleep(seconds)
        _log(f"⏳ 等待 {seconds} 秒")

    async def get_text(self, selector: str, timeout: int = DEFAULT_TIMEOUT * 1000):
        """读取元素文本内容。"""
        text = await self.page.inner_text(selector, timeout=timeout)
        _log(f"📖 读取文本: {text}")
        return text

    async def get_attribute(self, selector: str, name: str, timeout: int = DEFAULT_TIMEOUT * 1000):
        """读取元素指定属性的值。"""
        value = await self.page.get_attribute(selector, name, timeout=timeout)
        _log(f"🏷️ 读取属性 {name}: {value}")
        return value

    async def select_option(self, selector: str, value: str = None, label: str = None,
                            timeout: int = DEFAULT_TIMEOUT * 1000):
        """在下拉框中按 value 或 label 选择选项。"""
        if value is not None:
            await self.page.select_option(selector, value=value, timeout=timeout)
        elif label is not None:
            await self.page.select_option(selector, label=label, timeout=timeout)
        _log(f"🔽 已选择: {value or label}")

    async def press_key(self, key: str):
        """在页面上按键，如 'Enter'、'Escape'。"""
        await self.page.keyboard.press(key)
        _log(f"⌨️ 页面按键: {key}")

    async def evaluate(self, expression: str):
        """在页面中执行 JavaScript 并返回结果。"""
        result = await self.page.evaluate(expression)
        _log("🧩 JS 执行完成")
        return result

    async def wait_until_text(self, text: str, timeout: int = DEFAULT_TIMEOUT * 1000):
        """等待页面中出现指定文本（轮询 document.body.innerText）。"""
        await self.page.wait_for_function(
            "t => document.body.innerText.includes(t)", arg=text, timeout=timeout)
        _log(f"⏳ 页面已出现文本: {text}")

    # ---------- 加载自适应 ----------

    async def wait_network_idle(self, timeout: int = DEFAULT_TIMEOUT * 1000):
        """等待页面网络空闲（动态内容加载完成后）。"""
        try:
            await self.page.wait_for_load_state("networkidle", timeout=timeout)
            _log("⏳ 网络已空闲")
        except Exception as e:
            _log(f"⚠️ 等待网络空闲超时: {e}")

    async def auto_scroll_load(self, max_scrolls: int = 10, interval: float = 0.8):
        """自动滚动到底部触发懒加载，直到页面高度不再增长或达到次数上限。"""
        last_height = await self.page.evaluate("document.body.scrollHeight")
        used = 0
        for used in range(1, max_scrolls + 1):
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(interval)
            new_height = await self.page.evaluate("document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height
        _log(f"📜 滚动加载完成（{used} 次）")

    # ---------- 自适应数据提取（不依赖具体选择器） ----------

    async def extract_all_links(self):
        """抓取页面全部链接，返回 [{"text","href","title"}]。"""
        links = await self.page.evaluate("""() => {
            const out = [];
            for (const a of document.querySelectorAll('a[href]')) {
                const href = a.getAttribute('href');
                if (!href || href.startsWith('javascript:')) continue;
                out.push({
                    text: (a.innerText || '').trim(),
                    href: href,
                    title: a.getAttribute('title') || ''
                });
            }
            return out;
        }""")
        _log(f"🔗 提取链接 {len(links)} 条")
        return links

    async def extract_texts(self, selector: str):
        """抓取匹配选择器的所有元素文本（如 'h1,h2,h3'、'a'），返回字符串列表。"""
        texts = await self.page.eval_on_selector_all(
            selector,
            "(els) => els.map(e => (e.innerText || e.textContent || '').trim()).filter(Boolean)")
        _log(f"📖 提取文本 {len(texts)} 项（{selector}）")
        return texts

    async def extract_table(self, index: int = 0):
        """抓取页面中的表格（默认第一个）为二维列表；无表格返回 None。"""
        table = await self.page.evaluate("""(index) => {
            const tables = document.querySelectorAll('table');
            if (!tables[index]) return null;
            const rows = [];
            for (const tr of tables[index].querySelectorAll('tr')) {
                const cells = [];
                for (const td of tr.querySelectorAll('th, td')) {
                    cells.push((td.innerText || '').trim());
                }
                if (cells.length) rows.push(cells);
            }
            return rows;
        }""", index)
        _log(f"📊 提取表格: {len(table) if table else 0} 行")
        return table

    async def extract_article_text(self):
        """抓取页面正文（启发式：优先 article/main，否则取文本最长的块）。"""
        text = await self.page.evaluate("""() => {
            const pick = (els) => {
                let best = '';
                for (const el of els) {
                    const t = (el.innerText || '').trim();
                    if (t.length > best.length) best = t;
                }
                return best;
            };
            const article = document.querySelector('article');
            if (article) return article.innerText.trim();
            const main = document.querySelector('main');
            if (main) return main.innerText.trim();
            return pick(document.querySelectorAll('p, section, div'));
        }""")
        _log(f"📄 提取正文 {len(text) if text else 0} 字符")
        return text

    async def extract_meta(self):
        """抓取页面元信息：标题、描述、关键词。"""
        meta = await self.page.evaluate("""() => {
            const get = (name) => {
                const el = document.querySelector(`meta[name='${name}'], meta[property='og:${name}']`);
                return el ? el.getAttribute('content') : null;
            };
            return { title: document.title, description: get('description'), keywords: get('keywords') };
        }""")
        _log(f"🏷️ 元信息: {meta.get('title')!r}")
        return meta

    async def extract_json_ld(self):
        """抓取页面中 JSON-LD 结构化数据（script[type='application/ld+json']）。"""
        data = await self.page.evaluate("""() => {
            const out = [];
            for (const s of document.querySelectorAll("script[type='application/ld+json']")) {
                try { out.push(JSON.parse(s.textContent)); } catch (e) {}
            }
            return out;
        }""")
        _log(f"🧩 提取 JSON-LD {len(data)} 条")
        return data

    async def extract_regex(self, pattern: str):
        """抓取页面可见文本中匹配正则的所有片段。"""
        import re
        body = await self.page.evaluate("document.body.innerText")
        matches = re.findall(pattern, body)
        _log(f"🔎 正则匹配 {len(matches)} 项")
        return matches

    async def find_by_text(self, text: str, click: bool = True):
        """按可见文本定位元素；click=True 时点击它。
        适合点击"下一页"/"确定"等文本稳定的元素，无需关心选择器。"""
        try:
            locator = self.page.locator(f"text={text}").first
            await locator.wait_for(state="attached", timeout=DEFAULT_TIMEOUT * 1000)
            if click:
                try:
                    await locator.click(timeout=DEFAULT_TIMEOUT * 1000)
                except Exception:
                    _log("⚠️ 按文本点击超时，改用 JS 点击")
                    await locator.evaluate("el => el.click()")
                _log(f"🖱️ 已点击文本: {text}")
            else:
                _log(f"🎯 已定位文本: {text}")
            return True
        except Exception as e:
            _log(f"⚠️ 未找到文本 '{text}': {e}")
            return False

    async def dump_page(self, filename: str = "page_report.json"):
        """导出页面结构报告（标题/链接/表格/输入框等），便于复盘与迭代需求。"""
        report = await self.page.evaluate("""() => {
            const h = (sel) => [...document.querySelectorAll(sel)].map(e => (e.innerText||'').trim()).filter(Boolean).slice(0,20);
            const links = [...document.querySelectorAll('a[href]')].slice(0,50).map(a => ({text:(a.innerText||'').trim().slice(0,50), href:a.href}));
            const inputs = [...document.querySelectorAll('input,select,textarea')].map(e => ({tag:e.tagName, id:e.id, name:e.name, type:e.type||''}));
            return {
                url: location.href,
                title: document.title,
                description: (document.querySelector("meta[name='description']")||{}).content || null,
                h1: h('h1'), h2: h('h2'), h3: h('h3'),
                link_count: document.querySelectorAll('a[href]').length,
                links: links,
                table_count: document.querySelectorAll('table').length,
                inputs: inputs
            };
        }""")
        import json
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        _log(f"📋 页面报告已导出: {filename}")
        return report

    # ---------- 登录与会话持久化 ----------

    async def save_session(self, filename: str = "session.json"):
        """保存当前浏览器会话（Cookie/登录状态）到文件，之后可免登录。"""
        await self.context.storage_state(path=os.path.abspath(filename))
        _log(f"🔐 会话已保存: {filename}")

    async def load_session(self, filename: str = "session.json"):
        """从文件加载会话并重建页面（免登录）。需在 goto 之前调用。"""
        if not os.path.exists(filename):
            _log(f"⚠️ 会话文件不存在: {filename}（首次运行需手动登录后调用 save_session）")
            return False
        self.session_file = filename
        if self.context is not None:
            try:
                await self.context.close()
            except Exception:
                pass
            self.context = await self.browser.new_context(
                user_agent=_FAKE_UA,
                viewport={"width": 1366, "height": 768},
                locale="zh-CN",
                storage_state=os.path.abspath(filename),
            )
            self.page = await self.context.new_page()
        _log(f"🔐 已加载会话: {filename}")
        return True

    async def wait_for_login(self, signal: str = "退出登录", timeout: int = 120 * 1000):
        """等待用户手动登录完成。

        先弹提示窗告知去浏览器登录/验证码；点击确定后等待 signal 出现作为登录成功信号。
        signal 以 '#'/'.' 开头视为 CSS 选择器，否则视为页面文本。"""
        wait_for_user(f"请在浏览器中完成登录/验证码。\n脚本将自动检测登录成功信号：{signal}")
        try:
            if signal.startswith("#") or signal.startswith("."):
                await self.page.wait_for_selector(signal, timeout=timeout)
                _log(f"🔐 登录成功（元素出现）: {signal}")
            else:
                await self.page.wait_for_function(
                    "t => document.body.innerText.includes(t)", arg=signal, timeout=timeout)
                _log(f"🔐 登录成功（文本出现）: {signal}")
            return True
        except Exception as e:
            _log(f"⚠️ 等待登录信号超时: {e}")
            return False

    # ---------- 网络响应捕获（拿接口数据） ----------

    def start_response_capture(self, url_filter: str = None):
        """开始捕获匹配 url_filter（URL 包含的字符串）的 JSON 网络响应。

        捕获结果存于 self.captured_responses，用 get_captured_responses() 读取。
        适合在页面操作（点击/滚动/翻页）之前开启，之后读取接口返回的数据。"""
        self.stop_response_capture()  # 幂等：先停旧捕获
        self.captured_responses = []
        self._capture_filter = url_filter

        async def _on_response(response):
            try:
                url = response.url
                if self._capture_filter and self._capture_filter not in url:
                    return
                if "json" not in response.headers.get("content-type", ""):
                    return
                data = await response.json()
            except Exception:
                return
            self.captured_responses.append({
                "url": url,
                "method": response.request.method,
                "status": response.status,
                "json": data,
            })

        self._capture_handler = _on_response
        self.page.on("response", _on_response)
        _log("🌐 开始捕获网络响应" + (f"（过滤: {url_filter}）" if url_filter else ""))

    def get_captured_responses(self):
        """返回已捕获的响应列表 [{url, method, status, json}]。"""
        return list(getattr(self, "captured_responses", []))

    def stop_response_capture(self):
        """停止捕获网络响应。"""
        handler = getattr(self, "_capture_handler", None)
        if handler is not None:
            try:
                self.page.remove_listener("response", handler)
            except Exception:
                pass
            self._capture_handler = None
        _log("🌐 已停止捕获响应")

    async def wait_for_response(self, url_filter: str, timeout: int = DEFAULT_TIMEOUT * 1000):
        """等待页面发出匹配 url_filter 的网络响应并返回其 JSON 数据。

        应在触发请求的操作之前调用（先挂等待，再触发，如点击加载更多）。
        也可先用 start_response_capture 收集、再 get_captured_responses 读取。"""
        try:
            async with self.page.expect_response(
                    lambda r: url_filter in r.url and "json" in r.headers.get("content-type", ""),
                    timeout=timeout) as resp_info:
                pass
            resp = await resp_info.value
            data = await resp.json()
            _log(f"🌐 捕获响应: {resp.url}")
            return data
        except Exception as e:
            _log(f"⚠️ 等待响应超时: {url_filter}（{type(e).__name__}）")
            return None

    async def close(self):
        """关闭浏览器。幂等：可重复调用；任一步失败都会继续执行后续清理。"""
        if self not in _LIVE_PAGES:
            return
        _LIVE_PAGES.discard(self)
        try:
            if self.browser:
                await self.browser.close()
        except Exception as e:
            _log(f"⚠️ 关闭浏览器异常: {e}")
        finally:
            self.browser = None
            self.page = None
            self.context = None
            if self.playwright:
                try:
                    await self.playwright.stop()
                except Exception as e:
                    _log(f"⚠️ 停止 Playwright 异常: {e}")
                finally:
                    self.playwright = None
        _log("🌐 浏览器已关闭")


# 当前存活（未关闭）的 WebPage 实例，供 run_web_task 在异常时强制清理。
_LIVE_PAGES = set()


def run_web_task(task_func):
    """执行单个异步网页流程。

    - 同一进程内只能调用一次（asyncio.run 限制）。
    - 即使 task_func 中途抛异常，也会在退出前自动关闭所有未关闭的浏览器，
      避免 chromium 进程残留。
    """
    async def _runner():
        try:
            result = task_func()
            if inspect.iscoroutine(result):
                await result
            else:
                _log("⚠️ run_web_task 传入的函数不是 async 函数，已忽略其返回值")
        finally:
            for page in list(_LIVE_PAGES):
                try:
                    await page.close()
                except Exception as e:
                    _log(f"⚠️ 清理浏览器失败: {e}")

    asyncio.run(_runner())
