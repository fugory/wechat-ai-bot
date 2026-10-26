# -*- coding: utf-8 -*-
"""实验 B：能不能在不抢焦点的情况下把文字送进微信输入框。

安全设计：
  * 只粘贴，**绝不按回车**，所以不会发出任何消息
  * 用 PrintWindow 截图（不需要前台），前后对比输入框区域
  * 结束后按 Esc 清空输入框
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time

sys.path.insert(0, r"D:\dsh\projects\ai-chat-bot")
sys.path.insert(0, r"D:\dsh\projects\ai-chat-bot\gui")

try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    pass

import wxwin  # noqa: E402
from PIL import Image  # noqa: E402

OUT = r"D:\dsh\scratch"
TEXT = "B_TEST_1740"

u = ctypes.windll.user32
WM_PASTE = 0x0302
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
VK_ESCAPE = 0x1B


def clip_put(text: str) -> bool:
    try:
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        win32clipboard.CloseClipboard()
        return True
    except Exception as e:
        print("剪贴板失败:", e)
        return False


def grab(hwnd):
    """复用 sticker_match._grab（内部会调 PrintWindow，抓得到 Qt 窗口）。"""
    from sticker_match import _grab
    return _grab(hwnd)


def main():
    h = wxwin.find_wechat()
    if not h:
        print("找不到微信窗口")
        return 1
    print("微信窗口:", hex(h), "客户区:", wxwin.client_size(h))

    # 子窗口（Qt Quick 的渲染宿主）
    kids = []

    def cb(ch, _):
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(ch, cls, 256)
        if "MMUIRender" in cls.value:
            kids.append(ch)
        return True

    u.EnumChildWindows(h, ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    sub = kids[0] if kids else 0
    print("MMUI 子窗口:", hex(sub) if sub else "无")

    before = grab(h)
    if before:
        before.save(OUT + r"\b_before.png")

    if not clip_put(TEXT):
        return 1

    fg_before = u.GetForegroundWindow()
    targets = [("sub", sub), ("main", h)]
    for label, hw in targets:
        if not hw:
            continue
        u.PostMessageW(hw, WM_PASTE, 0, 0)
        time.sleep(1.2)
        img = grab(h)
        if img:
            img.save(OUT + r"\b_after_%s.png" % label)
        print("已向 %s 发送 WM_PASTE（不按回车）" % label)

    fg_after = u.GetForegroundWindow()
    print("前台窗口是否变化:", "否（真正的后台）" if fg_before == fg_after else "变了（抢了焦点）")

    # 清理：Esc 清空输入框（不会发送）
    for hw in (sub, h):
        if hw:
            u.PostMessageW(hw, WM_KEYDOWN, VK_ESCAPE, 0)
            u.PostMessageW(hw, WM_KEYUP, VK_ESCAPE, 0)
            time.sleep(0.3)
    print("已发送 Esc 清理")
    print("截图:", OUT + r"\b_before.png / b_after_sub.png / b_after_main.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
