# -*- coding: utf-8 -*-
"""用 SendInput 做真实 Ctrl+V（PostMessage 传不了修饰键，微信会把 v 当字符）。

SendInput 要求目标窗口在前台，所以我们先 SetForegroundWindow，做完再保持。
"""
import ctypes
import time
from ctypes import wintypes

u = ctypes.windll.user32

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56
VK_RETURN = 0x0D


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _key(vk, up=False):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki.wVk = vk
    inp.ki.wScan = 0
    inp.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
    inp.ki.time = 0
    inp.ki.dwExtraInfo = None
    return inp


def send(seq):
    arr = (INPUT * len(seq))(*seq)
    u.SendInput(len(seq), ctypes.byref(arr), ctypes.sizeof(INPUT))


def focus(hwnd: int) -> bool:
    try:
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)
            time.sleep(0.4)
        u.SetForegroundWindow(hwnd)
        time.sleep(0.2)
        return u.GetForegroundWindow() == hwnd
    except Exception:
        return False


def paste(hwnd: int) -> bool:
    """真实 Ctrl+V。"""
    if not focus(hwnd):
        return False
    send([_key(VK_CONTROL), _key(VK_V), _key(VK_V, True), _key(VK_CONTROL, True)])
    return True


def enter(hwnd: int) -> bool:
    if not focus(hwnd):
        return False
    send([_key(VK_RETURN), _key(VK_RETURN, True)])
    return True
