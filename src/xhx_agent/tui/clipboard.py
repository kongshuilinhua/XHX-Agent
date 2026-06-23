"""跨平台读取系统剪贴板（零依赖）。

Textual 的 ``App.clipboard`` 只保存在 app 内部复制过的文本，从不读取操作系统的
剪贴板（见其源码注释 "only contains text copied in the app, and not text copied
from elsewhere in the OS"）。因此 ``TextArea`` 默认的 Ctrl+V（``action_paste``）在
Windows 上读到的是空字符串，粘不进系统剪贴板里的内容。

这里提供一个不引入第三方依赖的系统剪贴板读取，供 ``ChatInput.action_paste`` 调用。
读取失败时一律返回 ""，绝不抛异常——粘贴失败最多是粘不进，不应让 TUI 崩。
"""

from __future__ import annotations

import subprocess
import sys


def read_clipboard() -> str:
    """返回系统剪贴板中的文本；读取失败或为空时返回 ""。"""
    try:
        if sys.platform == "win32":
            return _read_windows()
        if sys.platform == "darwin":
            return _read_command(["pbpaste"])
        # Linux/BSD：优先 Wayland，再退回 X11 的 xclip / xsel。
        for cmd in (
            ["wl-paste", "--no-newline"],
            ["xclip", "-selection", "clipboard", "-o"],
            ["xsel", "-b"],
        ):
            text = _read_command(cmd)
            if text:
                return text
        return ""
    except Exception:
        return ""


def _read_command(cmd: list[str]) -> str:
    """运行外部剪贴板命令并返回其 stdout；任何失败都返回 ""。"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


def _read_windows() -> str:
    """通过 Win32 剪贴板 API 读取 CF_UNICODETEXT。"""
    import ctypes
    from ctypes import wintypes

    CF_UNICODETEXT = 13  # noqa: N806 Win32 剪贴板格式常量，沿用平台惯例的大写命名
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]

    if not user32.OpenClipboard(None):
        return ""
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ""
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.c_wchar_p(ptr).value or ""
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()
