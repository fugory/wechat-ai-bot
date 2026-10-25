# -*- coding: utf-8 -*-
"""wxbg —— 微信后台收发（不占用屏幕、不需要前台）

原理（全部实测通过）：
    1. MoveWindow 把窗口挪到 (-9999,-9999)：仍是正常窗口，可比最小化更好地处理鼠标消息
    2. PrintWindow 抓图：窗口在屏幕外也能拿到真实画面
    3. UIA/WinRT OCR 读画面上的文字 → 按文字位置点击，而不是写死坐标
    4. PostMessage 投递 WM_CHAR / VK_RETURN 完成输入与发送

为什么要 OCR 定位而不是固定坐标：
    侧栏按最近消息排序，新消息会把会话顶上去，坐标必然漂；按名字找位置才稳。

注意：
    * 最小化（IsIconic）状态下鼠标消息会被 Qt 丢弃，**不要最小化**，要挪走。
    * 组合键（Ctrl+A 等）在后台不可靠，所以本模块不做"清空输入框"，靠调用方保证输入框干净。
"""

from __future__ import annotations

import ctypes
import time
import sys
import os
from ctypes import wintypes
from typing import List, Optional, Tuple

import psutil

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

# 必须声明 DPI 感知：否则 Windows 对窗口坐标做虚拟化（125% 缩放下报告值和实际像素
# 差 1.25 倍），点击整体偏移、截图对不齐。这个坑排查了很久。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.GetWindowDC.restype = ctypes.c_void_p
user32.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]
user32.PrintWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_uint]
user32.PrintWindow.restype = wintypes.BOOL
user32.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.BOOL]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]

WM_CHAR, WM_KEYDOWN, WM_KEYUP = 0x0102, 0x0100, 0x0101
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
MK_LBUTTON = 0x0001
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
MK_RBUTTON = 0x0002
VK_RETURN = 0x0D
PW_RENDERFULLCONTENT = 2

WECHAT_EXES = ("weixin.exe", "wechat.exe")

# 布局参数：一律用「距窗口边缘的固定像素」，不用比例。
# 理由：微信侧栏宽度、行高、工具栏高度都是固定像素，不随窗口缩放；
# 早期用比例坐标，一旦窗口被拖动/缩放就全错（踩过）。
SIDE_W = 276                 # 侧栏宽度
ROW_Y0 = 130                 # 第一行会话中心（距顶）
ROW_DY = 85                  # 行距
SIDE_CLICK_X = 170           # 点会话的横向位置（文字区，别点头像）
TITLE_X0, TITLE_X1 = 290, 640   # 标题横向范围
TITLE_Y0, TITLE_Y1 = 55, 100    # 标题纵向范围
SEARCH_PX = (200, 70)
CHAT_TAB_ICON = (42, 139)      # 左侧图标栏第一个图标（聊天）
SMILEY_DX = 54               # 表情按钮：距聊天区左边
BOTTOM_INPUT = 90           # 输入框中心距底
BOTTOM_TOOLBAR = 40          # 工具栏（表情等）距底
PANEL_CLOSE_RIGHT = 60       # 右侧面板关闭按钮距右
PANEL_CLOSE_TOP = 45
# 表情面板：5 列 × N 行，锚在输入框上方（1100~1400 宽窗口实测通用）
EMOJI_COLS = 5
EMOJI_COLS_X = [86, 193, 300, 407, 514]      # 五列中心（客户区 x，1100x760 实测）
EMOJI_ROWS_Y = [158, 297, 407]               # 主窗口里的近似位置（已不用来点击）
# 表情面板是**独立弹窗窗口**（Qt…ToolSaveBits, 643x643），点击必须发到它自己
PANEL_COLS_X = [101, 212, 320, 430, 540]
PANEL_ROWS_Y = [100, 208, 311, 417]   # 收藏超过 15 个时会有第 4 行
PANEL_TAB_STICKER = (201, 575)   # 面板底部心形 tab（收藏贴纸）
STICKER_BUBBLE_X_LEFT = 446      # 收到的表情气泡中心 x
STICKER_BUBBLE_X_RIGHT = 924     # 自己发的气泡中心 x
STICKER_BUBBLE_Y_FROM_BOTTOM = 272   # 气泡中心距客户区底部
MENU_ADD_ITEM = (132, 55)        # 右键菜单里「添加到表情」的位置（菜单窗口内）                 # 行距
OFFSCREEN = -9999
CANON_W, CANON_H = 1100, 760
MIN_W, MIN_H = 860, 600        # 自定义尺寸的下限（再小输入区/表情面板会被裁）      # 只用于「归位时给个舒服的尺寸」，不强制


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def find_hwnd(restore: bool = True) -> Optional[int]:
    """按进程名 + 标题找微信主窗口。

    坑一：进程里有个不可见的 WxTrayIconMessageWindowClass，面积比主窗口还大，
          按面积取最大会抓错（PrintWindow 全黑）。
    坑二：微信可能被收到托盘，主窗口变成不可见；这时要 ShowWindow 出来。
    """
    pids = {p.info["pid"] for p in psutil.process_iter(["pid", "name"])
            if (p.info.get("name") or "").lower() in WECHAT_EXES}
    if not pids:
        return None
    visible, hidden = [], []

    def cb(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if not cls.value.startswith("Qt") or "Tray" in cls.value:
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        tbuf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, tbuf, n + 1)
        if "微信" not in tbuf.value and "Weixin" not in tbuf.value:
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        area = (r.right - r.left) * (r.bottom - r.top)
        (visible if user32.IsWindowVisible(hwnd) else hidden).append((area, hwnd))
        return True

    user32.EnumWindows(
        ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb), 0)
    if visible:
        return max(visible)[1]
    if hidden and restore:
        hwnd = max(hidden)[1]
        user32.ShowWindow(hwnd, 9)          # SW_RESTORE
        time.sleep(0.8)
        if user32.IsWindowVisible(hwnd):
            return hwnd
    return None


def _cfg_keep_size() -> bool:
    """是否保留用户窗口尺寸：环境变量优先，其次 config.json。"""
    import json as _json
    env = os.environ.get("WXBG_KEEP_SIZE")
    if env is not None:
        return env == "1"
    base = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) \
        else os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(base, "config.json"),):
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                return bool((_json.load(f).get("wechat") or {}).get("keep_size"))
        except Exception:
            continue
    return False


class BgWeChat:
    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.hwnd = find_hwnd()
        if not self.hwnd:
            raise RuntimeError("没找到微信窗口（微信没登录或已退出）")
        self.orig = self._rect()
        # 上次进程被强杀时窗口会留在屏幕外，不能把这个坐标当成"原位置"记住
        if self.orig[0] < -1000 or self.orig[1] < -1000:
            self._log("窗口停在屏幕外（上次未正常退出），先归位")
            self.orig = (200, 150, 0, 0)
            self._move_client_size(200, 150, CANON_W, CANON_H)
            time.sleep(0.4)
            self.orig = self._rect()
        self._offscreen = False
        self._ocr_ready = False
        self._ensure_canon()

    # ---------- 基础设施 ----------
    def _log(self, msg: str) -> None:
        if self.verbose:
            print("[wxbg] " + msg, flush=True)

    def _rect(self) -> Tuple[int, int, int, int]:
        r = wintypes.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)

    def _client(self) -> Tuple[int, int, int, int]:
        """客户区尺寸 + 客户区原点相对窗口的偏移。

        WM_LBUTTONDOWN 的 lParam 用的是**客户区坐标**，而截图/OCR 拿到的是整窗图。
        两者差一个标题栏高度，不换算就会点偏——这是踩过的坑。
        """
        r = wintypes.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        c = wintypes.RECT()
        user32.GetClientRect(self.hwnd, ctypes.byref(c))
        pt = wintypes.POINT(0, 0)
        user32.ClientToScreen(self.hwnd, ctypes.byref(pt))
        return (pt.x - r.left, pt.y - r.top, c.right - c.left, c.bottom - c.top)

    def _frame(self) -> Tuple[int, int]:
        _, _, w, h = self._rect()
        _, _, cw, ch = self._client()
        return (max(0, w - cw), max(0, h - ch))

    def _move_client_size(self, x: int, y: int, cw: int, ch: int) -> None:
        fw, fh = self._frame()
        user32.MoveWindow(self.hwnd, x, y, cw + fw, ch + fh, True)

    def _ensure_canon(self) -> None:
        """尺寸策略。

        * 默认（WXBG_KEEP_SIZE=1）：**保留用户尺寸**，只拦"太小"
          —— 因为点击已经改成 UIA 定位，不再依赖 1100x760
        * 未开启：沿用旧行为，一律归一到 1100x760（可回退）

        太小的界面上输入区和表情面板会被裁掉，所以设一个下限。
        """
        keep = _cfg_keep_size()
        _, _, cw, ch = self._client()
        if keep:
            minw, minh = MIN_W, MIN_H
            if cw >= minw and ch >= minh:
                return
            tw, th = max(cw, minw), max(ch, minh)
            self._log("客户区 %dx%d 太小，放大到 %dx%d" % (cw, ch, tw, th))
            self._move_client_size(self.orig[0], self.orig[1], tw, th)
            time.sleep(0.5)
            self.orig = self._rect()
            return
        if (cw, ch) != (CANON_W, CANON_H):
            self._log("客户区 %dx%d 调整为 %dx%d" % (cw, ch, CANON_W, CANON_H))
            self._move_client_size(self.orig[0], self.orig[1], CANON_W, CANON_H)
            time.sleep(0.5)
            self.orig = self._rect()

    def move_offscreen(self) -> None:
        """移出屏幕（尺寸保持不变）。"""
        if self._offscreen:
            return
        _, _, cw, ch = self._client()
        self._move_client_size(OFFSCREEN, OFFSCREEN, cw, ch)
        time.sleep(0.6)
        self._offscreen = True
        self._log("窗口已移出屏幕（客户区 %dx%d）" % (cw, ch))
        try:
            self._close_panels()          # 上次误触留下的面板会毁掉布局
        except Exception as e:
            self._log("清理面板失败（忽略）：%s" % e)

    def move_back(self) -> None:
        if not self._offscreen:
            return
        _, _, cw, ch = self._client()
        self._move_client_size(self.orig[0], self.orig[1], cw, ch)
        self._offscreen = False
        self._log("窗口已归位")

    def shot(self):
        """PrintWindow 抓图（屏幕外也可用），并裁成**客户区**，坐标体系统一。"""
        from PIL import Image
        _, _, w, h = self._rect()
        hdc = user32.GetWindowDC(self.hwnd)
        mdc = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        gdi32.SelectObject(mdc, bmp)
        user32.PrintWindow(self.hwnd, mdc, PW_RENDERFULLCONTENT)
        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth, bi.biHeight = w, -h
        bi.biPlanes, bi.biBitCount = 1, 32
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(self.hwnd, hdc)

        ox, oy, cw, ch = self._client()
        if cw > 0 and ch > 0:
            img = img.crop((ox, oy, ox + cw, oy + ch))
        return img

    def ocr_region(self, box: Tuple[int, int, int, int],
                   tries: int = 3, scale: int = 3) -> List[Tuple[str, int, int]]:
        """OCR 客户区里的绝对像素矩形 (x0, y0, x1, y1)，返回 [(文字, x, y)]。"""
        from wechatauto import ScreenOCR
        if not self._ocr_ready:
            try:
                ScreenOCR.available()        # 首次调用做引擎预热
            except Exception:
                pass
            self._ocr_ready = True

        for attempt in range(tries):
            img = self.shot()
            x0, y0, x1, y1 = box
            crop = img.crop((x0, y0, x1, y1))
            crop = crop.resize((crop.width * scale, crop.height * scale))
            out = []
            try:
                for text, x, y, bw, bh in (ScreenOCR.recognize(crop) or []):
                    out.append((text, x0 + x // scale + bw // (2 * scale),
                                y0 + y // scale + bh // (2 * scale)))
            except Exception as e:
                self._log("OCR 失败：%s" % e)
            if out:
                return out
            time.sleep(0.35)
        return []

    # ---------- 消息投递 ----------
    def _post_click(self, x: int, y: int) -> None:
        lp = (y << 16) | (x & 0xFFFF)
        user32.PostMessageW(self.hwnd, WM_MOUSEMOVE, 0, lp)
        time.sleep(0.06)
        user32.PostMessageW(self.hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
        time.sleep(0.06)
        user32.PostMessageW(self.hwnd, WM_LBUTTONUP, 0, lp)


    # ---------------- UIA 定位（优先于硬编码坐标）----------------

    def uia_center(self, pred):
        """在 UIA 树里找元素，返回它中心的**客户区坐标**；找不到返回 None。"""
        try:
            import bridge
            u = bridge.get_uia()
            if u is None or not u.ensure_window():
                return None
            from wechatauto.uia_driver import _find_by
            el = _find_by(u._win, pred, max_depth=30)
            if el is None:
                return None
            r = el.BoundingRectangle
            if r.right <= r.left or r.bottom <= r.top:
                return None
            sx = (r.left + r.right) // 2
            sy = (r.top + r.bottom) // 2
            wr = self._rect()
            cx_off, cy_off, _cw, _ch = self._client()
            return (int(sx - wr[0] - cx_off), int(sy - wr[1] - cy_off))
        except Exception:
            return None

    def uia_chat_title(self):
        """当前会话标题（UIA 读，读不到返回空串）。"""
        try:
            import bridge
            u = bridge.get_uia()
            if u is None or not u.ensure_window():
                return ""
            from wechatauto.uia_driver import _find_by
            el = _find_by(u._win, lambda e: (e.AutomationId or "") == "content_view.top_content",
                          max_depth=30)
            return (el.Name or "").strip() if el is not None else ""
        except Exception:
            return ""

    # ---------------- 坐标一致性（不依赖缓存）----------------

    def refresh_window(self) -> bool:
        """重新绑定窗口并恢复显示，返回是否可用。"""
        try:
            import bridge
            h = None
            try:
                import wxwin
                h = wxwin.find_wechat()
            except Exception:
                h = None
            if not h:
                u = bridge.get_uia()
                if u and u.ensure_window():
                    h = getattr(u, "hwnd", None) or getattr(u, "_hwnd", None)
            if not h:
                return False
            self.hwnd = int(h)
            if user32.IsIconic(self.hwnd):
                user32.ShowWindow(self.hwnd, 9)      # SW_RESTORE
                time.sleep(0.45)
            new_rect = self._rect()
            old = getattr(self, "orig", None)
            # 尺寸变了就重置 UIA：否则元素坐标还是按旧布局算的，会点偏
            if old and (old[2] != new_rect[2] or old[3] != new_rect[3]):
                try:
                    import bridge
                    bridge.reset_uia()
                    time.sleep(0.35)
                except Exception:
                    pass
            self.orig = new_rect
            return True
        except Exception as e:
            self._log("refresh_window 失败：%s" % e)
            return False

    def uia_screen_center(self, pred):
        """UIA 元素中心的**屏幕坐标**（UIA 本来就返回屏幕坐标，不做换算）。"""
        try:
            import bridge
            u = bridge.get_uia()
            if u is None or not u.ensure_window():
                return None
            from wechatauto.uia_driver import _find_by
            el = _find_by(u._win, pred, max_depth=30)
            if el is None:
                return None
            r = el.BoundingRectangle
            if r.right <= r.left or r.bottom <= r.top:
                return None
            return ((r.left + r.right) // 2, (r.top + r.bottom) // 2)
        except Exception:
            return None

    def click_screen(self, sx: int, sy: int) -> None:
        """真实鼠标点击（屏幕坐标）。能拿到键盘焦点，PostMessage 拿不到。"""
        try:
            user32.SetCursorPos(int(sx), int(sy))
            time.sleep(0.08)
            user32.mouse_event(0x0002, 0, 0, 0, 0)   # LEFTDOWN
            time.sleep(0.05)
            user32.mouse_event(0x0004, 0, 0, 0, 0)   # LEFTUP
        except Exception as e:
            self._log("真实点击失败：%s" % e)

    def focus_input_real(self) -> bool:
        """用一个真实的鼠标点击把输入框激活（拿到键盘焦点）。"""
        if not self.refresh_window():
            return False
        c = self.uia_screen_center(lambda e: (e.AutomationId or "") == "chat_input_field")
        if not c:
            self._log("找不到输入框（UIA）")
            return False
        self.click_screen(*c)
        time.sleep(0.35)
        return True

    def scale(self):
        """实际客户区相对标定尺寸(1100x760)的比例，用于缩放剩余硬编码坐标。"""
        cw, ch = self._client()[2:]
        return (cw / 1100.0 if cw else 1.0, ch / 760.0 if ch else 1.0)

    def click_chat_tab(self) -> None:
        """点左侧「微信」图标，回到聊天列表。UIA 优先，失败退回固定坐标。"""
        c = self.uia_center(lambda e: (e.Name or "") == "微信"
                            and "Button" in (e.ControlTypeName or ""))
        if c:
            self.click_px(*c)
            return
        self.click_px(*CHAT_TAB_ICON)

    def click_px(self, x: int, y: int) -> None:
        self._post_click(int(x), int(y))

    def click_ratio(self, rx: float, ry: float) -> None:
        _, _, w, h = self._client()
        self._post_click(int(w * rx), int(h * ry))

    def click_input(self) -> None:
        """点输入框。UIA 优先（字体/尺寸变了也不会点偏），失败退回旧坐标。"""
        c = self.uia_center(lambda e: (e.AutomationId or "") == "chat_input_field")
        if c:
            self.click_px(*c)
            return
        _, _, cw, ch = self._client()
        self.click_px(SIDE_W + (cw - SIDE_W) // 2, ch - BOTTOM_INPUT)

    # ---------- 表情面板 ----------
    def open_emoji_panel(self) -> bool:
        self.click_smiley()
        time.sleep(1.2)
        return True

    def close_emoji_panel(self) -> bool:
        """关掉表情面板（按 ESC）。面板是切换式的，用完不关会影响下一次点击。"""
        try:
            self.activate()
            user32.PostMessageW(self.hwnd, 0x0100, 0x1B, 0)   # WM_KEYDOWN VK_ESCAPE
            user32.PostMessageW(self.hwnd, 0x0101, 0x1B, 0)   # WM_KEYUP
            time.sleep(0.3)
            return self.find_emoji_panel() is None
        except Exception:
            return False

    def find_emoji_panel(self) -> Optional[int]:
        """找表情面板那个独立弹窗（类名含 ToolSaveBits，宽度 > 500）。"""
        pids = {p.info["pid"] for p in psutil.process_iter(["pid", "name"])
                if (p.info.get("name") or "").lower() in WECHAT_EXES}
        found = []

        def cb(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in pids:
                return True
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if "ToolSaveBits" not in cls.value:
                return True
            if not user32.IsWindowVisible(hwnd):
                return True
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            if (r.right - r.left) > 500:
                found.append((r.right - r.left, hwnd))
            return True

        user32.EnumWindows(
            ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb), 0)
        return max(found)[1] if found else None

    def right_click_px(self, x: int, y: int) -> None:
        """在工作区投递一次右键点击（客户区坐标）。"""
        lp = (y << 16) | (x & 0xFFFF)
        user32.PostMessageW(self.hwnd, WM_MOUSEMOVE, 0, lp)
        time.sleep(0.08)
        user32.PostMessageW(self.hwnd, WM_RBUTTONDOWN, MK_RBUTTON, lp)
        time.sleep(0.08)
        user32.PostMessageW(self.hwnd, WM_RBUTTONUP, 0, lp)

    def find_menu_hwnd(self) -> Optional[int]:
        """找右键菜单那个弹窗：窄而高的 ToolSaveBits 窗口（表情面板是 643x643，不会被误选）。"""
        pids = {p.info["pid"] for p in psutil.process_iter(["pid", "name"])
                if (p.info.get("name") or "").lower() in WECHAT_EXES}
        found = []

        def cb(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in pids or not user32.IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if "ToolSaveBits" not in cls.value:
                return True
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            wdt, hgt = r.right - r.left, r.bottom - r.top
            if 150 < wdt < 500 and hgt > 200:
                found.append(hwnd)
            return True

        user32.EnumWindows(
            ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb), 0)
        return found[0] if found else None

    def click_menu_item(self, mx: int, my: int) -> bool:
        """往菜单弹窗里点一项。"""
        menu = self.find_menu_hwnd()
        if not menu:
            self._log("没找到右键菜单窗口")
            return False
        lp = (my << 16) | (mx & 0xFFFF)
        user32.PostMessageW(menu, WM_MOUSEMOVE, 0, lp)
        time.sleep(0.08)
        user32.PostMessageW(menu, WM_LBUTTONDOWN, MK_LBUTTON, lp)
        time.sleep(0.08)
        user32.PostMessageW(menu, WM_LBUTTONUP, 0, lp)
        return True

    def menu_items_text(self) -> str:
        """OCR 整张右键菜单，返回按行拼接的文字。

        之前只裁 y=8..80 一条，把第一项切掉了——OCR 读到的是第二项「转发」，
        于是把"有添加到表情"误判成"没有"（实测）。
        """
        menu = self.find_menu_hwnd()
        if not menu:
            return ""
        try:
            import ctypes as _ct
            from ctypes import wintypes as _wt
            from PIL import ImageGrab
            r = _wt.RECT()
            user32.GetWindowRect(menu, _ct.byref(r))
            img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom),
                                 all_screens=True)
            img = img.resize((img.width * 2, img.height * 2))
            from wechatauto import ScreenOCR
            if not self._ocr_ready:
                try:
                    ScreenOCR.available()
                except Exception:
                    pass
                self._ocr_ready = True
            items = ScreenOCR.recognize(img) or []
            lines = []
            for it in items:
                try:
                    text, x, y, bw, bh = it
                except Exception:
                    continue
                lines.append((y, str(text).strip()))
            lines.sort(key=lambda p: p[0])
            return "\n".join(t for _, t in lines if t)
        except Exception as e:
            self._log("菜单 OCR 失败：%s" % e)
            return ""

    def menu_has_add(self) -> bool:
        """菜单里有没有「添加到表情」这一项（只在未收藏的表情上出现）。"""
        text = self.menu_items_text()
        return "添加" in (text or "")

    def find_forward_dialog(self) -> Optional[int]:
        """找「微信发送给」转发窗口。"""
        pids = {p.info["pid"] for p in psutil.process_iter(["pid", "name"])
                if (p.info.get("name") or "").lower() in WECHAT_EXES}
        found = []

        def cb(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in pids or not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if "发送给" in buf.value:
                found.append(hwnd)
            return True

        user32.EnumWindows(
            ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb), 0)
        return found[0] if found else None

    def close_forward_dialog(self) -> bool:
        """关掉转发窗口（ESC 优先，不行就 WM_CLOSE）。"""
        hwnd = self.find_forward_dialog()
        if not hwnd:
            return False
        user32.PostMessageW(hwnd, WM_KEYDOWN, 0x1B, 0)
        time.sleep(0.05)
        user32.PostMessageW(hwnd, WM_KEYUP, 0x1B, 0xC0000001)
        time.sleep(0.6)
        if user32.IsWindowVisible(hwnd):
            user32.PostMessageW(hwnd, 0x0010, 0, 0)      # WM_CLOSE
            time.sleep(0.6)
        self._log("关掉了一个误开的转发窗口")
        return True

    def click_menu_item_safe(self) -> bool:
        """点菜单第一项，随后检查是否误触了转发。

        菜单 OCR 不可靠（实测），所以不靠它判断，改为"点完立刻检查副作用"。
        返回 False 表示这次偷表情失败（可能点了转发，已收拾）。
        """
        self.menu_items_text()          # 仅记录，便于排查
        if not self.click_menu_item(MENU_ADD_ITEM[0], MENU_ADD_ITEM[1]):
            return False
        time.sleep(1.2)
        if self.find_forward_dialog():
            self.close_forward_dialog()
            self._log("这次点了「转发」，已关掉弹窗（这张多半已经收藏过）")
            return False
        return True

    def steal_latest_sticker(self, mine: bool = False) -> bool:
        """右键「最新一条表情消息」并点「添加到表情」。

        前置条件：目标表情是当前会话最后一条消息，且还没被收藏。
        调用方必须先用数据库确认这两点，否则会点到「转发…」。
        """
        _, _, _, ch = self._client()
        x = STICKER_BUBBLE_X_RIGHT if mine else STICKER_BUBBLE_X_LEFT
        y = ch - STICKER_BUBBLE_Y_FROM_BOTTOM
        self.right_click_px(x, y)
        time.sleep(1.2)
        ok = self.click_menu_item_safe()
        time.sleep(2.0)
        return ok

    def _click_panel_at(self, panel: int, x: int, y: int) -> None:
        """往表情面板那个独立窗口投递一次左键点击（坐标是面板客户区）。"""
        lp = (y << 16) | (x & 0xFFFF)
        user32.PostMessageW(panel, WM_MOUSEMOVE, 0, lp)
        time.sleep(0.08)
        user32.PostMessageW(panel, WM_LBUTTONDOWN, MK_LBUTTON, lp)
        time.sleep(0.08)
        user32.PostMessageW(panel, WM_LBUTTONUP, 0, lp)

    def click_panel_cell(self, index: int) -> bool:
        """点面板里的第 index 个表情。必须发到面板窗口，发主窗口没用（实测）。"""
        panel = self.find_emoji_panel()
        if not panel:
            self._log("没找到表情面板窗口")
            return False
        # 先点「收藏贴纸」tab：面板可能停在表情 tab，直接点格子会插入 😊 字符
        self._click_panel_at(panel, PANEL_TAB_STICKER[0], PANEL_TAB_STICKER[1])
        time.sleep(0.6)
        col = index % len(PANEL_COLS_X)
        row = index // len(PANEL_COLS_X)
        if row >= len(PANEL_ROWS_Y):
            self._log("表情索引 %d 超出已标定范围" % index)
            return False
        x, y = PANEL_COLS_X[col], PANEL_ROWS_Y[row]
        self._click_panel_at(panel, x, y)
        time.sleep(0.5)
        self._log("点击面板第 %d 格（%d,%d）" % (index, x, y))
        return True

    def click_emoji_cell(self, index: int) -> None:
        """点第 index 个收藏表情（0 基，左上到右下，每行 5 个）。

        面板锚在输入框上方：纵向按「距底」算、横向按「距左」算，
        这样窗口缩放时坐标不会漂。
        """
        col = index % EMOJI_COLS
        row = index // EMOJI_COLS
        if row >= len(EMOJI_ROWS_Y) or col >= len(EMOJI_COLS_X):
            self._log('表情索引 %d 超出已标定范围（最多 %d 个）' % (index, len(EMOJI_ROWS_Y) * EMOJI_COLS))
            return
        self.click_px(EMOJI_COLS_X[col], EMOJI_ROWS_Y[row])

    def send_emoji(self, index: int) -> bool:
        """发第 index 个收藏表情；调用方需用数据库确认真发出去。"""
        self.open_emoji_panel()
        ok = self.click_panel_cell(index)
        time.sleep(0.8)
        return ok

    def click_smiley(self) -> None:
        """点表情按钮。

        注意：这里**故意不用 UIA 坐标**。实测用 UIA 报的中心点去点，
        面板不弹（原因未查明），而固定坐标一直稳定，所以优先用坐标。
        """
        _, _, cw, ch = self._client()
        x = SIDE_W + SMILEY_DX
        y = ch - BOTTOM_TOOLBAR
        self.click_px(x, y)
        time.sleep(0.35)
        if self.find_emoji_panel():
            return
        # 兜底：UIA 报的位置再试一次
        c = self.uia_center(lambda e: "发送表情" in (e.Name or ""))
        if c:
            self.click_px(*c)

    def type_text(self, text: str) -> None:
        for ch in text:
            user32.PostMessageW(self.hwnd, WM_CHAR, ord(ch), 1)
            time.sleep(0.02)

    def dismiss_overlays(self) -> None:
        """点一下聊天区空白处，收起表情面板等浮层。

        不要用 ESC：在微信里 ESC 会把当前聊天一起关掉（实测），后面就没得发了。
        """
        _, _, cw, ch = self._client()
        self.click_px(SIDE_W + (cw - SIDE_W) // 2, 200)
        time.sleep(0.4)

    def paste(self) -> None:
        """Ctrl+V 粘贴（用于把剪贴板里的图片贴进输入框）。"""
        VK_CONTROL, VK_V = 0x11, 0x56
        user32.PostMessageW(self.hwnd, WM_KEYDOWN, VK_CONTROL, 0)
        user32.PostMessageW(self.hwnd, WM_KEYDOWN, VK_V, 0)
        user32.PostMessageW(self.hwnd, WM_KEYUP, VK_V, 0)
        user32.PostMessageW(self.hwnd, WM_KEYUP, VK_CONTROL, 0)

    def press_enter(self) -> None:
        user32.PostMessageW(self.hwnd, WM_KEYDOWN, VK_RETURN, 0x001C0001)
        time.sleep(0.05)
        user32.PostMessageW(self.hwnd, WM_KEYUP, VK_RETURN, 0xC01C0001)

    def clear_input(self) -> None:
        """退格清空。组合键不可靠，用固定次数退格兜底。"""
        for _ in range(60):
            user32.PostMessageW(self.hwnd, WM_KEYDOWN, 0x08, 0)
            user32.PostMessageW(self.hwnd, WM_KEYUP, 0x08, 0)
        time.sleep(0.1)

    # ---------- 业务 ----------
    @staticmethod
    def _best_match(items: List[Tuple[str, int, int]], name: str):
        """OCR 有噪声（"文件传输助手"可能读成"文牛传一"），用相似度挑最像的那行。"""
        import difflib
        best, score = None, 0.0
        for text, x, y in items:
            r = difflib.SequenceMatcher(None, text, name).ratio()
            if name and name in text:
                r = max(r, 0.95)
            if r > score:
                best, score = (text, x, y), r
        return best, score

    def _close_panels(self) -> None:
        """右侧常被误触打开「AI 搜索」面板：它会把聊天区挤窄，标题 OCR 直接读空。"""
        _, _, cw, ch = self._client()
        items = self.ocr_region((cw - 300, 20, cw - 5, 110), tries=1, scale=3)
        blob = " ".join(t for t, _, _ in items)
        if "搜索" in blob or "AI" in blob:
            self.click_px(cw - PANEL_CLOSE_RIGHT, PANEL_CLOSE_TOP)
            time.sleep(0.9)
            self._log("检测到右侧面板，已关闭")

    def current_title(self) -> str:
        """多尺度尝试：同一个标题在不同缩放下 OCR 成功率差别很大。"""
        for scale in (3, 5, 4):
            items = self.ocr_region((TITLE_X0, TITLE_Y0, TITLE_X1, TITLE_Y1),
                                    tries=1, scale=scale)
            if items:
                items.sort(key=lambda t: t[1])
                text = items[0][0].strip()
                if text:
                    return text
        return ""

    def title_matches(self, name: str, threshold: float = 0.6) -> bool:
        title = self.current_title()
        if not title:
            return False
        if name in title or title in name:
            return True
        import difflib
        return difflib.SequenceMatcher(None, title, name).ratio() >= threshold

    def open_chat(self, name: str, tries: int = 2) -> bool:
        """按名字打开会话：点搜索 → 输入全名 → OCR 找那行 → 点它 → 验标题。"""
        for attempt in range(1, tries + 1):
            if self.title_matches(name):
                return True
            self.click_px(SEARCH_PX[0], SEARCH_PX[1])
            time.sleep(0.6)
            self.clear_input()
            self.type_text(name)
            time.sleep(1.3)

            _, _, _cw, _ch = self._client()
            items = self.ocr_region((0, 70, SIDE_W, _ch - 10))
            best, score = self._best_match(items, name)
            if best and score >= 0.5:
                text, tx, ty = best
                self._log("命中候选「%s」相似度 %.2f" % (text, score))
                self._post_click(tx, ty)
                time.sleep(1.2)
                if self.title_matches(name):
                    self._log("已切到「%s」" % name)
                    return True
                self._log("点击后标题是「%s」，不是「%s」（第 %d 次）"
                          % (self.current_title(), name, attempt))
            else:
                self._log("搜索结果没有像「%s」的行（第 %d 次）" % (name, attempt))

            self.clear_input()
            time.sleep(0.4)
        return False

    def activate(self) -> bool:
        """把窗口弄到前台并激活。

        Qt 对"非活动窗口"的投递鼠标消息常常直接忽略——不激活就点不动侧栏。
        先用 SetForegroundWindow，失败再 AttachThreadInput 强行接一下输入队列。
        """
        if user32.IsIconic(self.hwnd):
            user32.ShowWindow(self.hwnd, 9)
            time.sleep(0.4)
        user32.BringWindowToTop(self.hwnd)
        user32.SetForegroundWindow(self.hwnd)
        time.sleep(0.35)
        if user32.GetForegroundWindow() == self.hwnd:
            return True
        try:
            fg = user32.GetForegroundWindow()
            t1 = user32.GetWindowThreadProcessId(fg, None)
            t2 = user32.GetWindowThreadProcessId(self.hwnd, None)
            user32.AttachThreadInput(t1, t2, True)
            user32.SetForegroundWindow(self.hwnd)
            user32.AttachThreadInput(t1, t2, False)
            time.sleep(0.35)
        except Exception as e:
            self._log("强制激活失败：%s" % e)
        ok = user32.GetForegroundWindow() == self.hwnd
        if not ok:
            self._log("警告：窗口未能激活，点击可能无效")
        return ok

    def is_foreground(self) -> bool:
        """微信是不是当前前台窗口——用来判断"用户现在是不是正看着它"。"""
        return user32.GetForegroundWindow() == self.hwnd

    def ensure_offscreen(self) -> None:
        if not self._offscreen:
            self.move_offscreen()

    def ensure_onscreen(self) -> None:
        """确保窗口真的在屏幕内。

        不能用 self._offscreen 判断：新进程不知道窗口还停在上次的 (-9999)。
        """
        if user32.IsIconic(self.hwnd):
            user32.ShowWindow(self.hwnd, 9)      # SW_RESTORE：最小化时 UIA 读不到内容
            time.sleep(0.5)
        r = self._rect()
        if self._offscreen or r[0] < -1000 or r[1] < -1000:
            self._offscreen = True
            self.move_back()

    def send(self, name: str, text: str) -> bool:
        """发一条文本。调用方负责再用数据库确认落库。"""
        if not self.open_chat(name):
            self._log("切换会话失败，放弃发送（避免误发到别的聊天）")
            return False
        self.click_input()
        time.sleep(0.2)
        self.type_text(text)
        time.sleep(0.3)
        self.press_enter()
        self._log("已发送给「%s」：%s" % (name, text[:20]))
        return True

    # ---------- 按索引切会话（推荐路径） ----------
    def click_session(self, index: int) -> None:
        self.click_px(SIDE_CLICK_X, ROW_Y0 + index * ROW_DY)

    def open_index(self, index: int, name: str, span: int = 2,
                   scan_max: int = 8) -> bool:
        """切到目标会话：先试预估行号，不对就**逐行扫描**直到标题匹配。

        为什么不用索引一把梭：侧栏顺序会因新消息/置顶变化，索引只是大概率，
        而点错行会把消息发进别的聊天（实测误发过）。所以宁可多花几秒扫描。
        """
        self.activate()           # 不激活的话 Qt 会忽略投递的点击
        self.dismiss_overlays()   # 先收起浮层，否则点不到侧栏
        tried = set()

        def try_row(i: int) -> bool:
            if i < 0 or i in tried:
                return False
            tried.add(i)
            self.click_session(i)
            time.sleep(0.9)
            if self.title_matches(name):
                return True
            if not self.current_title():
                self._close_panels()
            return False

        # 1) 先试预估行号和它的邻居
        for i in [index] + [index - d for d in range(1, span + 1)] +                  [index + d for d in range(1, span + 1)]:
            if try_row(i):
                return True
        # 2) 再按顺序扫一遍（会话少于 8 个，代价可接受）
        for i in range(scan_max):
            if try_row(i):
                return True
        self._log("扫描完毕仍没找到「%s」，当前标题「%s」" % (name, self.current_title()))
        return False

    def open_index_uia(self, index: int, name: str, verify=None,
                       scan_max: int = 6) -> bool:
        """点侧栏行切会话，用 verify() 回调（UIA 读标题）确认结果。

        比库的 open_chat 快一个数量级（1s 级 vs 18s），又不像截图 OCR 那样会读到旧帧。
        """
        verify = verify or (lambda: self.current_title())
        self.activate()
        tried = set()
        order = [index] + [i for i in range(scan_max)]
        for i in order:
            if i < 0 or i in tried:
                continue
            tried.add(i)
            self.click_session(i)
            time.sleep(0.55)
            cur = verify() or ""
            if cur and (name in cur or cur in name):
                return True
        return False

    def type_and_send(self, text: str) -> bool:
        """当前会话已确认，输入并发送。"""
        self.click_input()
        time.sleep(0.2)
        self.type_text(text)
        time.sleep(0.3)
        self.press_enter()
        return True

    def send_by_index(self, index: int, name: str, text: str) -> bool:
        if not self.open_index(index, name):
            return False
        return self.type_and_send(text)
